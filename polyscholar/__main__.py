# SPDX-License-Identifier: AGPL-3.0-only
"""启动桌面前分流受监督的子进程。 / Dispatch supervised children before desktop startup."""
from multiprocessing import freeze_support


if __name__ == '__main__':
    freeze_support()
    from .app import main
    raise SystemExit(main())
