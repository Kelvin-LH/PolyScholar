# SPDX-License-Identifier: AGPL-3.0-only
"""Native queued-signal lifecycle regression using controlled QObject fixtures.

真实 Qt 排队信号与受控交错夹具，不冒充真实后台线程或 Windows 故障定位。
This is not a real worker-thread or Windows CI root-cause acceptance test.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject, Signal, Qt, QCoreApplication
from PySide6.QtWidgets import QApplication, QWidget, QPushButton
from polyscholar.ui.managed_dialog import ManagedIODialog


class FixtureWorker(QObject):
    finished = Signal()
    failed = Signal(str)


class FixtureWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._closing = False
        self.io_worker = None
        self.workers = []
        self.dialog = None

    def run_io(self, work, ready, message):
        worker = FixtureWorker(self)
        self.io_worker = worker
        self.workers.append(worker)
        worker.finished.connect(self.release_owner,Qt.ConnectionType.QueuedConnection)

    def release_owner(self):
        self.io_worker = None
        # Force the allowed poll interleaving before the old dialog slot is delivered.
        # 显式安排旧对话框槽送达前的 poll 交错，而非宣称真实线程必然如此。
        self.dialog._poll_shared_io()


class FixtureDialog(ManagedIODialog):
    def __init__(self,window):
        super().__init__(window)
        self.cancel_control = QPushButton('Cancel',self)
        self.completions = 0
        self.control_updates = 0

    def update_controls(self):
        self.control_updates += 1
        self.cancel_control.setEnabled(self._busy and not self._closed)

    def managed_finished(self):
        self.completions += 1


def main():
    app = QApplication([])
    callback_errors = []
    original_hook = sys.excepthook
    sys.excepthook = lambda kind,value,tb:callback_errors.append(str(value))
    window = FixtureWindow()
    dialog = FixtureDialog(window)
    window.dialog = dialog
    try:
        dialog.run(lambda:None,lambda value:None,'old')
        old = window.workers[0]
        dialog.defer_after_io(lambda:dialog.run(lambda:None,lambda value:None,'new'))
        old.finished.emit()
        QCoreApplication.processEvents()
        new = window.workers[1]
        assert window.io_worker is new
        assert dialog._busy, 'A late old completion cleared the active operation'
        assert dialog._watched_worker is new
        assert dialog.cancel_control.isEnabled()
        assert dialog.completions == 1
        assert dialog._pending_action is None
        new.finished.emit()
        QCoreApplication.processEvents()
        assert window.io_worker is None
        assert not dialog._busy
        assert dialog._watched_worker is None
        assert not dialog.cancel_control.isEnabled()
        assert dialog.completions == 2

        dialog.run(lambda:None,lambda value:None,'closing')
        closing_worker = window.workers[2]
        pending_ran = []
        dialog.defer_after_io(lambda:pending_ran.append(True))
        dialog.close()
        updates_before = dialog.control_updates
        closing_worker.finished.emit()
        QCoreApplication.processEvents()
        assert dialog._closed
        assert window.io_worker is None  # The owner still completes its lifecycle. / 主窗口仍正常结束生命周期。
        assert dialog._pending_action is None
        assert not pending_ran
        assert dialog.completions == 2
        assert dialog.control_updates == updates_before
        assert not callback_errors, f'Qt callbacks failed: {callback_errors}'
    finally:
        dialog.close()
        window.close()
        app.processEvents()
        sys.excepthook = original_hook
    print('Managed dialog queued completion regression passed')


if __name__ == '__main__':
    main()
