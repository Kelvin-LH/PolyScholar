# SPDX-License-Identifier: AGPL-3.0-only
"""Trusted Windows launch gate; never reads credentials or imports user code.

先等父进程完成 Job 绑定，再启动命令。Read only one release byte so the
unbuffered remainder of stdin belongs exclusively to the requested worker.
"""
import os
import subprocess
import sys

def read_command():
    if os.read(sys.stdin.fileno(), 1) != b'\x01':
        raise ValueError('Launch owner did not release the gate')
    command = sys.argv[1:]
    if not command:
        raise ValueError('Missing launch command')
    return command


def main():
    try:
        command = read_command()
        # 不预读业务请求；stdin contains the worker's original protocol bytes.
        child = subprocess.Popen(command, stdin=sys.stdin, close_fds=True)
        return child.wait()
    except (OSError, ValueError, TypeError):
        # No raw command, path, protocol content or exception crosses stdout.
        return 125


if __name__ == '__main__':
    raise SystemExit(main())
