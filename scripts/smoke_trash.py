# SPDX-License-Identifier: AGPL-3.0-only
"""真实生成 PDF 的原生回收站检查；不运行翻译引擎或网络 API。

Native trash flow on generated PDFs; no translation engine or network API.
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
from polyscholar.ui.trash import plain_question


def wait(predicate):
    deadline = time.monotonic()+15
    while not predicate() and time.monotonic()<deadline:
        QTest.qWait(10)
    assert predicate(), 'Managed trash operation did not finish'


def idle(window, dialog=None):
    wait(lambda:window.io_worker is None and (dialog is None or (not dialog._busy and dialog._pending_action is None)))


def select(dialog, identifier):
    for index in range(dialog.items.count()):
        if dialog.items.item(index).data(Qt.ItemDataRole.UserRole)['documentId']==identifier:
            dialog.items.setCurrentRow(index)
            idle(dialog.window, dialog)
            return
    raise AssertionError('Expected explicit trash marker')


def create(path, text):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((60,80),text)
        pdf.save(path)


def move(window, action, answer=QMessageBox.StandardButton.Yes):
    with patch('polyscholar.ui.trash.plain_question',return_value=answer==QMessageBox.StandardButton.Yes):
        action()
        wait(lambda:window.io_worker is None and window._trash_operation._closed)


def main():
    app = QApplication([])
    app.setStyleSheet(STYLE)
    unsafe_text='永久删除 <b>Untrusted title</b> & 原文，不能解释为 HTML。'
    def inspect_confirmation(box):
        assert box.textFormat()==Qt.TextFormat.PlainText and box.text()==unsafe_text
        assert box.defaultButton()==box.button(QMessageBox.StandardButton.No)
        return QMessageBox.StandardButton.No
    with patch.object(QMessageBox,'exec',inspect_confirmation):
        assert not plain_question(None,'纯文本确认',unsafe_text)
    with tempfile.TemporaryDirectory(prefix='polyscholar-trash-ui-') as directory:
        root = Path(directory)
        for name in ('parent','supplement','translation'):
            create(root/f'{name}.pdf',f'Generated {name} evidence for recoverable deletion.')
        service = LocalService(data_dir=root/'data')
        parent = service.import_pdf(root/'parent.pdf')
        service.update_document(parent['id'],{'title':'Recoverable local entry','notes':'Local retained note','tags':['retained']})
        supplement = service.import_attachment(parent['id'],root/'supplement.pdf','supplement')
        translation = service.import_attachment(parent['id'],root/'translation.pdf','translation')
        collection = service.create_collection('Retained membership')
        service.set_membership(parent['id'],collection['id'])
        service.parse_document(parent['id'])
        block = service.document_blocks(parent['id'])[0]
        claim = service.save_claim(parent['id'],'Retained evidence note',[{'blockId':block['id'],'quote':block['text']}])
        # 合成已完成任务仅验证关系保留，不能作为真实翻译验收。
        # The synthetic completed job verifies retention, not real translation acceptance.
        job = service.store.new_job(parent['id'],'babeldoc')
        output = Path(job['outputDir'])
        output.mkdir(parents=True)
        create(output/'result.pdf','Synthetic translation artifact.')
        job.update(state='completed',artifacts=['result.pdf'])
        service.store.put_job(job)
        window = Window(service)
        try:
            window.resize(1024,700)
            window.show()
            app.processEvents()
            window.document_list.setCurrentRow(0)
            window.attachment_list.setCurrentRow(1)
            window.read_attachment()
            idle(window)
            assert window.reader_document['id']==supplement['documentId']
            move(window,window.remove_attachment)
            assert window.reader_document is None and len(service.list_attachments(parent['id']))==2
            assert len(service.list_trash())==1
            window.document_list.setCurrentRow(0)
            move(window,window.delete_doc,QMessageBox.StandardButton.No)
            assert len(service.list_documents())==1
            window.open_original()
            idle(window)
            move(window,window.delete_doc)
            assert not service.list_documents() and window.reader_document is None
            assert (root/'parent.pdf').is_file() and (output/'result.pdf').is_file()
            window.open_trash()
            dialog = window.trash_dialog
            idle(window,dialog)
            dialog.resize(1024,700)
            app.processEvents()
            assert dialog.width()<=1024 and dialog.height()<=700 and window.width()==1024
            for control in (dialog.restore_button,dialog.purge_button,dialog.refresh_button,dialog.retry_button):
                assert control.mapTo(dialog,control.rect().topRight()).x()<dialog.width()
            assert dialog.items.accessibleName() and dialog.preview.accessibleName() and dialog.pending.accessibleName()
            select(dialog,supplement['documentId'])
            assert not dialog.restore_button.isEnabled() and '主条目' in dialog.status.text()
            select(dialog,parent['id'])
            assert 'PDF 文件：3' in dialog.preview.toPlainText() and '翻译产物：1' in dialog.preview.toPlainText()
            # 注入服务失败仅验证界面不丢状态，数据库原子性另由后端测试验收。
            # Injected service failure checks UI retention; backend tests verify actual DB atomicity.
            with patch.object(service,'purge_document',side_effect=ValueError('永久删除失败，回收站保持。')),patch('polyscholar.ui.trash.plain_question',return_value=True),patch.object(QMessageBox,'warning') as warning:
                dialog.purge()
                idle(window,dialog)
                assert warning.called and dialog.items.count()==2 and len(service.list_trash())==2 and '失败' in dialog.status.text()
            # A canceled permanent deletion retains all documents and managed files.
            with patch('polyscholar.ui.trash.plain_question',return_value=False):
                dialog.purge()
            assert len(service.list_trash())==2 and (output/'result.pdf').is_file()
            dialog.restore()
            idle(window,dialog)
            assert len(service.list_documents())==1 and len(service.list_attachments(parent['id']))==2
            assert service.document_collections(parent['id'])==[collection['id']]
            assert service.list_claims(parent['id'])[0]['id']==claim['id'] and service.current_document_ir(parent['id'])['id']==block['revisionId']
            assert len(service.store.list_jobs())==1 and (output/'result.pdf').is_file()
            select(dialog,supplement['documentId'])
            with patch.object(service,'restore_document',side_effect=ValueError('恢复失败，记录保持。')),patch.object(QMessageBox,'warning') as warning:
                dialog.restore()
                idle(window,dialog)
                assert warning.called and dialog.items.count()==1 and '失败' in dialog.status.text()
            dialog.restore()
            idle(window,dialog)
            assert len(service.list_attachments(parent['id']))==3 and not service.list_trash()
            dialog.close()
            # Active child jobs block moving the entire family, without hiding the library entry.
            active = service.store.new_job(translation['documentId'],'babeldoc')
            window.document_list.setCurrentRow(0)
            with patch.object(QMessageBox,'warning') as warning:
                move(window,window.delete_doc)
                assert warning.called and len(service.list_documents())==1 and not service.list_trash()
            active.update(state='failed')
            service.store.put_job(active)
            move(window,window.delete_doc)
            # Persisted markers survive a new application session.
            window.close()
            service.close()
            service = LocalService(data_dir=root/'data')
            window = Window(service)
            window.show()
            app.processEvents()
            window.open_trash()
            dialog = window.trash_dialog
            idle(window,dialog)
            select(dialog,parent['id'])
            # Denying actual owned-file cleanup must leave a durable, non-restorable queue.
            with patch.object(service.store,'_remove_managed_path',side_effect=PermissionError('Synthetic cleanup denial')),patch('polyscholar.ui.trash.plain_question',return_value=True):
                dialog.purge()
                idle(window,dialog)
            assert not service.list_trash() and dialog.items.count()==0 and dialog.pending.count()==1
            assert '无法恢复' in dialog.status.text() and '未完成' in dialog.status.text() and '已完成' not in dialog.status.text()
            assert (output/'result.pdf').exists() and all((root/f'{name}.pdf').exists() for name in ('parent','supplement','translation'))
            dialog.retry()
            idle(window,dialog)
            assert not service.list_pending_cleanup() and not (output/'result.pdf').exists() and '已完成' in dialog.status.text()
            # Main-window close still waits for the shared worker while stopping dialog timers.
            started = threading.Event()
            release = threading.Event()
            real = service.list_trash
            def delayed():
                started.set()
                release.wait(5)
                return real()
            with patch.object(service,'list_trash',side_effect=delayed):
                dialog.refresh()
                wait(started.is_set)
                window.close()
                assert window._closing and not window._closed and dialog._closed
                release.set()
                wait(lambda:window._closed)
            assert not service._children and not dialog.timer.isActive()
        finally:
            window.close()
            service.close()
    print('Native trash: family/child recovery, retained relationships, scoped purge, pending cleanup, persistence and managed shutdown passed')


if __name__=='__main__':
    main()
