# SPDX-License-Identifier: AGPL-3.0-only
"""Native GUI smoke with generated PDF and synthetic model IDs; no API requests."""
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QFileDialog
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
    print('Native GUI: import, edit, PDF reading, model selection, cache and export passed')

if __name__ == '__main__':
    main()
