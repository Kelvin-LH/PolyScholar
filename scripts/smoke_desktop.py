# SPDX-License-Identifier: AGPL-3.0-only
"""Native GUI smoke with generated PDF and synthetic model IDs; no API requests."""
from pathlib import Path
import sys
import tempfile
import time
import threading
import json
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QDialog
from PySide6.QtGui import QPdfWriter, QPainter
from PySide6.QtTest import QTest
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService

def wait_until(predicate, timeout=5):
    deadline=time.monotonic()+timeout
    while not predicate() and time.monotonic()<deadline:
        QTest.qWait(10)
    assert predicate(), 'Asynchronous GUI operation did not complete'

def main():
    app = QApplication([])
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-gui-check-') as directory:
        root = Path(directory)
        source = root / 'synthetic.pdf'
        writer = QPdfWriter(str(source))
        painter = QPainter(writer)
        painter.drawText(100, 100, 'Synthetic PDF for local UI validation')
        painter.end()
        del painter, writer  # Release the PDF file handle before import/Windows cleanup.
        service = LocalService(data_dir=root / 'data', resources_dir=root / 'resources')
        window = Window(service)
        try:
            window.show()
            app.processEvents()
            with patch.object(QFileDialog, 'getOpenFileNames', return_value=([str(source)], '')):
                window.import_pdf()
                assert not window.import_button.isEnabled()
                wait_until(lambda:window.io_worker is None)
            assert len(window.docs) == 1
            window.document_tree.setCurrentItem(window.document_tree.topLevelItem(0))
            window.fields['title'].setText('Local reading check')
            window.fields['tags'].setText('reading, test')
            window.abstract.setPlainText('A private local abstract')
            window.save_doc()
            saved = service.list_documents()[0]
            assert saved['title'] == 'Local reading check'
            assert saved['tags'] == ['reading', 'test']
            assert saved['abstract'] == 'A private local abstract'
            with patch.object(QInputDialog, 'getText', return_value=('项目', True)):
                window.new_collection()
            parent=service.list_collections()[0]
            window.collection_tree.setCurrentItem(window.collection_tree.topLevelItem(2))
            assert window.document_tree.topLevelItemCount()==0
            with patch.object(QFileDialog, 'getOpenFileNames', return_value=([str(source)], '')):
                window.import_pdf()
                assert not window.import_button.isEnabled()
                wait_until(lambda:window.io_worker is None)
            assert window.document_tree.topLevelItemCount()==1
            window.document_tree.setCurrentItem(window.document_tree.topLevelItem(0))
            with patch.object(QInputDialog, 'getText', return_value=('方法', True)):
                window.new_collection()
            child=next(c for c in service.list_collections() if c['parentId']==parent['id'])
            with patch.object(window, 'collection_picker', return_value=(True,child['id'])):
                window.add_to_collection()
            window.remove_from_collection()
            assert window.document_tree.topLevelItemCount()==0
            assert service.document_collections(saved['id'])==[child['id']]
            window.include_children.setChecked(True)
            assert window.document_tree.topLevelItemCount()==1
            for i in range(window.tag_filter.count()):
                window.tag_filter.item(i).setSelected(True)
            window.search.setText('Local')
            assert window.document_tree.topLevelItemCount()==1
            window.search.setText('unmatched')
            assert window.document_tree.topLevelItemCount()==0
            window.clear_filters()
            window.collection_tree.setCurrentItem(window.collection_tree.topLevelItem(0))
            window.document_tree.setCurrentItem(window.document_tree.topLevelItem(0))
            window.open_original()
            wait_until(lambda:window.io_worker is None and window.pdf_document.pageCount()==1)
            assert window.pdf_document.pageCount() == 1
            # A completed HTML task updates the GUI via service events (progress N/M + usage).
            job=service.store.new_job(saved['id'],'html-llm')
            output=Path(job['outputDir']);output.mkdir(parents=True)
            (output/'translated.html').write_text('<html><body>zh</body></html>',encoding='utf-8')
            job.update(state='completed',artifacts=['translated.html'],progress={'current':68,'total':68},usage={'total_tokens':42})
            service.store.put_job(job)
            thread=threading.Thread(target=service._notify_job,args=(job,));thread.start();thread.join()
            app.processEvents()
            assert window.job_table.item(0,3).text()=='68 / 68'
            assert '42' in window.job_table.item(0,4).text()

            assert '全文' in window.send_boundary.text()
            window.nav.setCurrentRow(5)
            window.cache.setText(str(root / 'custom-cache'))
            window.timeout.setValue(1200)
            window.key.setText('synthetic-session-key')
            window.save_settings()
            assert Path(service.get_settings()['cachePath']) == (root / 'custom-cache').resolve()
            assert window.key.text() == ''
            assert service.get_settings()['timeoutSeconds']==1200
            with patch.object(service, 'list_models', return_value=['synthetic-a', 'synthetic-b']):
                window.fetch_models()
                assert window.model_worker.wait(2000)
                QTest.qWait(20)
                assert window.model.count() == 2
                assert window.fetch_button.isEnabled()
            second=root/'second.pdf';second.write_bytes(source.read_bytes()+b'\n% Second local document\n')
            service.import_pdf(second)
            window.task_doc.setCurrentIndex(window.task_doc.findData(saved['id']))
            window.refresh()
            assert window.task_doc.currentData()==saved['id']
            for index in range(window.citation_items.count()):
                item=window.citation_items.item(index)
                item.setSelected(item.data(256)==saved['id'])
            for label, marker in [('BibTeX','@article'),('RIS','TY  -'),('CSL-JSON','Local reading check')]:
                window.citation_format.setCurrentText(label)
                assert marker in window.citation_preview.toPlainText()
            exported = root / 'references.json'
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(exported), '')):
                window.export_citations()
            assert 'Local reading check' in exported.read_text(encoding='utf-8')
            assert len(json.loads(exported.read_text(encoding='utf-8')))==1
            diagnostics=root/'diagnostics.json'
            with patch.object(QDialog,'exec',return_value=QDialog.DialogCode.Accepted), patch.object(QFileDialog,'getSaveFileName',return_value=(str(diagnostics),'')):
                window.preview_diagnostics()
            assert 'schemaVersion' in json.loads(diagnostics.read_text(encoding='utf-8'))
            assert 'synthetic-session-key' not in diagnostics.read_text(encoding='utf-8')
            window.resize(1024, 700)
            app.processEvents()
            assert window.endpoint.height() >= 40
            original_read=service.read_pdf
            def slow_read(identifier):
                time.sleep(0.05);return original_read(identifier)
            with patch.object(service,'read_pdf',side_effect=slow_read):
                window.open_original()
                window.close()
                assert not window._closed
                wait_until(lambda:window._closed)
            reopened = LocalService(data_dir=root / 'data', resources_dir=root / 'resources')
            try:
                assert next(d for d in reopened.list_documents() if d['id']==saved['id'])['abstract'] == 'A private local abstract'
                assert reopened._key == ''
            finally:
                reopened.close()
        finally:
            window.close()
            service.close()
    print('Native GUI: import, edit, collections, tag filtering, PDF reading, task events, artifact selection, timeout, citation formats, export and safe busy close passed')

if __name__ == '__main__':
    main()
