# SPDX-License-Identifier: AGPL-3.0-only
"""Maintainer-only embedded CPython builder; end users never run pip or install Python."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tarfile
import tempfile
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.engines import VERSIONS, limited_environment

RELEASE = '20260929'
PYTHON_VERSION = '3.12.14'
# Pinned official GitHub release asset digests, fetched with gh api on 2026-10-02.
TARGETS = {
    ('Darwin', 'arm64'): ('aarch64-apple-darwin', 'de6b8f94fa765639b423ea353ab340669704c7186f96ee3cab389dcfde770c3c'),
    ('Darwin', 'x86_64'): ('x86_64-apple-darwin', 'f51ec8a7fa0ede129a5e2e942a2a453ba4830adaca2289da4449390e42443292'),
    ('Linux', 'aarch64'): ('aarch64-unknown-linux-gnu', '9c797cf657f6dced51d3e74eeabd7c1ca742d5bf10f66080a290e424ced8edaf'),
    ('Linux', 'x86_64'): ('x86_64-unknown-linux-gnu', '06c90b93f419b63371c18f20fed0558a1a901f6518c3c24f755077e048447e7f'),
    ('Windows', 'AMD64'): ('x86_64-pc-windows-msvc', '28728baf30b65e263f0b25c5a85be8226e7ab0d212fbadd6a8f0f796139fa804'),
    ('Windows', 'ARM64'): ('aarch64-pc-windows-msvc', '96b4c8cb02ad37d97141035530fa63cac3ff2690a9cfdfd343290c0c94227ea2'),
}

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()

def binary(root):
    return root / ('python.exe' if os.name == 'nt' else 'bin/python3')

def clean_environment():
    return limited_environment()

def unpack(archive, destination):
    # Validate members and link targets even on maintainer Python versions before tar filters.
    with tarfile.open(archive, 'r:gz') as tar:
        base = destination.resolve()
        for item in tar.getmembers():
            name = PurePosixPath(item.name)
            if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != 'python':
                raise ValueError('Invalid runtime archive member')
            if item.isdev() or item.isfifo(): raise ValueError('Special archive file rejected')
            if item.issym() or item.islnk():
                target = (destination / item.name).parent / item.linkname if item.issym() else destination / item.linkname
                if not target.resolve().is_relative_to(base):
                    raise ValueError('Runtime archive link escapes destination')
        tar.extractall(destination, filter='data')

def verify(runtime, engine, work):
    py = binary(runtime).absolute()
    env = clean_environment()
    # PATH deliberately excludes all host interpreters and user-site directories.
    env['PATH'] = str(runtime/'bin') if os.name != 'nt' else str(runtime)
    package, expected = VERSIONS[engine]
    module = 'babeldoc.main' if engine == 'babeldoc' else 'pdf2zh.pdf2zh'
    code = ('import importlib.metadata,sys; '
            'assert sys.version_info[:2]==(3,12); '
            'assert importlib.metadata.version(' + repr(package) + ')=='+repr(expected)+'; '
            'print(sys.version.split()[0])')
    subprocess.run([str(py), '-I', '-c', code], env=env, cwd=work, check=True, timeout=30)
    subprocess.run([str(py), '-I', '-m', module, '--version'], env=env, cwd=work,
                   check=True, timeout=90)
    subprocess.run([str(py), '-I', '-m', 'pip', 'check'], env=env, cwd=work,
                   check=True, timeout=60)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'.runtime')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    target = TARGETS.get((platform.system(), platform.machine()))
    if not target: parser.error('Unsupported build host; build native target artifacts in target CI')
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=True)
    triple, expected_hash = target
    asset = 'cpython-'+PYTHON_VERSION+'+'+RELEASE+'-'+triple+'-install_only.tar.gz'
    url = 'https://github.com/astral-sh/python-build-standalone/releases/download/'+RELEASE+'/'+asset.replace('+', '%2B')
    with tempfile.TemporaryDirectory(prefix='polyscholar-runtime-') as temporary:
        work = Path(temporary)
        if not args.verify_only:
            archive = work / 'runtime.tar.gz'
            print('Downloading pinned standalone CPython:', asset, flush=True)
            # OS curl uses the host trust store; old maintainer Python builds may lack a CA bundle.
            # Never disable TLS verification. The downloaded bytes are independently hash-checked.
            curl = shutil.which('curl')
            if curl:
                subprocess.run([curl, '--fail', '--location', '--silent', '--show-error',
                                '--proto', '=https', '--tlsv1.2', '--output', str(archive), url],
                               check=True, timeout=300)
            else:
                with urlopen(url, timeout=120) as response, archive.open('wb') as stream:
                    shutil.copyfileobj(response, stream)
            if digest(archive) != expected_hash: raise ValueError('Runtime SHA-256 mismatch')
            unpack(archive, work/'unpacked')
        for engine in ('babeldoc', 'pdfmathtranslate'):
            runtime = output / engine
            if not args.verify_only:
                if runtime.exists(): raise ValueError('Runtime directory already exists; use --verify-only or a new output')
                shutil.copytree(work/'unpacked/python', runtime, symlinks=True)
                print('Installing isolated engine:', engine, flush=True)
                subprocess.run([str(binary(runtime)), '-I', '-m', 'pip', '--isolated', 'install',
                                '--disable-pip-version-check', '-r', str(ROOT/'integrations'/(engine+'-requirements.txt'))],
                               env=clean_environment(), check=True, timeout=1200)
                installed = subprocess.check_output([str(binary(runtime)), '-I', '-m', 'pip', 'freeze', '--all'],
                                                    env=clean_environment(), text=True, timeout=30)
                (runtime/'installed-packages.txt').write_text(installed, encoding='utf-8')
                # Move the tree before validation: proves execution does not depend on original prefix.
                moved = output / (engine+'-relocation-check')
                runtime.rename(moved)
                try: verify(moved, engine, work)
                finally: moved.rename(runtime)
            verify(runtime, engine, work)
        manifest = dict(python_version=PYTHON_VERSION, release=RELEASE, target=triple,
                        asset=asset, asset_sha256=expected_hash, engines=['babeldoc','pdfmathtranslate'],
                        relocation_verified=True, network_translation_tested=False,
                        python_external_install_required=False)
        (output/'runtime-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        print('Embedded runtime ready:', output, flush=True)

if __name__ == '__main__': main()
