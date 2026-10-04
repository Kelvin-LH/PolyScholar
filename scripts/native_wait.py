# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded native observation for checks with real Python background workers.

原生检查通过 Qt 事件循环等待，不用连续 qWait 拖慢 Python 工作线程。
"""
import time


def wait_for(predicate, timeout=20, message='Native UI did not settle'):
    """Observe completion without changing the worker or its domain deadline.

    只观察真实完成条件；不强制刷新业务状态、不延长工作线程的业务时限。
    """
    from PySide6.QtCore import QEventLoop, QTimer

    if predicate():
        return
    deadline = time.monotonic() + timeout
    if timeout <= 0:
        raise AssertionError(message)
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(10)
    failures = []
    completed = False

    def check():
        nonlocal completed
        try:
            if time.monotonic() >= deadline:
                loop.quit()
                return
            if predicate():
                completed = time.monotonic() < deadline
                loop.quit()
        except BaseException as error:
            # Qt callbacks cannot propagate directly to this Python caller.
            # Qt 槽的异常不能直接传给调用方；退出后在原调用处重抛。
            failures.append(error)
            loop.quit()

    timer.timeout.connect(check)
    timer.start()
    try:
        loop.exec()
    finally:
        timer.stop()
        timer.timeout.disconnect(check)
    if failures:
        raise failures[0]
    # Never turn timeout/early exit into success with a new predicate call.
    # 不在退出后重新求值，从而将超时或提前退出改成成功。
    assert completed, message
