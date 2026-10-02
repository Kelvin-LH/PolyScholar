# SPDX-License-Identifier: AGPL-3.0-only
"""无文件书目与后加真实 PDF 的原生检查，无联网模型或引擎。

Native fileless bibliography and later real-PDF flow, without model or engine network calls.
"""
from pathlib import Path
import sys
import tempfile
import time
import threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QDialog
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService
from polyscholar.ui.searches import TYPES
from polyscholar.ui.creators import CreatorsDialog


def wait(predicate):
    deadline=time.monotonic()+15
    while not predicate() and time.monotonic()<deadline:
        QTest.qWait(10)
    assert predicate(),'Managed bibliographic operation did not finish'


def idle(window,*dialogs):
    wait(lambda:window.io_worker is None and all(not dialog._busy and dialog._pending_action is None for dialog in dialogs))


def select_root(window,identifier):
    for index in range(window.document_list.count()):
        if window.document_list.item(index).data(Qt.ItemDataRole.UserRole)['id']==identifier:
            window.document_list.setCurrentRow(index)
            return
    raise AssertionError('Expected visible bibliographic root')


def create_pdf(path,text):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((60,80),text)
        pdf.save(path)


def main():
    app=QApplication([])
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-bibliographic-ui-') as directory:
        root=Path(directory)
        service=LocalService(data_dir=root/'data')
        collection=service.create_collection('Fileless items')
        window=Window(service)
        try:
            window.resize(1024,700)
            window.show()
            app.processEvents()
            window.new_bibliographic()
            canceled=window.bibliographic_dialog
            canceled.fields['title'].setText('Canceled draft')
            canceled.close()
            assert not service.list_documents()
            window.collection_tree.setCurrentItem(window.collection_tree.topLevelItem(2))
            created=[]
            for index,(label,kind) in enumerate(TYPES):
                window.new_bibliographic()
                dialog=window.bibliographic_dialog
                dialog.item_type.setCurrentIndex(dialog.item_type.findData(kind))
                dialog.fields['title'].setText('Fileless '+kind)
                dialog.fields['year'].setText('2024')
                if kind=='article-journal':dialog.fields['doi'].setText('10.1234/fileless')
                if index==0:
                    with patch.object(service,'create_bibliographic_item',side_effect=ValueError('创建失败，草稿保持。')),patch.object(QMessageBox,'warning') as warning:
                        dialog.create()
                        idle(window,dialog)
                        assert warning.called and dialog.fields['title'].text()=='Fileless '+kind and not service.list_documents()
                dialog.create()
                idle(window,dialog)
                assert dialog.created is not None
                identifier=dialog.created['id']
                created.append(identifier)
                select_root(window,identifier)
                assert window.item_type.currentData()==kind and window.attachment_list.count()==0 and window.attachment_empty.isVisible()
                assert not window.read_button.isEnabled() and window.task_doc.count()==0 and not window.create_translation_button.isEnabled()
                assert window.evidence_doc.count()==0 and not window.parse_button.isEnabled() and not window.generate_summary_button.isEnabled()
                assert service.primary_pdf_id(identifier) is None and service.list_attachments(identifier)==[]
                assert not dialog.created['hasPdf'] and not dialog.created['hasAnyPdf'] and dialog.created['sha256'] is None
                creators = [{'role':'author','type':'organization','literal':'Synthetic Collective','family':'','given':''}]
                creators_dialog = CreatorsDialog(creators,window)
                with patch('polyscholar.ui.library.CreatorsDialog',return_value=creators_dialog),patch.object(creators_dialog,'exec',return_value=QDialog.DialogCode.Accepted):
                    window.edit_creators()
                window.notes.setPlainText('Editable fileless note '+kind)
                window.fields['tags'].setText('fileless, local')
                window.save_doc()
                assert service.store.document(identifier)['tags']==['fileless','local']
                assert service.store.document(identifier)['creators']==creators
                for item in window.citation_items.selectedItems():item.setSelected(False)
                for row in range(window.citation_items.count()):
                    if window.citation_items.item(row).data(Qt.ItemDataRole.UserRole)==identifier:window.citation_items.item(row).setSelected(True)
                for name in ('CSL-JSON','BibTeX','RIS'):
                    window.citation_format.setCurrentText(name)
                    assert 'Fileless '+kind in window.citation_preview.toPlainText()
                dialog.close()
            assert service.document_collections(created[0])==[collection['id']]
            window.search.setText('article-journal')
            assert window.document_list.count()==1
            window.clear_filters()
            assert len(service.search_fulltext('Fileless')['coverage'])==0
            # Candidate merge of fileless roots does not invent PDF identities or attachments.
            other=service.create_bibliographic_item({'title':'Second metadata source','itemType':'article-journal','doi':'10.1234/fileless','year':'2024','notes':'Retained second source metadata'})
            window.refresh()
            window.open_duplicates()
            candidates=window.duplicates_dialog
            idle(window,candidates)
            assert candidates.pairs.count()==1
            candidates.open_preview()
            merge=candidates.merge_dialog
            idle(window,merge)
            merge.master.setCurrentIndex(merge.master.findData(created[0]))
            idle(window,merge)
            assert merge._preview['counts']['pdfs']==0
            with patch('polyscholar.ui.duplicates.plain_question',return_value=True):
                merge.merge()
                idle(window,merge,candidates)
            assert service.list_attachments(created[0])==[] and service.primary_pdf_id(created[0]) is None
            assert service.list_merge_history(created[0]) and len(service.list_documents())==4
            merge.close()
            candidates.close()
            select_root(window,created[0])
            with patch('polyscholar.ui.trash.plain_question',return_value=True):
                window.delete_doc()
                wait(lambda:window.io_worker is None and window._trash_operation._closed)
            window.open_trash()
            trash=window.trash_dialog
            idle(window,trash)
            assert trash._preview['counts']['pdfs']==0
            trash.restore()
            idle(window,trash)
            trash.close()
            select_root(window,created[0])
            create_pdf(root/'later.pdf','Actual later PDF fileless evidence.')
            with patch.object(QFileDialog,'getOpenFileName',return_value=(str(root/'later.pdf'),'PDF')):
                window.add_attachment()
                idle(window)
            files=service.list_attachments(created[0])
            assert len(files)==1 and files[0]['documentId']!=created[0]
            file_id=files[0]['documentId']
            assert files[0]['isPrimary'] and service.primary_pdf_id(created[0])==file_id
            assert not service.store.document(created[0])['hasPdf'] and service.store.document(created[0])['hasAnyPdf']
            assert window.read_button.isEnabled() and window.task_doc.currentData()==file_id and window.evidence_doc.currentData()==file_id
            window.open_original()
            idle(window)
            wait(lambda:window.pdf_docs[0].pageCount()==1)
            assert window.reader_document['id']==file_id and window.reader_document['parentDocumentId']==created[0]
            window.parse_evidence()
            idle(window)
            assert window.evidence_blocks.count()==1 and 'Actual later PDF' in window.evidence_text.toPlainText()
            block=service.document_blocks(file_id)[0]
            service.save_claim(file_id,'Actual file identity evidence',[{'blockId':block['id'],'quote':block['text']}])
            assert service.list_claims(file_id)
            try:
                service.current_document_ir(created[0])
            except ValueError as error:
                assert '没有 PDF' in str(error)
            else:
                raise AssertionError('Fileless root must not own a PDF IR')
            # An explicit primary choice is a true file identity, never an automatic translated fallback.
            create_pdf(root/'translated.pdf','Existing real translated PDF.')
            window.attachment_role.setCurrentIndex(window.attachment_role.findData('translation'))
            with patch.object(QFileDialog,'getOpenFileName',return_value=(str(root/'translated.pdf'),'PDF')):
                window.add_attachment()
                idle(window)
            window.attachment_list.setCurrentRow(1)
            second_id=window.selected_attachment()['documentId']
            window.set_primary_pdf()
            idle(window)
            assert service.primary_pdf_id(created[0])==second_id
            window.open_original()
            idle(window)
            assert window.reader_document['id']==second_id
            service.trash_document(second_id)
            window.refresh()
            select_root(window,created[0])
            assert service.primary_pdf_id(created[0]) is None and not window.read_button.isEnabled()
            service.restore_document(second_id)
            window.refresh()
            select_root(window,created[0])
            assert service.primary_pdf_id(created[0])==second_id and window.read_button.isEnabled()
            # The legacy imported root remains its own real PDF identity.
            create_pdf(root/'legacy.pdf','Legacy actual PDF identity.')
            legacy=service.import_pdf(root/'legacy.pdf')
            window.refresh()
            select_root(window,legacy['id'])
            assert service.primary_pdf_id(legacy['id'])==legacy['id'] and window.read_button.isEnabled()
            window.open_original()
            idle(window)
            assert window.reader_document['id']==legacy['id']
            assert window.width()==1024 and window.stack.widget(0).horizontalScrollBar().maximum()==0
            # Close still waits for the managed create worker and preserves the committed item.
            window.new_bibliographic()
            pending=window.bibliographic_dialog
            pending.fields['title'].setText('Committed during close')
            started=threading.Event()
            release=threading.Event()
            real=service.create_bibliographic_item
            def delayed(*args,**kwargs):
                started.set()
                release.wait(5)
                return real(*args,**kwargs)
            with patch.object(service,'create_bibliographic_item',side_effect=delayed):
                pending.create()
                wait(started.is_set)
                window.close()
                assert window._closing and not window._closed and pending._closed
                release.set()
                wait(lambda:window._closed)
            assert not service._children and not pending.timer.isActive()
            service.close()
            service=LocalService(data_dir=root/'data')
            assert any(document['title']=='Committed during close' for document in service.list_documents())
        finally:
            window.close()
            service.close()
    print('Native fileless bibliography: four types, validation drafts, citation/search/merge/trash, true later PDF identity and managed close passed')


if __name__=='__main__':
    main()
