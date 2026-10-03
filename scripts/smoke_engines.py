# SPDX-License-Identifier: AGPL-3.0-only
"""Offline pinned parser/client checks; no translation or network calls.

离线检查固定版真实解析器和客户端构造，不等同真实翻译/API验收。
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.engines import limited_environment
ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=ROOT / '.runtime')
    args = parser.parse_args()
    helper = Path(__file__).with_name('engine_memory_probe.py').absolute()
    with tempfile.TemporaryDirectory(prefix='polyscholar-engine-memory-check-') as temporary:
        for engine in ('babeldoc', 'pdfmathtranslate'):
            binary = args.runtime.absolute() / engine / (
                'python.exe' if sys.platform == 'win32' else 'bin/python3')
            result = subprocess.run(
                [str(binary), '-I', str(helper), engine, temporary],
                cwd=temporary, env=limited_environment(),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=90,
            )
            expected = [b'Pinned engine memory configuration checks passed']
            if result.returncode != 0 or result.stdout.splitlines() != expected:
                raise RuntimeError('Pinned offline engine configuration check failed')
            print(engine + ' parser, memory configuration and client checks passed; '
                  'no translation or network executed', flush=True)

if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Pinned offline engine configuration check failed')
