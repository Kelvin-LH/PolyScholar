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
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

if __package__:
    from .managed_process import ProcessCleanupError, WindowsProcess, start_process
else:
    from managed_process import ProcessCleanupError, WindowsProcess, start_process

VERSIONS = {'babeldoc': ('babeldoc', '0.6.4'), 'pdfmathtranslate': ('pdf2zh', '1.9.11')}

class EngineError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def pdf_header(path):
    with path.open('rb') as stream:
        return stream.read(5) == b'%PDF-'

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


def validate(req, *, output_ready=False):
    if req.engine not in VERSIONS:
        raise ValueError('Unsupported engine')
    if not req.python.is_file() or not req.source.is_file():
        raise ValueError('Python executable and source PDF must exist')
    if req.source.suffix.lower() != '.pdf' or not pdf_header(req.source):
        raise ValueError('Input must be a PDF')
    if output_ready:
        # 仅可信子入口使用；普通任务仍必须创建全新目录。
        # Only the trusted child accepts its parent's already-created directory.
        if not req.output.is_dir() or req.output.is_symlink():
            raise ValueError('Managed output directory is unavailable')
    elif req.output.exists() or req.output.is_symlink():
        raise ValueError('Use a new output directory to prevent overwriting')
    parsed = urlsplit(req.endpoint)
    _ = parsed.port  # Reject malformed and out-of-range ports in every entry point.
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


def command(req):
    # 所有任务参数走 stdin；命令行只有可信脚本和解释器。
    # Only the trusted launcher and interpreter appear in the OS command line.
    entry = Path(__file__).resolve().with_name('engine_entry.py')
    return [str(req.python.absolute()), '-I', str(entry)]


def request_input(req, api_key):
    # 共用 worker 协议；序列化结果仅写入匿名管道，不保存配置文件。
    # Reuse the worker protocol and deliver only through an anonymous pipe.
    if __package__:
        from .job_worker import MAX_INPUT_BYTES, parse_request
    else:
        from job_worker import MAX_INPUT_BYTES, parse_request
    body = dict(
        protocol_version=1, job_id='upstream', engine=req.engine,
        python=str(req.python.absolute()), source=str(req.source.absolute()),
        output=str(req.output.absolute()), endpoint=req.endpoint, model=req.model,
        api_key=api_key, source_language=req.source_language,
        target_language=req.target_language, pages=req.pages, timeout=req.timeout,
        allow_document_upload=req.allow_document_upload,
        allow_asset_download=req.allow_asset_download,
    )
    data = json.dumps(body, ensure_ascii=True).encode('utf-8') + b'\n'
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('Engine request exceeds the protocol limit')
    parse_request(data)
    return data


class _InputDelivery:
    """Keep a non-reading child from blocking the adapter's timeout.

    子进程不读取 stdin 时，管道写入不能阻断父进程的超时和终止。
    """

    def __init__(self, stream, data):
        self.failed = False
        self._thread = threading.Thread(
            target=self._write, args=(stream, data), daemon=True,
            name='polyscholar-engine-input',
        )

    def _write(self, stream, data):
        try:
            stream.write(data)
            stream.flush()
        except (OSError, ValueError):
            self.failed = True
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                self.failed = True

    def start(self):
        self._thread.start()

    def finish(self):
        # Call only after process-tree termination; a live reader can hold the pipe.
        # 先确认进程树终止，再等待写入线程回收，避免活读者持有管道。
        if self._thread.ident is None:
            return
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise ProcessCleanupError('Engine input delivery did not stop')


def limited_environment():
    # Do not copy API credentials or provider config from the parent environment.
    # Keep the genuine OS home; never repurpose HOME or CODEX_HOME.
    allowed = ('PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP',
               'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
               # OS configuration/cache discovery and native subprocess resolution.
               'APPDATA', 'LOCALAPPDATA', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT',
               'XDG_CONFIG_HOME', 'XDG_CACHE_HOME')
    return {key: os.environ[key] for key in allowed if key in os.environ}


