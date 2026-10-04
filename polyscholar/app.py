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
QLabel#brand { background:transparent; color:#23614f; font-size:21px; font-weight:600; }
QLabel#heading { font-size:26px; font-weight:600; }
QLabel#muted { color:#62736d; }
QPushButton { min-height:24px; padding:7px 12px; border:1px solid #d8dfe5; border-radius:5px; }
QPushButton:hover { background:#edf4f1; }
QPushButton#primary { background:#23614f; color:white; border:0; }
QPushButton:disabled { color:#99a4af; background:#f4f5f6; }
QLineEdit,QComboBox,QSpinBox { min-height:24px; padding:8px; border:1px solid #d8dfe5; border-radius:4px; }
QTextEdit,QPlainTextEdit,QTextBrowser { padding:8px; border:1px solid #d8dfe5; border-radius:4px; }
QTreeWidget { border:1px solid #d8dfe5; outline:0; }
QTreeWidget::item { padding:6px 3px; }
QTreeWidget::item:selected { color:#23614f; background:#e6f1ec; }
QListWidget { border:0; background:transparent; outline:0; }
QListWidget::item { padding:7px; }
QListWidget#nav::item { padding:14px 12px; margin:3px 0; }
QListWidget::item:selected { color:white; background:#23614f; border-radius:5px; }
QPushButton:focus,QLineEdit:focus,QComboBox:focus,QTextEdit:focus,QPlainTextEdit:focus { border:2px solid #23614f; }
QTabBar::tab { padding:8px; }
QTabBar::tab:selected { color:#174c3c; background:#e6f1ec; }
QTableWidget { border:0; gridline-color:#edf0f2; }
QHeaderView::section { background:#f2f4f6; padding:9px 6px; border:0; }
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
        self.setWindowTitle('PolyScholar · 研译'); self.resize(1440,960); self.setMinimumSize(1024,700)
        root=QWidget(); row=QHBoxLayout(root); row.setContentsMargins(0,0,0,0); row.setSpacing(0)
        side=QWidget(); side.setObjectName('sidebar'); side.setFixedWidth(200); sl=QVBoxLayout(side);sl.setContentsMargins(16,24,16,20)
        brand=QLabel('PolyScholar 研译');brand.setObjectName('brand');sl.addWidget(brand);sl.addSpacing(24)
        self.nav=QListWidget();self.nav.setObjectName('nav');self.nav.setAccessibleName('主导航');self.nav.addItems(['文献库','双语阅读','翻译任务','证据摘要','引用导出','设置']);sl.addWidget(self.nav)
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
        layout.setContentsMargins(24, 22, 24, 20)
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
        for button in (self.import_button,self.read_button,self.reader_result,self.reader_source):button.setEnabled(False)
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
