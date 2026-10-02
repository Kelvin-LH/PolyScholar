# SPDX-License-Identifier: AGPL-3.0-only
from PySide6.QtCore import QPointF, QTimer
from PySide6.QtWidgets import QLabel, QComboBox, QSplitter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView

class ReaderPage:
    def reader(self):
        l=self.page('双语阅读','原文与翻译结果并排阅读；段落对齐和图中文字注释仍在研发。')
        self.pdf_title=QLabel('请在文献库中选择文献并点击“阅读文献”。');l.addWidget(self.pdf_title)
        self._reader_evidence=None;self.reader_document=None;self.reader_source=QComboBox();self.reader_source.currentIndexChanged.connect(self.select_reader_source);l.addWidget(self.reader_source);self.reader_result=QComboBox();self.reader_result.addItem('仅阅读原文',None);self.reader_result.currentIndexChanged.connect(self.load_reader);l.addWidget(self.reader_result)
        split=QSplitter();self.pdf_docs=[];self.pdf_views=[]
        for _ in range(2):
            pdf=QPdfDocument(self);view=QPdfView();view.setDocument(pdf);view.setPageMode(QPdfView.PageMode.MultiPage);view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            self.pdf_docs.append(pdf);self.pdf_views.append(view);split.addWidget(view)
        self._evidence_timer=QTimer(self);self._evidence_timer.setSingleShot(True);self._evidence_timer.timeout.connect(self.locate_loaded_evidence)
        self.pdf_docs[0].statusChanged.connect(lambda status:self._evidence_timer.start(0))
        l.addWidget(split,1)

    def refresh_reader_choices(self):
        if not hasattr(self,'reader_result'):return
        self.refresh_reader_sources()
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

    def clear_reader(self):
        self.reader_document=None;self._reader_evidence=None
        for pdf in self.pdf_docs:pdf.close()
        for buffer in getattr(self,'buffers',[]):buffer.deleteLater()
        self.buffers=[];self.reader_source.clear();self.refresh_reader_choices()
        self.pdf_title.setText('请在文献库中选择文献并点击“阅读文献”。')

    def refresh_reader_sources(self):
        self.reader_source.blockSignals(True);self.reader_source.clear()
        if self.reader_document:
            identifier=self.reader_document['id']
            # A child PDF has its own content identity but belongs to one root library entry.
            parent=self.reader_document.get('parentDocumentId') or identifier
            if parent not in {document['id'] for document in self.docs}:
                self.reader_source.blockSignals(False);self.clear_reader();return
            rows=self.service.list_attachments(parent)
            if identifier not in {row['documentId'] for row in rows}:
                self.reader_source.blockSignals(False);self.clear_reader();return
            for row in rows:self.reader_source.addItem(self.attachment_label(row),row['documentId'])
            index=self.reader_source.findData(identifier)
            if index>=0:self.reader_source.setCurrentIndex(index)
        self.reader_source.blockSignals(False)

    def select_reader_source(self,*args):
        identifier=self.reader_source.currentData()
        if identifier and self.io_worker is None and not self._closing:self.open_document(identifier)

    def open_document(self,identifier):
        if self.io_worker is not None or self._closing:return
        self.reader_document=self.service.store.document(identifier)
        self.refresh_reader_choices()
        self.reader_result.blockSignals(True);self.reader_result.setCurrentIndex(0);self.reader_result.blockSignals(False)
        self.load_reader()

    def open_original(self):
        document=self.selected()
        if not document:return
        # 书目身份不等于 PDF；仅用实际主要文件身份打开阅读器。
        # Bibliographic identity is not a PDF; open only the actual primary file identity.
        identifier=self.service.primary_pdf_id(document['id'])
        if identifier:self.open_document(identifier)

    def open_evidence(self,block):
        if self.io_worker is not None or self._closing:return
        def open_block():
            self.reader_document=self.service.store.document(block['documentId'])
            self.refresh_reader_choices()
            self.reader_result.blockSignals(True);self.reader_result.setCurrentIndex(0);self.reader_result.blockSignals(False)
            self.load_reader(evidence=block)
        self.guard(open_block)

    def locate_loaded_evidence(self,*args):
        block=self._reader_evidence
        pdf=self.pdf_docs[0]
        if not block or pdf.status()!=QPdfDocument.Status.Ready:return
        page=block['pageNumber']-1
        if not 0<=page<pdf.pageCount():return
        size=pdf.pagePointSize(page);bbox=block['bbox']
        # Parser boxes use visible rotated/cropped page coordinates normalized to 0..1.
        # Qt PDF navigation uses points in this same visible page, with a top-left origin.
        center=QPointF((bbox[0]+bbox[2])*0.5*size.width(),(bbox[1]+bbox[3])*0.5*size.height())
        self.pdf_views[0].pageNavigator().jump(page,center,0)
        self._reader_evidence=None

    def load_reader(self,*args,evidence=None):
        if self.io_worker is not None or not self.reader_document:return
        self._reader_evidence=evidence
        self.pdf_views[0].setPageMode(QPdfView.PageMode.SinglePage if evidence else QPdfView.PageMode.MultiPage)
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
            self._evidence_timer.start(0)
        self.run_io(work,ready,'正在载入阅读文件…')

