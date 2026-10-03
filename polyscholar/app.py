# SPDX-License-Identifier: AGPL-3.0-only
"""Native personal desktop UI. No browser, webview or HTTP server."""
import sys
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
    QVBoxLayout, QLabel, QPushButton, QListWidget, QStackedWidget,
    QMessageBox, QScrollArea, QFrame)

STYLE = """
QWidget { background:#ffffff; color:#1c2532; font-size:14px; }
QWidget#sidebar { background:#f1f4f5; }
QLabel#brand { background:transparent; color:#23614f; font-size:19px; font-weight:600; }
QLabel#heading { font-size:21px; font-weight:600; }
QLabel#muted { color:#718096; font-size:12px; }
QPushButton { min-height:20px; padding:5px 14px; border:1px solid #d8dfe5; border-radius:4px; font-size:13px; background:#ffffff; }
QPushButton:hover { background:#edf4f1; border-color:#b9cfc5; }
QPushButton#primary { background:#23614f; color:white; border:0; font-weight:600; }
QPushButton#primary:hover { background:#1d5442; }
QPushButton:disabled { color:#99a4af; background:#f4f5f6; border-color:#e6eaed; }
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
"""

from .ui.library import LibraryPage
from .ui.reader import ReaderPage
from .ui.tasks import TasksPage
from .ui.citations import CitationsPage
from .ui.settings import SettingsPage
from .ui.workers import IOWorker, safe_error

class Window(LibraryPage, ReaderPage, TasksPage, CitationsPage, SettingsPage, QMainWindow):
    job_changed = Signal(dict)
    def __init__(self, service):
        super().__init__(); self.service=service; self.docs=[]; self.jobs=[]; self.model_worker=None; self.io_worker=None; self._closing=False; self._closed=False
        self.setWindowTitle('PolyScholar · 研译'); self.resize(1440,960); self.setMinimumSize(1024,700)
        root=QWidget(); row=QHBoxLayout(root); row.setContentsMargins(0,0,0,0); row.setSpacing(0)
        side=QWidget(); side.setObjectName('sidebar'); side.setFixedWidth(200); sl=QVBoxLayout(side);sl.setContentsMargins(18,22,16,14)
        brand=QLabel('PolyScholar 研译');brand.setObjectName('brand');sl.addWidget(brand);sl.addSpacing(14)
        self.nav=QListWidget();self.nav.setObjectName('nav');self.nav.addItems(['文献库','原文阅读','翻译任务','引用导出','设置']);sl.addWidget(self.nav)
        self.stack=QStackedWidget(); row.addWidget(side); row.addWidget(self.stack,1);self.setCentralWidget(root)
        self.library();self.reader();self.tasks();self.citations();self.settings()
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex);self.nav.setCurrentRow(0)
        self.job_changed.connect(self.on_job_changed,Qt.ConnectionType.QueuedConnection);self._unsubscribe=self.service.subscribe_jobs(self.job_changed.emit)
        self.clock=QTimer(self);self.clock.timeout.connect(self.tick_elapsed);self.clock.start(1000)
        self.refresh()

    def tick_elapsed(self):
        """Keep the running-task elapsed and progress columns alive between worker events."""
        if self._closing or not hasattr(self,'job_table') or not hasattr(self,'elapsed_label'):return
        running=[job for job in self.jobs if job.get('state')=='running']
        if running:
            # Translation writes progress into the job record; poll it each tick.
            try:self.jobs=self.service.list_jobs()
            except Exception:return
        for row,job in enumerate(self.jobs):
            if job.get('state')!='running' or row >= self.job_table.rowCount():continue
            item=self.job_table.item(row,3)
            if item is None:continue
            progress=job.get('progress')
            if progress and isinstance(progress,dict):
                item.setText(f"{progress.get('current',0):g} / {progress.get('total',0):g} 段")
            else:
                item.setText(self.elapsed_label(job))

    def page(self,title,subtitle,scroll=True):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(22,16,22,14);l.setSpacing(10)
        h=QLabel(title);h.setObjectName('heading');l.addWidget(h)
        sub=QLabel(subtitle);sub.setObjectName('muted');sub.setWordWrap(True);l.addWidget(sub)
        if not scroll:
            self.stack.addWidget(w);return l
        area=QScrollArea();area.setWidgetResizable(True);area.setFrameShape(QFrame.Shape.NoFrame);area.setWidget(w);self.stack.addWidget(area);return l

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
        self.refresh_jobs();self.refresh_citation_items()

    def on_job_changed(self,event):
        if not self._closing:self.refresh_jobs()

    def run_io(self,work,ready,message):
        if self.io_worker is not None or self._closing:return
        self.io_status.setText(message)
        for button in (self.import_button,self.read_button,self.reader_source):button.setEnabled(False)
        worker=IOWorker(work,self);self.io_worker=worker
        self.update_evidence_controls();self.update_attachment_controls()
        worker.ready.connect(lambda value:self.guard(lambda:ready(value)) if not self._closing else None,Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(lambda message:QMessageBox.warning(self,'操作未完成',message) if not self._closing else None,Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self.io_finished,Qt.ConnectionType.QueuedConnection);worker.start()

    def io_finished(self):
        worker=self.io_worker;self.io_worker=None
        if worker:worker.deleteLater()
        self.io_status.setText('')
        for button in (self.import_button,self.read_button,self.reader_source):button.setEnabled(True)
        self.update_evidence_controls();self.update_attachment_controls()
        if self._closing:self.close()

    def closeEvent(self,event:QCloseEvent):
        if self._closed:event.accept();return
        active=self.io_worker is not None or (self.model_worker and self.model_worker.isRunning())
        if active:
            self._closing=True;self.setEnabled(False);self.io_status.setText('正在结束本地操作，请稍候…');event.ignore();return
        self._closing=True;self._unsubscribe();self.service.close();self._closed=True;event.accept()


def main():
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
