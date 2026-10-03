# SPDX-License-Identifier: AGPL-3.0-only
from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QComboBox, QStackedWidget, QMessageBox
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView

class ReaderPage:
    def reader(self):
        l=self.page('原文阅读','阅读原始 PDF;全文检索定位在本页工作。译文在翻译任务页完成后从浏览器打开。')
        self.pdf_title=QLabel('请在文献库中选择文献并点击“阅读文献”。');l.addWidget(self.pdf_title)
        toolbar=QHBoxLayout();toolbar.setSpacing(6)
        self.reader_source=QComboBox();self.reader_source.currentIndexChanged.connect(self.select_reader_source);toolbar.addWidget(self.reader_source,1)
        self.open_translation_button=self.button('打开译文',self.open_translation);self.open_translation_button.setToolTip('在浏览器打开本文最新的中文译文');toolbar.addWidget(self.open_translation_button)
        l.addLayout(toolbar)
        # Single full-width pane: an empty-state hint replaces the dead gray viewport.
        self.reader_stack=QStackedWidget()
        self.empty_hint=QLabel('尚未打开文献。\n在文献库导入 PDF 后点击“阅读文献”,即可在此阅读;\n全文检索与证据摘要会自动定位到本页对应位置。')
        self.empty_hint.setObjectName('muted');self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter);self.empty_hint.setWordWrap(True)
        self.pdf_document=QPdfDocument(self);self.pdf_view=QPdfView();self.pdf_view.setDocument(self.pdf_document)
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage);self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.reader_stack.addWidget(self.empty_hint);self.reader_stack.addWidget(self.pdf_view)
        self.reader_stack.setCurrentWidget(self.empty_hint);l.addWidget(self.reader_stack,1)
        self._evidence_timer=QTimer(self);self._evidence_timer.setSingleShot(True);self._evidence_timer.timeout.connect(self.locate_loaded_evidence)
        self.pdf_document.statusChanged.connect(lambda status:self._evidence_timer.start(0))

    def refresh_reader_choices(self):
        if not hasattr(self,'reader_source'):return
        self.refresh_reader_sources()

    def clear_reader(self):
        self.reader_document=None;self._reader_evidence=None
        self.pdf_document.close()
        self.reader_source.clear()
        self.reader_stack.setCurrentWidget(self.empty_hint)
        self.pdf_title.setText('请在文献库中选择文献并点击“阅读文献”。')

    def refresh_reader_sources(self):
        if not hasattr(self,'reader_source'):return
        self.reader_source.blockSignals(True);self.reader_source.clear()
        if getattr(self,'reader_document',None):
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
        self.refresh_reader_sources()
        self.load_reader()

    def open_original(self):
        document=self.selected()
        if document:self.open_document(document['id'])

    def open_translation(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        document=getattr(self,'reader_document',None)
        if not document or self.io_worker is not None or self._closing:return
        try:
            path=self.service.open_translation(document['id'])
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except ValueError as error:
            QMessageBox.information(self,'打开译文',str(error))

    def open_evidence(self,block):
        if self.io_worker is not None or self._closing:return
        def open_block():
            self.reader_document=self.service.store.document(block['documentId'])
            self.refresh_reader_sources()
            self.load_reader(evidence=block)
        self.guard(open_block)

    def locate_loaded_evidence(self,*args):
        block=self._reader_evidence
        pdf=self.pdf_document
        if not block or pdf.status()!=QPdfDocument.Status.Ready:return
        page=block['pageNumber']-1
        if not 0<=page<pdf.pageCount():return
        size=pdf.pagePointSize(page);bbox=block['bbox']
        # Parser boxes use visible rotated/cropped page coordinates normalized to 0..1.
        # Qt PDF navigation uses points in this same visible page, with a top-left origin.
        center=QPointF((bbox[0]+bbox[2])*0.5*size.width(),(bbox[1]+bbox[3])*0.5*size.height())
        self.pdf_view.pageNavigator().jump(page,center,0)
        self._reader_evidence=None

    def load_reader(self,*args,evidence=None):
        if self.io_worker is not None or not self.reader_document:return
        self._reader_evidence=evidence
        self.pdf_view.setPageMode(QPdfView.PageMode.SinglePage if evidence else QPdfView.PageMode.MultiPage)
        document=self.reader_document.copy()
        def work():
            return self.service.read_pdf(document['id'])
        def ready(raw):
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice
            self.pdf_document.close()
            if getattr(self,'buffer',None) is not None:self.buffer.deleteLater()
            self.buffer=QBuffer(self);self.buffer.setData(QByteArray(raw));self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            self.pdf_document.load(self.buffer)
            self.reader_stack.setCurrentWidget(self.pdf_view)
            self.pdf_title.setText(document['title']);self.nav.setCurrentRow(1)
            self._evidence_timer.start(0)
        self.run_io(work,ready,'正在载入阅读文件…')
