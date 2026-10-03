# SPDX-License-Identifier: AGPL-3.0-only
"""arXiv import dialog: paste links, preview fetched metadata, import checked entries."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QMessageBox)
from .workers import IOWorker

class ArxivImportDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window_ref=window;self.entries=[];self.worker=None
        self.setWindowTitle('arXiv 导入');self.resize(720,560)
        layout=QVBoxLayout(self);layout.setSpacing(8)
        hint=QLabel('每行一个 arXiv 链接或编号（如 https://arxiv.org/abs/2312.04567 或 2312.04567）。'
                    '获取元数据与下载正文只向 arXiv 发送论文编号，不发送本机文献。')
        hint.setObjectName('muted');hint.setWordWrap(True);layout.addWidget(hint)
        self.input=QPlainTextEdit();self.input.setPlaceholderText('https://arxiv.org/abs/2312.04567\n2312.04567v2\ncs/0301012')
        layout.addWidget(self.input,1)
        fetch_row=QHBoxLayout();fetch_row.setSpacing(8)
        self.fetch_button=QPushButton('获取元数据');self.fetch_button.clicked.connect(self.start_fetch);fetch_row.addWidget(self.fetch_button)
        self.status=QLabel('');self.status.setObjectName('muted');self.status.setWordWrap(True);fetch_row.addWidget(self.status,1)
        layout.addLayout(fetch_row)
        self.preview=QListWidget();self.preview.setVisible(False);layout.addWidget(self.preview,2)
        buttons=QHBoxLayout();buttons.setSpacing(8)
        self.import_button=QPushButton('导入所选并下载正文');self.import_button.setObjectName('primary');self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.start_import);buttons.addWidget(self.import_button,1)
        self.cancel_button=QPushButton('关闭');self.cancel_button.clicked.connect(self.reject);buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def _busy(self,working):
        self.fetch_button.setEnabled(not working);self.import_button.setEnabled(not working and bool(self.entries))
        self.input.setEnabled(not working);self.cancel_button.setEnabled(not working)

    def start_fetch(self):
        text=self.input.toPlainText().strip()
        if not text:self.status.setText('请先粘贴 arXiv 链接或编号。');return
        if self.worker is not None or self.window_ref._closing or self.window_ref.io_worker is not None:return
        self.fetch_button.setEnabled(False);self.status.setText('正在向 arXiv 查询元数据…')
        self.worker=IOWorker(lambda:self.window_ref.service.arxiv_lookup(text),self)
        self.worker.ready.connect(self.fetched);self.worker.failed.connect(self.failed);self.worker.finished.connect(self.cleanup)
        self.worker.start()

    def fetched(self,entries):
        self.entries=entries;self.preview.clear();self.preview.setVisible(True)
        for entry in entries:
            authors=('；'.join(entry['authors'][:4])+(' 等' if len(entry['authors'])>4 else '')) or '作者未知'
            item=QListWidgetItem(f"{entry['title']}\n{authors} · {entry['date'] or '日期未知'} · {entry['identifier']}")
            item.setFlags(item.flags()|Qt.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole,entry);item.setToolTip(entry['abstract'][:800])
            self.preview.addItem(item)
        self.status.setText(f'获取到 {len(entries)} 篇，已全部勾选；取消勾选不需要的条目后导入。')
        self.import_button.setEnabled(bool(entries))

    def failed(self,message):
        # Restore buttons so a transient network failure never dead-ends the dialog.
        self._busy(False);self.status.setText(message)

    def cleanup(self):
        self.worker=None

    def start_import(self):
        checked=[item.data(Qt.ItemDataRole.UserRole) for item in self.preview_items()
                 if item.checkState()==Qt.CheckState.Checked]
        if not checked:self.status.setText('请先勾选要导入的条目。');return
        if self.worker is not None or self.window_ref._closing or self.window_ref.io_worker is not None:return
        self._busy(True);self.status.setText(f'正在下载 {len(checked)} 篇 PDF 并导入…')
        collection=self.window_ref.current_collection()
        self.worker=IOWorker(lambda:self.window_ref.service.arxiv_import(checked,collection),self)
        self.worker.ready.connect(self.imported);self.worker.failed.connect(self.failed);self.worker.finished.connect(self.cleanup)
        self.worker.start()

    def preview_items(self):
        return [self.preview.item(index) for index in range(self.preview.count())]

    def imported(self,result):
        self._busy(False)
        imported=result.get('imported',[]);errors=result.get('errors',[])
        self.window_ref.refresh()
        if not errors:
            QMessageBox.information(self,'arXiv 导入完成',f'成功导入 {len(imported)} 篇。')
            self.accept();return
        summary=f'成功导入 {len(imported)} 篇，失败 {len(errors)} 篇：\n'+'\n'.join(dict.fromkeys(errors))
        self.status.setText(f'成功 {len(imported)} 篇，失败 {len(errors)} 篇；可再次点击导入重试（已入库的条目会自动去重）。')
        QMessageBox.warning(self,'arXiv 导入部分完成',summary)