def check_version(req, env):
    package, expected = VERSIONS[req.engine]
    # Package strings are fixed internal constants, not user code.
    code = 'import importlib.metadata; print(importlib.metadata.version(' + repr(package) + '))'
    result = subprocess.run([str(req.python.absolute()), '-I', '-c', code], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, timeout=30)
    if result.returncode != 0 or result.stdout.strip() != expected:
        raise EngineError('engine_unavailable', 'Pinned engine version missing or mismatched')


def stop_process(proc):
    if isinstance(proc, WindowsProcess):
        proc.stop()
        return
    if os.name == 'posix':
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5)
        if proc.poll() is None:
            proc.kill()
    proc.wait(timeout=5)


def run(req, api_key):
    validate(req)
    payload = request_input(req, api_key)
    env = limited_environment()
    check_version(req, env)
    source_hash = file_hash(req.source)
    # POSIX modes restrict ordinary same-host users; Windows requires ACL review.
    temp = Path(tempfile.mkdtemp(prefix='polyscholar-job-'))
    cleanup_safe = True
    output_created = False
    succeeded = False
    try:
        launch_command = command(req)
        req.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        output_created = True
        proc = start_process(launch_command, cwd=temp, env=env, stdin=subprocess.PIPE,
                             start_new_session=(os.name == 'posix'))
        delivery = _InputDelivery(proc.stdin, payload)
        try:
            delivery.start()
            try:
                result = proc.wait(timeout=req.timeout)
            except subprocess.TimeoutExpired:
                raise EngineError('timeout', 'Engine timed out; remote requests may still be billed') from None
            except KeyboardInterrupt:
                raise EngineError('cancelled', 'Engine interrupted; remote requests may still be billed') from None
        finally:
            try:
                # 即使父进程已退出也先结束后代；它们可能仍持有请求管道。
                # Stop descendants even after parent exit; they may still hold stdin.
                try:
                    stop_process(proc)
                except (OSError, subprocess.TimeoutExpired):
                    raise ProcessCleanupError('Engine process-tree exit was not confirmed') from None
            finally:
                delivery.finish()
        if delivery.failed:
            raise EngineError('engine_failed', 'Engine request delivery failed')
        if result:
            raise EngineError('engine_failed', 'Engine failed; raw engine logs are suppressed to avoid exposing private data')
        if file_hash(req.source) != source_hash:
            raise EngineError('source_changed', 'Source PDF changed during execution')
        outputs = sorted(req.output.glob('*.pdf'))
        if not outputs or any(p.is_symlink() or not pdf_header(p) for p in outputs):
            raise EngineError('invalid_output', 'Engine did not produce valid PDF output headers')
        manifest = {'project': 'PolyScholar', 'engine': req.engine,
                'engine_version': VERSIONS[req.engine][1], 'model': req.model,
                'source_sha256': source_hash, 'source_language': req.source_language,
                'target_language': req.target_language, 'pages': req.pages or 'all',
                'license': 'AGPL-3.0-only', 'signed': False,
                'outputs': {p.name: file_hash(p) for p in outputs},
                'cost': None, 'usage': None, 'quality_verified': False}
        (req.output / 'polyscholar-export.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        succeeded = True
        return manifest
    except ProcessCleanupError:
        cleanup_safe = False
        raise EngineError('io_error', 'Engine process-tree exit was not confirmed') from None
    except OSError:
        raise EngineError('io_error', 'Local engine or file operation failed') from None
    finally:
        # 终止未确认时保留文件，避免活引擎继续读写已回收目录。
        # Preserve files when tree exit is unproven; recovery remains a separate gate.
        if cleanup_safe:
            try:
                if output_created and not succeeded:
                    if req.output.is_symlink():
                        req.output.unlink(missing_ok=True)
                    elif req.output.is_dir():
                        shutil.rmtree(req.output)
            finally:
                shutil.rmtree(temp)


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
