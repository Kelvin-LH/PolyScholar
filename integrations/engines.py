# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Kelvin-LH and contributors.
"""CLI sidecar adapter. No GUI, token budget gateway, OCR review or hidden telemetry."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from urllib.parse import urlsplit

VERSIONS = {'babeldoc': ('babeldoc', '0.6.4'), 'pdfmathtranslate': ('pdf2zh', '1.9.11')}

@dataclass(frozen=True)
class Request:
    engine: str
    python: Path
    source: Path
    output: Path
    endpoint: str
    model: str
    source_language: str = 'en'
    target_language: str = 'zh'
    pages: str = ''
    timeout: int = 600
    allow_document_upload: bool = False
    allow_asset_download: bool = False


def validate(req):
    if req.engine not in VERSIONS:
        raise ValueError('Unsupported engine')
    if not req.python.is_file() or not req.source.is_file():
        raise ValueError('Python executable and source PDF must exist')
    if req.source.suffix.lower() != '.pdf' or req.source.read_bytes()[:5] != b'%PDF-':
        raise ValueError('Input must be a PDF')
    if req.output.exists():
        raise ValueError('Use a new output directory to prevent overwriting')
    parsed = urlsplit(req.endpoint)
    local = parsed.hostname in {'localhost', '127.0.0.1', '::1'}
    if (parsed.scheme != 'https' and not (local and parsed.scheme == 'http')) or not parsed.hostname:
        raise ValueError('Endpoint must use HTTPS or explicit loopback HTTP')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Endpoint must not contain credentials, query or fragment')
    if not req.model or any(ord(c) < 32 for c in req.model):
        raise ValueError('Model must be configured')
    for lang in (req.source_language, req.target_language):
        if not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})*', lang):
            raise ValueError('Invalid language identifier')
    if req.pages:
        for part in req.pages.split(','):
            if not re.fullmatch(r'[1-9][0-9]*(?:-[1-9][0-9]*)?', part):
                raise ValueError('Pages must be 1-based numbers or inclusive ranges')
            pair = [int(x) for x in part.split('-')]
            if len(pair) == 2 and pair[0] > pair[1]:
                raise ValueError('Page range is reversed')
    if not 1 <= req.timeout <= 86400:
        raise ValueError('Timeout must be between 1 and 86400 seconds')
    if not req.allow_document_upload or not req.allow_asset_download:
        raise ValueError('Explicit document processing and asset-download consent required')


def command(req, config):
    # Absolute paths and argv lists avoid shell evaluation and option injection.
    args = [str(req.python.absolute())]
    source, output = str(req.source.resolve()), str(req.output.resolve())
    if req.engine == 'babeldoc':
        args += ['-m', 'babeldoc.main', '--files', source, '--config', str(config),
                 '--openai', '--lang-in', req.source_language, '--lang-out', req.target_language,
                 '--output', output, '--watermark-output-mode', 'no_watermark']
    else:
        args += ['-m', 'pdf2zh.pdf2zh', source, '--service', 'openai', '--config', str(config),
                 '--lang-in', req.source_language, '--lang-out', req.target_language,
                 '--output', output, '--thread', '1']
    if req.pages:
        args += ['--pages', req.pages]
    return args


def private_config(req, directory, api_key):
    if not api_key:
        raise ValueError('API key required; use a dummy token for an explicit local gateway')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ('config.toml' if req.engine == 'babeldoc' else 'config.json')
    if req.engine == 'babeldoc':
        body = '[babeldoc]\n' + '\n'.join(
            key + ' = ' + json.dumps(value, ensure_ascii=False)
            for key, value in [('openai-model', req.model), ('openai-base-url', req.endpoint),
                               ('openai-api-key', api_key)]) + '\n'
    else:
        body = json.dumps({'translators': [{'name': 'openai', 'envs': {
            'OPENAI_BASE_URL': req.endpoint, 'OPENAI_API_KEY': api_key,
            'OPENAI_MODEL': req.model}}]})
    with path.open('x', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        stream.write(body)
    return path


def limited_environment():
    # Do not copy API credentials or provider config from the parent environment.
    # Keep the genuine OS home; never repurpose HOME or CODEX_HOME.
    allowed = ('PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP',
               'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
    return {key: os.environ[key] for key in allowed if key in os.environ}


def check_version(req, env):
    package, expected = VERSIONS[req.engine]
    # Package strings are fixed internal constants, not user code.
    code = 'import importlib.metadata; print(importlib.metadata.version(' + repr(package) + '))'
    result = subprocess.run([str(req.python.absolute()), '-c', code], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, timeout=30)
    if result.returncode != 0 or result.stdout.strip() != expected:
        raise RuntimeError('Pinned engine version missing or mismatched')


def stop_process(proc):
    if os.name == 'posix':
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if proc.poll() is None:
            proc.kill()
    proc.wait()


def run(req, api_key):
    validate(req)
    env = limited_environment()
    check_version(req, env)
    source_hash = hashlib.sha256(req.source.read_bytes()).hexdigest()
    req.output.mkdir(parents=True, exist_ok=False)
    # POSIX modes restrict ordinary same-host users; Windows requires ACL review.
    with tempfile.TemporaryDirectory(prefix='polyscholar-job-') as temporary:
        temp = Path(temporary)
        config = private_config(req, temp, api_key)
        try:
            proc = subprocess.Popen(command(req, config), cwd=temp, env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    start_new_session=(os.name == 'posix'))
        except OSError:
            raise RuntimeError('Engine could not start') from None
        try:
            result = proc.wait(timeout=req.timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            stop_process(proc)
            raise RuntimeError('Engine timed out or was interrupted; remote requests may still be billed') from None
        if result:
            raise RuntimeError('Engine failed; raw engine logs are suppressed to avoid exposing private data')
        if hashlib.sha256(req.source.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError('Source PDF changed during execution')
        outputs = sorted(req.output.glob('*.pdf'))
        if not outputs or any(p.is_symlink() or p.read_bytes()[:5] != b'%PDF-' for p in outputs):
            raise RuntimeError('Engine did not produce valid PDF output headers')
        manifest = {'project': 'PolyScholar', 'engine': req.engine,
                    'engine_version': VERSIONS[req.engine][1], 'model': req.model,
                    'source_sha256': source_hash, 'source_language': req.source_language,
                    'target_language': req.target_language, 'pages': req.pages or 'all',
                    'license': 'AGPL-3.0-only', 'signed': False,
                    'outputs': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs}}
        (req.output / 'polyscholar-export.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine', choices=VERSIONS, required=True)
    p.add_argument('--python', type=Path, required=True)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--endpoint', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--source-language', default='en')
    p.add_argument('--target-language', default='zh')
    p.add_argument('--pages', default='')
    p.add_argument('--timeout', type=int, default=600)
    p.add_argument('--allow-document-upload', action='store_true')
    p.add_argument('--allow-asset-download', action='store_true')
    a = p.parse_args()
    req = Request(a.engine, a.python, a.input, a.output, a.endpoint, a.model,
                  a.source_language, a.target_language, a.pages, a.timeout,
                  a.allow_document_upload, a.allow_asset_download)
    try:
        result = run(req, os.environ.get('POLYSCHOLAR_API_KEY', ''))
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired):
        p.exit(1, 'Translation failed or configuration invalid. No raw credentials/logs were printed.\n')
    print(json.dumps({'engine': result['engine'], 'outputs': list(result['outputs'])}))

if __name__ == '__main__':
    main()
