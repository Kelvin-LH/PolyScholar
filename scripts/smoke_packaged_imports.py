# SPDX-License-Identifier: AGPL-3.0-only
"""Source entry for the same offline frozen-runtime checks.

源码入口复用冻结运行环境的离线检查，不依赖测试夹具。
"""
import multiprocessing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polyscholar.packaged_checks import run_local_import_checks


if __name__ == '__main__':
    multiprocessing.freeze_support()
    run_local_import_checks()
