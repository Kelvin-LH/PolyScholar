# SPDX-License-Identifier: AGPL-3.0-only
"""Exercise observation, queued completion, timeout and callback failure.

真实 Python 线程与 Qt 排队回调的检查工具回归，不代表产品或安装验收。
"""
import threading
import time
import sys
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication
from native_wait import wait_for


class Completion(QObject):
    finished = Signal()


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    hook_errors = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda *error: hook_errors.append(error)
    try:
        wait_for(lambda: True, timeout=0)
        completion = Completion()
        delivered = []
        completion.finished.connect(
            lambda: delivered.append(threading.get_ident()),
            Qt.ConnectionType.QueuedConnection,
        )
        release = threading.Event()

        def work():
            release.wait(5)
            # A real worker resumes repeatedly before queued GUI completion.
            # 实际后台线程多次恢复执行后，再排队通知 GUI 完成。
            for _ in range(20):
                time.sleep(0.001)
            completion.finished.emit()

        worker = threading.Thread(target=work)
        worker.start()
        try:
            release.set()
            wait_for(lambda: bool(delivered), timeout=5)
            assert delivered == [threading.get_ident()]
        finally:
            release.set()
            worker.join(5)
            assert not worker.is_alive()

        started = time.monotonic()
        try:
            wait_for(lambda: False, timeout=0.05, message='synthetic-timeout')
        except AssertionError as error:
            assert str(error) == 'synthetic-timeout'
        else:
            raise AssertionError('Unfinished observation was accepted')
        assert time.monotonic() - started >= 0.05

        late_calls = []

        def late_result():
            late_calls.append(True)
            return len(late_calls) >= 3

        try:
            wait_for(late_result, timeout=0.001, message='synthetic-late-result')
        except AssertionError as error:
            assert str(error) == 'synthetic-late-result'
        else:
            raise AssertionError('A fresh result overrode an expired observation')

        calls = []
        expected = ValueError('synthetic-callback-failure')

        def failing_predicate():
            calls.append(True)
            if len(calls) > 1:
                raise expected
            return False

        try:
            wait_for(failing_predicate, timeout=5)
        except ValueError as error:
            assert error is expected
        else:
            raise AssertionError('A callback failure was swallowed')
        assert len(calls) == 2
        # A failed wait must not leave an active timer or stale callback behind.
        # 失败后再等待，旧定时器和旧回调不能继续执行。
        quiet_started = time.monotonic()
        wait_for(lambda: time.monotonic() >= quiet_started + 0.1, timeout=5)
        assert len(calls) == 2

        exited = []

        def exit_application():
            exited.append(True)
            app.exit()

        QTimer.singleShot(0, exit_application)
        started = time.monotonic()
        try:
            wait_for(lambda: False, timeout=5, message='synthetic-early-exit')
        except AssertionError as error:
            assert str(error) == 'synthetic-early-exit'
        else:
            raise AssertionError('Early event-loop exit was accepted as completion')
        assert exited and time.monotonic() - started < 5
        assert not hook_errors
    finally:
        sys.excepthook = previous_hook
    print('Native observation worker, timeout and failure checks passed')


if __name__ == '__main__':
    main()
