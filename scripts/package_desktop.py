# SPDX-License-Identifier: AGPL-3.0-only
"""Native Python desktop builder. Run using the maintainer desktop build environment."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.engines import limited_environment, stop_process
from smoke_engines import verify_engines


def verify_packaged_engines(artifact):
    """Inspect only the bundled integration scripts with their embedded Python.

    仅检查包内集成脚本与自带 Python，不允许退回源码树。
    """
    resources = (
        artifact / 'Contents/Resources/resources'
        if sys.platform == 'darwin' else artifact / '_internal/resources'
    )
    verify_engines(resources / 'runtime', resources / 'integrations')


def verify_desktop(artifact):
    """Check the actual executable, independent of the source working directory.

    使用实际包内可执行文件检查，不依赖源码工作目录或外部 Python。
    """
    if sys.platform == 'darwin':
        program = artifact / 'Contents/MacOS/PolyScholar'
    else:
        program = artifact / ('PolyScholar.exe' if os.name == 'nt' else 'PolyScholar')
    env = limited_environment()
    env['PATH'] = (str(Path(env.get('SYSTEMROOT', 'C:/Windows')) / 'System32')
                   if os.name == 'nt' else os.defpath)
    env['QT_QPA_PLATFORM'] = 'offscreen'
    with tempfile.TemporaryDirectory(prefix='polyscholar-package-check-') as work:
        for check in ('--smoke-test', '--smoke-test-imports'):
            process = subprocess.Popen(
                [str(program), check], cwd=work, env=env,
                start_new_session=(os.name == 'posix'),
            )
            try:
                result = process.wait(timeout=300)
                if result:
                    raise RuntimeError('Frozen desktop check failed: ' + check)
            finally:
                # Reuse process-tree shutdown for a timed-out or interrupted check.
                # 超时或中断时复用进程树收尾，不只结束验证父进程。
                if process.poll() is None:
                    stop_process(process)

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
    # A failed rebuild must not leave an earlier success record for this output.
    # 重建开始前使旧成功记录失效；后续任何失败都不能沿用上一轮验收。
    manifest_path = dist / 'package-manifest.json'
    manifest_path.unlink(missing_ok=True)
    # Stage a plain script entrypoint; no -m assumption and no relative-import ambiguity.
    work = ROOT/'.tools/python-build'
    work.mkdir(parents=True, exist_ok=True)
    entrypoint = work/'desktop_entry.py'
    # 子进程只执行解析 target，不能再次创建 Qt 窗口或打开用户库。
    # Spawn children run only their parser target, never another Qt window or library.
    entrypoint.write_text(
        "from multiprocessing import freeze_support\n"
        "if __name__ == '__main__':\n"
        "    freeze_support()\n"
        "    from polyscholar.app import main\n"
        "    raise SystemExit(main())\n", encoding='utf-8')
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
    # The isolated summary worker loads this controlled helper beside its script.
    shutil.copy2(ROOT/'polyscholar/summary_model.py', resources/'integrations/summary_model.py')
    for filename in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT/filename, resources/filename)
    # Execute the packaged engine trees from their actual final bundle prefix.
    subprocess.run([sys.executable, str(ROOT/'scripts/prepare_runtime.py'), '--verify-only',
                    '--output', str(target_runtime)], cwd=ROOT, check=True)
    verify_desktop(artifact)
    verify_packaged_engines(artifact)
    if args.codesign_identity:
        if sys.platform != 'darwin': parser.error('codesign identity is supported only on macOS')
        subprocess.run(['codesign', '--force', '--deep', '--options', 'runtime', '--sign',
                        args.codesign_identity, str(artifact)], check=True)
    manifest = dict(name='PolyScholar', architecture=platform.machine(), platform=platform.system(),
                    python_install_required=False, runtime_included=True,
                    runtime_final_prefix_verified=True, frozen_gui_startup_verified=True,
                    local_import_checks_verified=True,
                    embedded_engine_memory_configuration_verified=True,
                    signed=bool(args.codesign_identity), notarized=False)
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print('Packaged desktop application:', artifact, flush=True)

if __name__ == '__main__': main()
