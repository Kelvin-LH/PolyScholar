# SPDX-License-Identifier: AGPL-3.0-only
"""Native personal desktop UI. No browser, webview or HTTP server."""
import sys
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
    QVBoxLayout, QLabel, QPushButton, QListWidget, QStackedWidget,
    QMessageBox, QScrollArea, QFrame)

STYLE = """
QWidget { background:#ffffff; color:#1c2532; font-size:14px; }
QWidget#sidebar { background:#f1f4f5; }
QLabel#brand { background:transparent; color:#23614f; font-size:17px; font-weight:600; }
QLabel#heading { font-size:21px; font-weight:600; }
QLabel#muted { color:#718096; font-size:12px; }
QPushButton { min-height:20px; padding:5px 14px; border:1px solid #d8dfe5; border-radius:4px; font-size:13px; background:#ffffff; }
QPushButton:hover { background:#edf4f1; border-color:#b9cfc5; }
QPushButton#primary { background:#23614f; color:white; border:0; font-weight:600; }
QPushButton#primary:hover { background:#1d5442; }
QPushButton:disabled { color:#99a4af; background:#f4f5f6; border-color:#e6eaed; }
QToolButton#disclosure { text-align:left; padding:7px; border:1px solid #d8dfe5; border-radius:4px; background:#f1f4f5; }
QToolButton#disclosure:checked { background:#edf4f1; }
QPushButton#scoreToggle { color:#23614f; padding:2px 6px; border:0; font-size:12px; }
/* Input total height stays >=40px: scripts/smoke_desktop.py asserts it at 1024x700. */
QLineEdit,QComboBox,QSpinBox { min-height:24px; padding:8px 10px; border:1px solid #d8dfe5; border-radius:4px; background:#ffffff; }
QTextEdit { padding:6px 8px; border:1px solid #d8dfe5; border-radius:4px; background:#ffffff; }
QComboBox::drop-down { border:0; width:24px; }
QTreeWidget,QListWidget#panel { border:1px solid #e4eae6; border-radius:6px; background:#ffffff; outline:0; }
QTreeWidget::item { padding:4px 6px; }
QTreeWidget::item:selected { color:#23614f; background:#e6f1ec; border-radius:4px; }
QTreeWidget::header { background:transparent; }
QListWidget { border:0; background:transparent; outline:0; }
QListWidget::item { padding:4px 6px; border-radius:4px; }
QListWidget::item:selected { color:white; background:#23614f; }
QListWidget#nav::item { padding:9px 12px; margin:1px 0; border-radius:6px; }
QListWidget#panel::item { padding:8px 10px; border-radius:0; border-bottom:1px solid #f1f4f5; }
QListWidget#panel::item:selected { border-radius:0; }
QTableWidget { border:0; gridline-color:#edf0f2; }
QHeaderView::section { background:#f2f4f6; padding:6px 8px; border:0; font-size:12px; }
QSplitter::handle { background:#e9eef0; }
QSplitter::handle:horizontal { width:1px; }
QSplitter::handle:vertical { height:1px; }
QScrollBar:vertical { background:transparent; width:9px; margin:2px; }
QScrollBar::handle:vertical { background:#ccd6d1; border-radius:4px; min-height:28px; }
QScrollBar::handle:vertical:hover { background:#b3c4bc; }
QScrollBar:horizontal { background:transparent; height:9px; margin:2px; }
QScrollBar::handle:horizontal { background:#ccd6d1; border-radius:4px; min-width:28px; }
QScrollBar::handle:horizontal:hover { background:#b3c4bc; }
QScrollBar::add-line,QScrollBar::sub-line { width:0; height:0; }
QScrollBar::add-page,QScrollBar::sub-page { background:transparent; }

QPushButton:focus,QLineEdit:focus,QComboBox:focus,QTextEdit:focus,QPlainTextEdit:focus { border:2px solid #23614f; }
QTabBar::tab { padding:8px; }
QTextBrowser,QPlainTextEdit { padding:6px; border:1px solid #d8dfe5; }
"""

from .ui.library import LibraryPage
from .ui.reader import ReaderPage
from .ui.tasks import TasksPage
from .ui.summary import SummaryPage
from .ui.citations import CitationsPage
from .ui.settings import SettingsPage
from .ui.workers import IOWorker, safe_error

