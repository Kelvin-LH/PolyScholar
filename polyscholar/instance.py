# SPDX-License-Identifier: AGPL-3.0-only
"""An OS-held lifetime lock; a stale lock file does not mean a live owner."""
import os
from pathlib import Path


class LibraryBusy(ValueError):
    """另一个进程持有库生命周期锁;CLI 以独立退出码区分这种失败。"""


class LibraryLock:
    def __init__(self, root):
        self.fd = None
        path = Path(root) / '.instance.lock'
        if path.is_symlink():
            raise ValueError('本地实例锁文件无效。')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b'0')
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise LibraryBusy('此文献库已在运行，请切换到已打开的 PolyScholar。') from None
        self.fd = fd

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)

    def __del__(self):
        self.close()
