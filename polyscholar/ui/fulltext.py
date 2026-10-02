# SPDX-License-Identifier: AGPL-3.0-only
"""本地 PDF 全文对话框，复用桌面的 IO 生命周期。

Local PDF full-text dialog sharing the desktop's managed IO lifetime.
"""
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QTextEdit, QSplitter, QMessageBox)


class FullTextDialog(QDialog):
    def __init__(self, window, criteria, scope):
        super().__init__(window)
        self.window=window
        # 调用者提供打开时的范围快照，后续库筛选不改变本次检索。
        # The caller snapshots scope at opening; later library filters do not change it.
        self.criteria=criteria
        self._closed=False
        self._busy=False
        self._pending_block=None
        self.setWindowTitle('本地全文检索')
        self.resize(900,650)
        layout=QVBoxLayout(self)
        label=QLabel(scope+'。检索已提取的本地 PDF 文本；扫描图片需 OCR，当前不会自动执行。')
        label.setWordWrap(True)
        layout.addWidget(label)
        row=QHBoxLayout()
        self.query=QLineEdit()
        self.query.setPlaceholderText('输入原文关键词，支持短字及中文')
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query,1)
        self.search_button=QPushButton('检索')
        self.search_button.clicked.connect(self.search)
        row.addWidget(self.search_button)
        layout.addLayout(row)
        self.status=QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        split=QSplitter()
        self.results=QListWidget()
        self.results.currentItemChanged.connect(self.show_result)
        split.addWidget(self.results)
        self.excerpt=QTextEdit()
        self.excerpt.setReadOnly(True)
        split.addWidget(self.excerpt)
        layout.addWidget(split,1)
        self.locate_button=QPushButton('定位选中结果')
        self.locate_button.clicked.connect(self.locate)
        layout.addWidget(self.locate_button)
        layout.addWidget(QLabel('当前范围内的 PDF 文本索引'))
        self.coverage=QListWidget()
        self.coverage.setMaximumHeight(160)
        self.coverage.currentItemChanged.connect(self.update_controls)
        layout.addWidget(self.coverage)
        self.parse_button=QPushButton('本地解析 / 重建选中 PDF')
        self.parse_button.clicked.connect(self.parse_selected)
        layout.addWidget(self.parse_button)
        controls=QHBoxLayout()
        self.rebuild_button=QPushButton('用已有文本重建索引')
        self.rebuild_button.clicked.connect(self.rebuild_selected)
        controls.addWidget(self.rebuild_button)
        self.clear_button=QPushButton('清除选中 PDF 索引')
        self.clear_button.clicked.connect(self.clear_selected)
        controls.addWidget(self.clear_button)
        layout.addLayout(controls)
        self.window.installEventFilter(self)
        self.timer=QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_controls)
        self.timer.start()
        self.update_controls()

    def update_controls(self,*args):
        busy=self._busy or self.window.io_worker is not None or self.window._closing or self._closed
        self.search_button.setEnabled(not busy)
        self.query.setEnabled(not busy)
        self.parse_button.setEnabled(not busy and self.coverage.currentItem() is not None)
        self.locate_button.setEnabled(not busy and self.results.currentItem() is not None)
        self.rebuild_button.setEnabled(not busy and self.coverage.currentItem() is not None)
        self.clear_button.setEnabled(not busy and self.coverage.currentItem() is not None)
        self.results.setEnabled(not busy)
        self.coverage.setEnabled(not busy)

    def run(self, work, ready, message):
        if self._closed or self.window._closing or self.window.io_worker is not None:
            return
        self._busy=True
        self.status.setText(message)
        self.update_controls()
        def result(value):
            # 隐藏后忽略结果，旧回调不会修改重新打开的对话框。
            # Ignore late results after hiding; old callbacks cannot affect a reopened dialog.
            if not self._closed:
                ready(value)
        # 主窗口拥有线程和服务关闭顺序，避免对话框启动第二套生命周期。
        # The window owns threads and shutdown ordering; the dialog adds no parallel lifetime.
        self.window.run_io(work,result,message)
        worker=self.window.io_worker
        if worker:
            worker.failed.connect(self.failed,Qt.ConnectionType.QueuedConnection)
            worker.finished.connect(self.io_finished,Qt.ConnectionType.QueuedConnection)
        else:
            self._busy=False
            self.update_controls()

    def failed(self,message):
        if not self._closed:
            self.status.setText(message)

    def io_finished(self):
        self._busy=False
        self.update_controls()
        # Window 的 finished 槽先清空共享 worker，随后阅读器才可启动加载。
        # Window clears the shared worker first, allowing the reader to start its own IO.
        block=self._pending_block
        self._pending_block=None
        if block is not None and not self._closed and not self.window._closing:
            self.window.open_evidence(block)

    def search(self):
        text=self.query.text().strip()
        self.run(
            lambda:self.window.service.search_fulltext(text,**self.criteria),
            self.show_search,
            '正在检索本地 PDF 文本…',
        )

    def show_search(self,result):
        previous=self.coverage.currentItem()
        previous_id=previous.data(Qt.ItemDataRole.UserRole)['documentId'] if previous else None
        self.results.clear()
        self.coverage.clear()
        self.excerpt.clear()
        for row in result['items']:
            label=f"{row.get('title',row.get('documentTitle',row.get('filename','PDF')))} · 第 {row.get('pageNumber',row.get('page'))} 页 · {row.get('snippet','')}"
            self.results.addItem(label)
            item=self.results.item(self.results.count()-1)
            item.setData(Qt.ItemDataRole.UserRole,row)
            item.setToolTip(label)
        names={'indexed':'已索引','no_text':'无可提取文本','unparsed':'尚未解析','parse_failed':'解析失败','cleared':'索引已清除'}
        for row in result['coverage']:
            state=names.get(row['status'],row['status'])
            if row.get('lastParseStatus')=='failed' and row['status']!='parse_failed':
                state+='；最近解析失败，保留旧索引'
            label=f"{row.get('title',row.get('documentTitle',row.get('filename','PDF')))} · {state} · {row.get('blockCount',0)} 个文本块"
            self.coverage.addItem(label)
            item=self.coverage.item(self.coverage.count()-1)
            item.setData(Qt.ItemDataRole.UserRole,row)
            item.setToolTip(label)
            if row['documentId']==previous_id:
                self.coverage.setCurrentItem(item)
        self.status.setText(f"共 {result['total']} 个匹配文本块。"+('仅显示前部结果，请缩小查询范围。' if result['truncated'] else '')+f"范围内 {len(result['coverage'])} 个 PDF。")
        if self.results.count():
            self.results.setCurrentRow(0)
        if self.coverage.count() and self.coverage.currentRow()<0:
            self.coverage.setCurrentRow(0)

    def show_result(self,*args):
        item=self.results.currentItem()
        self.excerpt.setPlainText('命中摘录（完整原文请定位 PDF）：\n'+item.data(Qt.ItemDataRole.UserRole).get('snippet','') if item else '')
        self.update_controls()

    def parse_selected(self):
        item=self.coverage.currentItem()
        if not item:
            return
        identifier=item.data(Qt.ItemDataRole.UserRole)['documentId']
        text=self.query.text().strip()
        def work():
            try:
                self.window.service.parse_document(identifier)
            except ValueError:
                return False,self.window.service.search_fulltext(text,**self.criteria)
            return True,self.window.service.search_fulltext(text,**self.criteria)
        def ready(result):
            success,response=result
            self.show_search(response)
            if not success:
                self.status.setText('选中 PDF 解析失败。请检查文件；覆盖状态已刷新。')
        self.run(work,ready,'正在本地解析选中 PDF…')

    def locate(self):
        item=self.results.currentItem()
        if not item:
            return
        hit=item.data(Qt.ItemDataRole.UserRole)
        def work():
            # 命中只能定位当前版本；历史 IR 保留用于证据，不代表结果仍有效。
            # Hits must target the current revision; retained historical IR does not validate them.
            revision=self.window.service.current_document_ir(hit['documentId'])
            if revision is None or revision['id']!=hit['revisionId']:
                raise ValueError('该结果的文本索引已变化，请重新检索。')
            block=next(
                (block for block in self.window.service.document_blocks(hit['documentId'])
                 if block['id']==hit['blockId']),
                None,
            )
            if block is None or block['revisionId']!=hit['revisionId']:
                raise ValueError('该结果已失效，请重新检索。')
            return block
        self.run(
            work,
            lambda block:setattr(self,'_pending_block',block),
            '正在核对结果来源…',
        )

    def index_selected(self,clear=False):
        item=self.coverage.currentItem()
        if not item:
            return
        identifier=item.data(Qt.ItemDataRole.UserRole)['documentId']
        text=self.query.text().strip()
        if clear and QMessageBox.question(self,'清除 PDF 索引','清除该 PDF 的检索索引？文献、提取文本与证据笔记会保留，可用已有文本重建。')!=QMessageBox.StandardButton.Yes:
            return
        def work():
            # 清理检索索引不删除提取文本、文献或证据笔记。
            # Removing an index preserves extracted text, documents and evidence notes.
            if clear:
                self.window.service.clear_fulltext_index(identifier)
            else:
                self.window.service.rebuild_fulltext_index(identifier)
            return self.window.service.search_fulltext(text,**self.criteria)
        self.run(work,self.show_search,'正在更新本地文本索引…')

    def rebuild_selected(self):
        self.index_selected()

    def clear_selected(self):
        self.index_selected(True)

    def eventFilter(self,watched,event):
        if watched is self.window and event.type()==QEvent.Type.Close:
            self.close()
        return super().eventFilter(watched,event)

    def closeEvent(self,event):
        # 关闭仅隐藏，主窗口继续管理线程；保留对象供已排队回调安全结束。
        # Closing hides the dialog; the window owns IO and queued callbacks can finish safely.
        self._closed=True
        self._pending_block=None
        self.timer.stop()
        event.accept()
