# SPDX-License-Identifier: AGPL-3.0-only
"""Native GUI smoke with generated PDF and synthetic model IDs; no API requests."""
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog
from PySide6.QtGui import QPdfWriter, QPainter
from PySide6.QtTest import QTest
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService

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
            assert len(window.docs) == 1
            window.document_list.setCurrentRow(0)
            window.fields['title'].setText('Local reading check')
            window.fields['tags'].setText('reading, test')
            window.notes.setPlainText('A private local note')
            window.save_doc()
            saved = service.list_documents()[0]
            assert saved['title'] == 'Local reading check'
            assert saved['tags'] == ['reading', 'test']
            assert saved['notes'] == 'A private local note'
            with patch.object(QInputDialog, 'getText', return_value=('项目', True)):
                window.new_collection()
            parent=service.list_collections()[0]
            window.collection_tree.setCurrentItem(window.collection_tree.topLevelItem(2))
            assert window.document_list.count()==0
            with patch.object(QFileDialog, 'getOpenFileNames', return_value=([str(source)], '')):
                window.import_pdf()
            assert window.document_list.count()==1
            window.document_list.setCurrentRow(0)
            with patch.object(QInputDialog, 'getText', return_value=('方法', True)):
                window.new_collection()
            child=next(c for c in service.list_collections() if c['parentId']==parent['id'])
            with patch.object(window, 'collection_picker', return_value=(True,child['id'])):
                window.add_to_collection()
            window.remove_from_collection()
            assert window.document_list.count()==0
            assert service.document_collections(saved['id'])==[child['id']]
            window.include_children.setChecked(True)
            assert window.document_list.count()==1
            for i in range(window.tag_filter.count()):
                window.tag_filter.item(i).setSelected(True)
            window.search.setText('Local')
            assert window.document_list.count()==1
            window.search.setText('unmatched')
            assert window.document_list.count()==0
            window.clear_filters()
            window.collection_tree.setCurrentItem(window.collection_tree.topLevelItem(0))
            window.document_list.setCurrentRow(0)
            window.open_original()
            app.processEvents()
            assert window.pdf_docs[0].pageCount() == 1
            window.nav.setCurrentRow(5)
            window.cache.setText(str(root / 'custom-cache'))
            window.key.setText('synthetic-session-key')
            window.save_settings()
            assert Path(service.get_settings()['cachePath']) == (root / 'custom-cache').resolve()
            assert window.key.text() == ''
            with patch.object(service, 'list_models', return_value=['synthetic-a', 'synthetic-b']):
                window.fetch_models()
                assert window.model_worker.wait(2000)
                QTest.qWait(20)
                assert window.model.count() == 2
                assert window.fetch_button.isEnabled()
            exported = root / 'references.json'
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(exported), '')):
                window.export_citations()
            assert 'Local reading check' in exported.read_text(encoding='utf-8')
            window.resize(1024, 700)
            app.processEvents()
            assert window.endpoint.height() >= 40
            window.close()
            reopened = LocalService(data_dir=root / 'data', resources_dir=root / 'resources')
            try:
                assert reopened.list_documents()[0]['notes'] == 'A private local note'
                assert reopened._key == ''
            finally:
                reopened.close()
        finally:
            window.close()
            service.close()
    print('Native GUI: import, edit, collections, tag filtering, PDF reading, model selection, cache and export passed')

if __name__ == '__main__':
    main()
