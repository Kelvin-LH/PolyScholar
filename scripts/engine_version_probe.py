# SPDX-License-Identifier: AGPL-3.0-only
"""Execute the actual version CLI with disposable configuration discovery.

以临时配置路径执行真实版本 CLI，不读取维护者原有的配置或翻译缓存。
"""
import asyncio
import os
from pathlib import Path
import runpy
import socket
import sys
from unittest.mock import patch


MODULES = {'babeldoc': 'babeldoc.main', 'pdfmathtranslate': 'pdf2zh.pdf2zh'}


def main():
    sys.dont_write_bytecode = True
    if len(sys.argv) != 3 or sys.argv[1] not in MODULES:
        raise ValueError('Invalid version fixture invocation')
    module = MODULES[sys.argv[1]]
    home = Path(sys.argv[2]).absolute()
    home.mkdir(parents=True, exist_ok=False)
    expanduser = os.path.expanduser

    def isolated_expanduser(path):
        text = os.fsdecode(path)
        if text == '~' or text.startswith(('~/', '~\\')):
            value = str(home) + text[1:]
            return os.fsencode(value) if isinstance(path, bytes) else value
        if text.startswith('~'):
            raise RuntimeError('Foreign home discovery is disabled')
        return expanduser(path)

    def audit(event, args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'socket.bind'):
            raise RuntimeError('Network is disabled in the version fixture')

    def refuse_passive_bind(*args, **kwargs):
        # Some imports probe IPv6 with bind; fail the capability probe before IO.
        # 部分模块导入时以 bind 探测 IPv6；在系统调用前拒绝此能力探测。
        raise OSError('Passive bind is disabled in the version fixture')

    # Windows creates its stdlib event-loop wakeup pair with local TCP. Build
    # only that native loop before denying IO; no upstream code runs here.
    # Windows 标准库以本地 TCP 创建事件循环唤醒管道；仅提前构造原生循环，
    # 此时尚未加载上游。真实 asyncio.run 领取一次，不能放行后续网络调用。
    loop = asyncio.new_event_loop()
    claimed = False

    def version_event_loop():
        nonlocal claimed
        if claimed or loop.is_closed():
            raise RuntimeError('The version event loop can only be used once')
        claimed = True
        return loop

    try:
        sys.addaudithook(audit)
        # Patch discovery only in this disposable process; keep the real OS home env.
        # 仅在夹具进程内替换路径发现，不改变 HOME/USERPROFILE 环境变量。
        with patch.object(Path, 'home', return_value=home), \
                patch('os.path.expanduser', side_effect=isolated_expanduser), \
                patch.object(socket.socket, 'bind', side_effect=refuse_passive_bind), \
                patch.object(asyncio.events, 'new_event_loop', side_effect=version_event_loop), \
                patch.object(sys, 'argv', [module, '--version']):
            runpy.run_module(module, run_name='__main__', alter_sys=True)
    finally:
        # Runner normally closes the loop; parser exits may never claim it.
        # Runner 正常负责关闭；参数解析提前退出时仍须释放未领取的循环。
        if not loop.is_closed():
            loop.close()


if __name__ == '__main__':
    main()
