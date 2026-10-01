# SPDX-License-Identifier: AGPL-3.0-only
"""Native Python desktop builder. Run using the maintainer desktop build environment."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=ROOT/'.tools/python-dist')
    parser.add_argument('--runtime', type=Path, default=ROOT/'.runtime')
    parser.add_argument('--codesign-identity', default='')
    args = parser.parse_args()
    dist = args.dist.absolute()
    runtime = args.runtime.absolute()
    if not (ROOT/'polyscholar/app.py').is_file(): parser.error('GUI source is missing')
    for engine in ('babeldoc', 'pdfmathtranslate'):
        relative = 'python.exe' if sys.platform == 'win32' else 'bin/python3'
        if not (runtime/engine/relative).is_file(): parser.error('Embedded runtime missing; run prepare_runtime.py first')
    if not (runtime/'runtime-manifest.json').is_file(): parser.error('Verified runtime manifest missing')
    # Stage a plain script entrypoint; no -m assumption and no relative-import ambiguity.
    work = ROOT/'.tools/python-build'
    work.mkdir(parents=True, exist_ok=True)
    entrypoint = work/'desktop_entry.py'
    entrypoint.write_text('from polyscholar.app import main\nraise SystemExit(main())\n', encoding='utf-8')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
               '--windowed', '--name', 'PolyScholar', '--paths', str(ROOT),
               '--distpath', str(dist), '--workpath', str(work/'work'), '--specpath', str(work),
               '--hidden-import', 'PySide6.QtPdf', '--hidden-import', 'PySide6.QtPdfWidgets',
               '--exclude-module', 'PySide6.QtWebEngineCore',
               '--exclude-module', 'PySide6.QtWebEngineWidgets',
               '--exclude-module', 'PySide6.QtWebEngineQuick', str(entrypoint)]
    subprocess.run(command, cwd=ROOT, check=True)
    artifact = dist/'PolyScholar.app' if sys.platform == 'darwin' else dist/'PolyScholar'
    resources = artifact/'Contents/Resources/resources' if sys.platform == 'darwin' else artifact/'_internal/resources'
    resources.mkdir(parents=True, exist_ok=True)
    target_runtime = resources/'runtime'
    if target_runtime.exists(): shutil.rmtree(target_runtime)
    shutil.copytree(runtime, target_runtime, symlinks=True)
    for directory in ('integrations', 'schemas', 'licenses'):
        target = resources/directory
        if target.exists(): shutil.rmtree(target)
        shutil.copytree(ROOT/directory, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for filename in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT/filename, resources/filename)
    # Execute the packaged engine trees from their actual final bundle prefix.
    subprocess.run([sys.executable, str(ROOT/'scripts/prepare_runtime.py'), '--verify-only',
                    '--output', str(target_runtime)], cwd=ROOT, check=True)
    if args.codesign_identity:
        if sys.platform != 'darwin': parser.error('codesign identity is supported only on macOS')
        subprocess.run(['codesign', '--force', '--deep', '--options', 'runtime', '--sign',
                        args.codesign_identity, str(artifact)], check=True)
    manifest = dict(name='PolyScholar', architecture=platform.machine(), platform=platform.system(),
                    python_install_required=False, runtime_included=True,
                    runtime_final_prefix_verified=True, signed=bool(args.codesign_identity), notarized=False)
    (dist/'package-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print('Packaged desktop application:', artifact, flush=True)

if __name__ == '__main__': main()
