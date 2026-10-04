# SPDX-License-Identifier: AGPL-3.0-only
"""真实生成 PDF 的人工合并原生检查；无模型或引擎联网。

Native manual-merge checks on generated PDFs; no model or engine network calls.
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
from PySide6.QtWidgets import QApplication, QMessageBox
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService
from polyscholar.ui.duplicates import safe_tooltip


def wait(predicate):
    deadline=time.monotonic()+15
    while not predicate() and time.monotonic()<deadline:
        QTest.qWait(10)
    assert predicate(),'Managed merge operation did not finish'


def idle(window,*dialogs):
    wait(lambda:window.io_worker is None and all(not dialog._busy and dialog._pending_action is None for dialog in dialogs))


def create(path,text):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((60,80),text)
        pdf.save(path)


def main():
    app=QApplication([])
    app.setStyleSheet(STYLE)
    assert safe_tooltip('<b>')=='<p>&lt;b&gt;</p>'
    with tempfile.TemporaryDirectory(prefix='polyscholar-merge-ui-') as directory:
        root=Path(directory)
        service=LocalService(data_dir=root/'data')
        roots=[]
        for name in ('source-a','source-b','manual-c'):
            create(root/f'{name}.pdf',f'Original {name} retained evidence.')
            document=service.import_pdf(root/f'{name}.pdf')
            service.update_document(document['id'],{'title':name,'doi':'10.1234/shared' if name!='manual-c' else '', 'year':'2024','date':'2024-03-01','notes':f'Complete notes from {name}.','tags':[name]})
            service.parse_document(document['id'])
            roots.append(document)
        master,source,manual=roots
        source_title='Source B complete title '+('long field '*90)+'END'
        source_creators=[{'role':'author','type':'organization','literal':'Research <b>Collective</b>','family':'','given':''},{'role':'editor','type':'person','literal':'','family':'Smith','given':'Alex'}]
        service.update_document(master['id'],{'creators':[{'role':'author','type':'person','family':'Chen','given':'Li','literal':''}]})
        service.update_document(source['id'],{'title':source_title,'creators':source_creators})
        service.update_document(manual['id'],{'creators':[{'role':'author','type':'person','literal':'Legacy Full Name','family':'','given':''}]})
        create(root/'supplement.pdf','Source B supplement retained.')
        supplement=service.import_attachment(source['id'],root/'supplement.pdf','supplement')
        create(root/'trashed.pdf','Independently trashed source child.')
        trashed=service.import_attachment(source['id'],root/'trashed.pdf','supplement')
        service.trash_document(trashed['documentId'])
        block=service.document_blocks(source['id'])[0]
        claim=service.save_claim(source['id'],'Retain original source evidence',[{'blockId':block['id'],'quote':block['text']}])
        collections=[]
        for index,document in enumerate(roots):
            collection=service.create_collection(f'Collection {index}')
            service.set_membership(document['id'],collection['id'])
            collections.append(collection['id'])
        # 合成已完成任务只证明数据关联保留，不是实际翻译质量验证。
        # A synthetic completed job verifies retained relations, not translation quality.
        job=service.store.new_job(source['id'],'babeldoc')
        output=Path(job['outputDir'])
        output.mkdir(parents=True)
        create(output/'synthetic.pdf','Synthetic artifact retained through merge.')
        job.update(state='completed',artifacts=['synthetic.pdf'])
        service.store.put_job(job)
        window=Window(service)
        try:
            window.resize(1024,700)
            window.show()
            app.processEvents()
            window.open_duplicates()
            candidates=window.duplicates_dialog
            idle(window,candidates)
            assert candidates.pairs.count()==1 and candidates.manual.count()==3
            candidates.open_preview()
            pair=candidates.merge_dialog
            idle(window,pair)
            with patch('polyscholar.ui.duplicates.plain_question',return_value=False):
                pair.merge()
            assert len(service.list_documents())==3
            pair.close()
            candidates.tabs.setCurrentIndex(1)
            for index in range(candidates.manual.count()):
                candidates.manual.item(index).setSelected(True)
            assert candidates.preview_button.isEnabled()
            candidates.open_preview()
            merge=candidates.merge_dialog
            idle(window,merge)
            merge.resize(1024,700)
            app.processEvents()
            assert merge.width()<=1024 and merge.height()<=700 and window.width()==1024
            merge.master.setCurrentIndex(merge.master.findData(master['id']))
            idle(window,merge)
            assert merge.history_button.isEnabled()
            QTest.mouseClick(merge.history_button,Qt.MouseButton.LeftButton)
            idle(window,merge.history_dialog)
            assert merge.history_dialog.identifier==master['id'] and merge.history_dialog.records.count()==0
            merge.history_dialog.close()
            merge.detail_field.setCurrentIndex(merge.detail_field.findData('title'))
            assert source_title in merge.details.toPlainText()
            merge.detail_field.setCurrentIndex(merge.detail_field.findData('creators'))
            assert 'Research <b>Collective</b>' in merge.details.toPlainText() and '编者 / 个人' in merge.details.toPlainText() and '姓：Smith' in merge.details.toPlainText()
            merge.field_sources['title'].setCurrentIndex(merge.field_sources['title'].findData(source['id']))
            idle(window,merge)
            merge.field_sources['creators'].setCurrentIndex(merge.field_sources['creators'].findData(source['id']))
            idle(window,merge)
            assert merge._draft_sources['title']==source['id'] and merge._draft_sources['creators']==source['id']
            tooltip=merge.field_sources['creators'].itemData(merge.field_sources['creators'].findData(source['id']),Qt.ItemDataRole.ToolTipRole)
            assert '&lt;b&gt;Collective&lt;/b&gt;' in tooltip and '<b>Collective</b>' not in tooltip
            # A changed source invalidates the snapshot atomically; UI drafts remain available.
            service.update_document(source['id'],{'notes':'Changed source-b notes after preview.'})
            with patch('polyscholar.ui.duplicates.plain_question',return_value=True),patch.object(QMessageBox,'warning') as warning:
                merge.merge()
                idle(window,merge)
                assert warning.called
            assert len(service.list_documents())==3 and merge._draft_sources['title']==source['id'] and not merge.merge_button.isEnabled()
            merge.load()
            idle(window,merge)
            assert merge.field_sources['title'].currentData()==source['id']
            # Failed refresh cannot re-enable confirmation against a stale master/revision.
            with patch.object(service,'merge_preview',side_effect=ValueError('预览失败，草稿保留。')),patch.object(QMessageBox,'warning') as warning:
                merge.load()
                idle(window,merge)
                assert warning.called and not merge.merge_button.isEnabled()
            merge.load()
            idle(window,merge)
            window.open_document(source['id'])
            idle(window,merge)
            assert window.reader_document['id']==source['id']
            with patch('polyscholar.ui.duplicates.plain_question',return_value=True):
                merge.merge()
                idle(window,merge,candidates)
            assert len(service.list_documents())==1 and window.selected()['id']==master['id'] and window.reader_document is None
            assert not merge.master.isEnabled() and not merge.sources_area.isEnabled() and not merge.reload_button.isEnabled()
            merge.detail_field.setCurrentIndex(merge.detail_field.findData('title'))
            assert source_title in merge.details.toPlainText()
            # 候选对话框释放 IO 后，本对话框仍需一次共享状态控件轮询。
            # Candidate IO release precedes this dialog's next shared-state control refresh.
            wait(lambda:merge.history_button.isEnabled())
            QTest.mouseClick(merge.history_button,Qt.MouseButton.LeftButton)
            idle(window,merge.history_dialog)
            assert merge.history_dialog.identifier==master['id'] and merge.history_dialog.records.count()==1
            merge.history_dialog.close()
            merged=service.list_documents()[0]
            assert merged['title']==source_title and merged['creators']==source_creators and set(merged['tags'])=={'source-a','source-b','manual-c'}
            assert all(text in merged['notes'] for text in ('Complete notes from source-a.','Changed source-b notes after preview.','Complete notes from manual-c.'))
            assert set(service.document_collections(master['id']))==set(collections)
            assert service.current_document_ir(source['id'])['id']==block['revisionId'] and service.list_claims(source['id'])[0]['id']==claim['id']
            assert (output/'synthetic.pdf').is_file() and len(service.store.list_jobs())==1
            assert service.import_pdf(root/'source-b.pdf')['id']==master['id']
            attachments=service.list_attachments(master['id'])
            assert len(attachments)==4 and any(row['documentId']==source['id'] and '合并前主PDF' in row['label'] for row in attachments)
            assert next(row for row in service.list_trash() if row['documentId']==trashed['documentId'])['parentDocumentId']==master['id']
            fulltext=service.search_fulltext('source-b')
            assert any(hit['documentId']==source['id'] and hit['parentDocumentId']==master['id'] for hit in fulltext['items'])
            assert window.citation_items.count()==1
            history=service.list_merge_history(master['id'])
            assert len(history)==1 and any(document['id']==source['id'] and document['notes']=='Changed source-b notes after preview.' for document in history[0]['snapshot']['documents'])
            for index in range(candidates.manual.count()):
                candidates.manual.item(index).setSelected(True)
            wait(lambda:candidates.history_button.isEnabled())
            QTest.mouseClick(candidates.history_button,Qt.MouseButton.LeftButton)
            history_dialog=candidates.history_dialog
            idle(window,history_dialog)
            assert history_dialog.records.count()==1 and 'Changed source-b notes after preview.' in history_dialog.details.toPlainText() and 'source-a' in history_dialog.details.toPlainText()
            history_dialog.close()
            merge.close()
            # Closing the candidate dialog and parent remains governed by the shared IO owner.
            started=threading.Event()
            release=threading.Event()
            real=service.list_duplicate_candidates
            def delayed(*args,**kwargs):
                started.set()
                release.wait(5)
                return real(*args,**kwargs)
            with patch.object(service,'list_duplicate_candidates',side_effect=delayed):
                candidates.refresh()
                wait(started.is_set)
                window.close()
                assert window._closing and not window._closed and candidates._closed
                release.set()
                wait(lambda:window._closed)
            assert not service._children and not candidates.timer.isActive()
        finally:
            window.close()
            service.close()
    print('Native duplicate merge: candidate/manual selection, complete differences, revision drift, retained identities, history and managed close passed')


if __name__=='__main__':
    main()
