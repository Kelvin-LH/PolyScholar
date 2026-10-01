# SPDX-License-Identifier: AGPL-3.0-only
from PySide6.QtWidgets import QLabel, QComboBox, QSplitter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView

class ReaderPage:
    def reader(self):
        l=self.page('双语阅读','原文与翻译结果并排阅读；段落对齐和图中文字注释仍在研发。')
        self.pdf_title=QLabel('请在文献库中选择文献并点击“阅读文献”。');l.addWidget(self.pdf_title)
        self.reader_document=None;self.reader_result=QComboBox();self.reader_result.addItem('仅阅读原文',None);self.reader_result.currentIndexChanged.connect(self.load_reader);l.addWidget(self.reader_result)
        split=QSplitter();self.pdf_docs=[];self.pdf_views=[]
        for _ in range(2):
            pdf=QPdfDocument(self);view=QPdfView();view.setDocument(pdf);view.setPageMode(QPdfView.PageMode.MultiPage);view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            self.pdf_docs.append(pdf);self.pdf_views.append(view);split.addWidget(view)
        l.addWidget(split,1)

    def refresh_reader_choices(self):
        if not hasattr(self,'reader_result'):return
        previous=self.reader_result.currentData();self.reader_result.blockSignals(True);self.reader_result.clear()
        self.reader_result.addItem('仅阅读原文',None)
        if self.reader_document:
            for job in self.jobs:
                if job.get('documentId')!=self.reader_document['id'] or job.get('state')!='completed':continue
                for index,name in enumerate(job.get('artifacts',[])):
                    self.reader_result.addItem(f"任务 {job['id'][:8]} · {name}",(job['id'],index))
        selected=self.reader_result.findData(previous)
        if selected>=0:self.reader_result.setCurrentIndex(selected)
        self.reader_result.blockSignals(False)

    def open_original(self):
        if self.io_worker is not None:return
        document=self.selected()
        if not document:return
        if not self.reader_document or self.reader_document['id']!=document['id']:
            self.reader_document=document;self.refresh_reader_choices()
        self.load_reader()

    def load_reader(self,*args):
        if self.io_worker is not None or not self.reader_document:return
        document=self.reader_document.copy();artifact=self.reader_result.currentData()
        def work():
            source=self.service.read_pdf(document['id'])
            translated=self.service.read_artifact_pdf(*artifact) if artifact else None
            return source,translated
        def ready(result):
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice
            for pdf in self.pdf_docs:pdf.close()
            for buffer in getattr(self,'buffers',[]):buffer.deleteLater()
            self.buffers=[]
            for index,raw in enumerate(result):
                if raw is None:continue
                buffer=QBuffer(self);buffer.setData(QByteArray(raw));buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                self.buffers.append(buffer);self.pdf_docs[index].load(buffer)
            self.pdf_title.setText(document['title']);self.nav.setCurrentRow(1)
        self.run_io(work,ready,'正在载入阅读文件…')

