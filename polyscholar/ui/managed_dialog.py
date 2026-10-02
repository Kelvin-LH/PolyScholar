# SPDX-License-Identifier: AGPL-3.0-only
"""原生对话框共用的主窗口 IO 与关闭管理。

Shared main-window IO and shutdown management for native dialogs.
"""
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QDialog


class ManagedIODialog(QDialog):
    """对话框显示结果；主窗口拥有 worker 和服务。

    Dialogs display results; the main window owns workers and the service.
    """
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._closed = False
        self._busy = False
        self._watched_worker = None
        self._pending_action = None
        self.window.installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll_shared_io)
        self.timer.start()

    def run(self, work, ready, message):
        if self._closed or self.window._closing or self.window.io_worker is not None:
            return
        self._busy = True
        self.show_status(message)
        self.update_controls()

        def result(value):
            # 隐藏后忽略晚回调，不修改随后打开的新对话框。
            # Ignore late callbacks after hiding; never change a subsequently opened dialog.
            if not self._closed:
                ready(value)

        self.window.run_io(work, result, message)
        worker = self.window.io_worker
        self._watched_worker = worker
        if worker:
            worker.failed.connect(self.failed, Qt.ConnectionType.QueuedConnection)
            worker.finished.connect(self._complete_io, Qt.ConnectionType.QueuedConnection)
        else:
            self._busy = False
            self.update_controls()

    def defer_after_io(self, action):
        """等主窗口释放共享 worker 后执行下一步。

        Run the next step after the window releases its shared worker.
        """
        self._pending_action = action
        self._poll_shared_io()

    def _complete_io(self):
        if not self._busy:
            return
        self._busy = False
        self._watched_worker = None
        self.update_controls()
        if not self._closed and not self.window._closing:
            self.managed_finished()
        self._run_pending()

    def _poll_shared_io(self):
        # 极快线程可能在附加 finished 槽前结束；主窗口指针是实际生命周期依据。
        # A fast thread may finish before our extra slot connects; use the window's owner state.
        if self._busy and self.window.io_worker is not self._watched_worker:
            self._complete_io()
        self.update_controls()
        self._run_pending()

    def _run_pending(self):
        if self._closed or self.window._closing:
            self._pending_action = None
            return
        if self._busy or self.window.io_worker is not None:
            return
        action = self._pending_action
        self._pending_action = None
        if action:
            action()

    def failed(self, message):
        if not self._closed:
            self.show_status(message)

    def show_status(self, message):
        if hasattr(self, 'status'):
            self.status.setText(message)

    def update_controls(self):
        """子类按共享 IO 状态调整自身控件。 / Subclasses gate their controls on shared IO."""

    def managed_finished(self):
        """共享 IO 释放后的可选挂钩。 / Optional hook after shared IO is released."""

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.Type.Close:
            self.close()
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        # 关闭仅隐藏，不销毁接收晚回调的对象；主窗口继续负责等待线程结束。
        # Hide without destroying queued callback receivers; the window still waits for its worker.
        self._closed = True
        self._pending_action = None
        self.timer.stop()
        event.accept()