class Window(LibraryPage, ReaderPage, TasksPage, SummaryPage, CitationsPage, SettingsPage, QMainWindow):
    job_changed = Signal(dict)
    def __init__(self, service):
        super().__init__(); self.service=service; self.docs=[]; self.jobs=[]; self.model_worker=None; self.io_worker=None; self._closing=False; self._closed=False
        self.setWindowTitle('PolyScholar · 研译'); self.resize(1440,960); self.setMinimumSize(960,700)
        root=QWidget(); row=QHBoxLayout(root); row.setContentsMargins(0,0,0,0); row.setSpacing(0)
        side=QWidget(); side.setObjectName('sidebar'); side.setFixedWidth(170); sl=QVBoxLayout(side);sl.setContentsMargins(12,22,10,14)
        brand=QLabel('PolyScholar 研译');brand.setObjectName('brand');sl.addWidget(brand);sl.addSpacing(24)
        self.nav=QListWidget();self.nav.setObjectName('nav');self.nav.setAccessibleName('主导航');self.nav.addItems(['文献库','双语阅读','翻译任务','证据摘要','设置']);sl.addWidget(self.nav)
        self.stack=QStackedWidget(); row.addWidget(side); row.addWidget(self.stack,1);self.setCentralWidget(root)
        self.library();self.reader();self.tasks();self.summary();self.citations();self.settings()
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex);self.nav.setCurrentRow(0)
        self.job_changed.connect(self.on_job_changed,Qt.ConnectionType.QueuedConnection);self._unsubscribe=self.service.subscribe_jobs(self.job_changed.emit)
        self.configure_shortcuts()
        self.refresh()

    def configure_shortcuts(self):
        # Qt maps standard shortcuts to native platform conventions.
        # 使用 Qt 标准快捷键，在各平台遵循本机按键约定。
        self.find_action = QAction("搜索文献", self)
        self.find_action.setShortcuts(QKeySequence.StandardKey.Find)
        self.find_action.triggered.connect(self.focus_library_search)
        self.addAction(self.find_action)
        self.open_action = QAction("导入本地 PDF", self)
        self.open_action.setShortcuts(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.import_pdf)
        self.addAction(self.open_action)
        self.navigation_actions = []
        for index in range(self.nav.count()):
            action = QAction(self.nav.item(index).text(), self)
            action.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            action.triggered.connect(lambda checked=False, index=index: self.nav.setCurrentRow(index))
            self.addAction(action)
            self.navigation_actions.append(action)

    def focus_library_search(self):
        self.nav.setCurrentRow(0)
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def page(self, title, subtitle, scroll=True):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName('heading')
        layout.addWidget(heading)
        description = QLabel(subtitle)
        description.setObjectName('muted')
        description.setWordWrap(True)
        layout.addWidget(description)
        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidget(widget)
            self.stack.addWidget(area)
        else:
            # Library panes scroll independently at small desktop sizes.
            # 小窗口下文献库三栏独立滚动，工具与列表始终可达。
            self.stack.addWidget(widget)
        return layout

    def button(self,text,callback,primary=False):
        b=QPushButton(text);b.clicked.connect(callback)
        if primary:b.setObjectName('primary')
        return b

    def guard(self,fn):
        try:return fn()
        except Exception as exc: QMessageBox.warning(self,'操作未完成',safe_error(exc));return None

    def refresh(self):
        self.docs=self.service.list_documents();self.refresh_organization();self.filter_docs()
        self.refresh_task_items()
        self.refresh_jobs();self.refresh_citation_items();self.refresh_summary_items()

    def on_job_changed(self,event):
        if not self._closing:
            self.refresh_jobs()
            # Completed HTML can make a fileless item readable without reselection.
            # HTML 完成后无需重新选择，无文件条目也立即可读。
            self.update_read_controls()

    def run_io(self,work,ready,message):
        if self.io_worker is not None or self._closing:return
        self.io_status.setText(message)
        for button in (self.import_button,self.read_button,self.reader_result,self.reader_source,self.verification_button):button.setEnabled(False)
        worker=IOWorker(work,self);self.io_worker=worker
        self.update_evidence_controls();self.update_attachment_controls();self.update_task_controls()
        worker.ready.connect(lambda value:self.guard(lambda:ready(value)) if not self._closing else None,Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(lambda message:QMessageBox.warning(self,'操作未完成',message) if not self._closing else None,Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self.io_finished,Qt.ConnectionType.QueuedConnection);worker.start()

    def io_finished(self):
        worker=self.io_worker;self.io_worker=None
        if worker:worker.deleteLater()
        self.io_status.setText('')
        for button in (self.import_button,self.read_button,self.reader_result,self.reader_source):button.setEnabled(True)
        self.update_evidence_controls();self.update_attachment_controls();self.update_task_controls()
        if self._closing:self.close()

    def closeEvent(self,event:QCloseEvent):
        if self._closed:event.accept();return
        active=self.io_worker is not None or (self.model_worker and self.model_worker.isRunning())
        if active:
            self.service.cancel_verifications()
            self._closing=True;self.setEnabled(False);self.io_status.setText('正在结束本地操作，请稍候…');event.ignore();return
        self._closing=True;self._unsubscribe();self.service.close();self._closed=True;event.accept()


def main():
    # 安装的 GUI 入口也要先分流冻结子进程，避免重复启动桌面。
    # Installed GUI entrypoints must dispatch frozen workers before creating the desktop.
    from multiprocessing import freeze_support
    freeze_support()
    # Maintainer checks use generated files before opening any desktop or user library.
    # 维护者检查先使用自生成文件，不打开桌面或用户文献库。
    if '--smoke-test-imports' in sys.argv:
        from .packaged_checks import run_local_import_checks
        try:
            run_local_import_checks()
        except Exception as error:
            print('PolyScholar local import checks failed: ' + type(error).__name__,
                  file=sys.stderr, flush=True)
            return 1
        return 0
    from .service import LocalService
    app=QApplication(sys.argv);app.setStyleSheet(STYLE)
    if '--smoke-test' in sys.argv:
        import tempfile
        with tempfile.TemporaryDirectory(prefix='polyscholar-smoke-') as tmp:
            service=LocalService(data_dir=tmp)
            w=Window(service);w.show()
            def finish():
                print('PolyScholar native GUI smoke passed',flush=True)
                w.close();app.quit()
            QTimer.singleShot(250,finish)
            return app.exec()
    try:service=LocalService()
    except ValueError as error:
        QMessageBox.warning(None,'无法启动',str(error));return 1
    w=Window(service);w.show();sys.exit(app.exec())

if __name__ == '__main__':
    raise SystemExit(main())
