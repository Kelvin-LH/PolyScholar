# SPDX-License-Identifier: AGPL-3.0-only
"""Offline pinned entry/parser/client checks; no translation or API calls.

离线检查指定集成组件和固定版解析器、客户端，不等同真实翻译/API验收。
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FAILURE = 'Pinned offline engine configuration check failed'


def verify_engines(runtime: Path, integrations: Path):
    """Check these exact runtime and integration directories, without fallback.

    显式指定包内解释器和集成目录；组件缺失时不能回退源码目录。
    """
    runtime = Path(runtime).absolute()
    integrations = Path(integrations).absolute()
    helper = Path(__file__).with_name('engine_memory_probe.py').absolute()
    # Minimal bootstrap environment; the selected adapter owns version-check IO.
    # 夹具启动只保留系统必需环境，不继承提供商配置或凭据。
    environment = {
        name: os.environ[name]
        for name in ('HOME', 'USERPROFILE', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')
        if name in os.environ
    }
    environment['PATH'] = os.defpath
    try:
        with tempfile.TemporaryDirectory(prefix='polyscholar-engine-memory-check-') as temporary:
            for engine in ('babeldoc', 'pdfmathtranslate'):
                binary = runtime / engine / (
                    'python.exe' if sys.platform == 'win32' else 'bin/python3')
                result = subprocess.run(
                    [str(binary), '-I', str(helper), engine, temporary, str(integrations)],
                    cwd=temporary, env=environment,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=90,
                )
                expected = [b'Pinned engine memory configuration checks passed']
                if result.returncode != 0 or result.stdout.splitlines() != expected:
                    raise RuntimeError(FAILURE)
                print(engine + ' stdin entry, parser, memory configuration and client '
                      'checks passed; no translation or API calls executed', flush=True)
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        raise RuntimeError(FAILURE) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=ROOT / '.runtime')
    parser.add_argument('--integrations', type=Path, default=ROOT / 'integrations')
    args = parser.parse_args()
    verify_engines(args.runtime, args.integrations)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit(FAILURE)
