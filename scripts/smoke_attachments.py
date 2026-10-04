# SPDX-License-Identifier: AGPL-3.0-only
"""Native managed-PDF attachment flow using generated local PDFs; no API calls."""
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from polyscholar.app import Window
from polyscholar.service import LocalService


def wait_until(predicate):
    deadline=time.monotonic()+15
    while not predicate() and time.monotonic()<deadline:QTest.qWait(10)
    assert predicate(),'Native attachment operation did not finish'


def create_pdf(path,text,pages=1):
    with pymupdf.open() as pdf:
        for _ in range(pages):pdf.new_page().insert_text((60,80),text)
        pdf.save(path)


def main():
    app=QApplication([])
    with tempfile.TemporaryDirectory(prefix='polyscholar-attachments-ui-') as directory:
        root=Path(directory);original=root/'original.pdf';supplement=root/'supplement.pdf';translation=root/'translated.pdf'
        create_pdf(original,'Original parent evidence.');create_pdf(supplement,'Supplement child evidence.',2);create_pdf(translation,'Existing translated PDF.',3)
        service=LocalService(data_dir=root/'data');parent=service.import_pdf(original);window=Window(service)
        try:
            window.show();app.processEvents();window.document_list.setCurrentRow(0)
            assert window.attachment_list.count()==1 and not window.attachment_delete_button.isEnabled()
            with patch.object(QFileDialog,'getOpenFileName',return_value=(str(supplement),'PDF')):
                window.add_attachment();assert not window.attachment_add_button.isEnabled();wait_until(lambda:window.io_worker is None)
            window.attachment_role.setCurrentIndex(window.attachment_role.findData('translation'))
            with patch.object(QFileDialog,'getOpenFileName',return_value=(str(translation),'PDF')):
                window.add_attachment();wait_until(lambda:window.io_worker is None)
            assert len(service.list_documents())==1 and window.document_list.count()==1
            rows=service.list_attachments(parent['id']);child=next(row for row in rows if row['role']=='supplement');translated=next(row for row in rows if row['role']=='translation')
            assert window.attachment_list.count()==3 and window.task_doc.count()==3 and window.evidence_doc.count()==3
            window.attachment_list.setCurrentRow(1);assert window.selected_attachment()['documentId']==child['id']
            window.refresh();assert window.selected_attachment()['documentId']==child['id']
            window.read_attachment();wait_until(lambda:window.io_worker is None and window.pdf_docs[0].pageCount()==2)
            assert window.reader_source.count()==3 and window.reader_source.currentData()==child['id']
            assert window.reader_result.count()==1
            window.jobs=[{'id':'parent-job','documentId':parent['id'],'state':'completed','artifacts':['parent.pdf']},{'id':'child-job','documentId':child['id'],'state':'completed','artifacts':['child.pdf']}]
            window.refresh_reader_choices();assert window.reader_result.count()==2 and window.reader_result.itemData(1)==('child-job',0)
            window.reader_source.setCurrentIndex(window.reader_source.findData(translated['id']));wait_until(lambda:window.io_worker is None and window.pdf_docs[0].pageCount()==3)
            assert window.reader_document['id']==translated['id']
            window.evidence_doc.setCurrentIndex(window.evidence_doc.findData(child['id']));window.parse_evidence();wait_until(lambda:window.io_worker is None)
            assert window.evidence_blocks.count()==2 and 'Supplement child evidence.' in window.evidence_text.toPlainText()
            assert service.current_document_ir(parent['id']) is None
            window.task_doc.setCurrentIndex(window.task_doc.findData(child['id']))
            with patch.object(service,'start_translation') as start:
                window.start_job();assert start.call_args.args[0]==child['id']
            assert window.citation_items.count()==1
            window.attachment_list.setCurrentRow(1)
            with patch('polyscholar.ui.trash.plain_question',return_value=False):
                window.remove_attachment();wait_until(lambda:window.io_worker is None and window._trash_operation._closed)
            assert len(service.list_attachments(parent['id']))==3
            with patch('polyscholar.ui.trash.plain_question',return_value=True) as confirmation:
                window.remove_attachment();wait_until(lambda:window.io_worker is None and window._trash_operation._closed)
                assert '回收站' in confirmation.call_args.args[2]
            assert len(service.list_attachments(parent['id']))==2 and supplement.is_file()
            assert window.task_doc.findData(child['id'])==-1 and window.evidence_doc.findData(child['id'])==-1
            assert len(service.list_documents())==1 and window.attachment_list.item(0).data(Qt.ItemDataRole.UserRole)['role']=='original'
            window.attachment_list.setCurrentRow(0);assert not window.attachment_delete_button.isEnabled()
            # 移至回收站时清空阅读中的家族 PDF。
            # Trashing a root clears the open family PDF and selectors.
            with patch('polyscholar.ui.trash.plain_question',return_value=True):
                window.delete_doc();wait_until(lambda:window.io_worker is None and window._trash_operation._closed)
            assert not service.list_documents() and window.reader_document is None and window.reader_source.count()==0
        finally:
            window.close();service.close()
    print('Native managed attachments: async PDF import, one library entry, child reading/IR/task identity, confirmation and removal passed')


if __name__=='__main__':main()
