# SPDX-License-Identifier: AGPL-3.0-only
"""Capture typed metadata screens with synthetic PDF data in a temporary library."""
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from PySide6.QtWidgets import QApplication
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService
from polyscholar.ui.creators import CreatorsDialog
from polyscholar.ui.searches import SearchDialog
from polyscholar.ui.fulltext import FullTextDialog


def main():
    output = Path(__file__).resolve().parents[1] / 'design/screenshots'
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([]); app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-metadata-capture-') as directory:
        root = Path(directory); source = root / 'synthetic.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((60, 80), 'Synthetic metadata UI fixture.'); pdf.save(source)
        service = LocalService(root / 'data')
        doc = service.import_pdf(source)
        creators = [dict(role='author', type='person', family='Chen', given='Li', literal=''),
                    dict(role='author', type='organization', literal='Research Collective', family='', given=''),
                    dict(role='editor', type='person', literal='完整姓名示例', family='', given='')]
        service.update_document(doc['id'], dict(title='Synthetic book · 本地书目信息示例', itemType='book',
            date='2024-02-29', publisher='Example Press', place='Melbourne', edition='2',
            isbn='9780306406157', creators=creators))
        window = Window(service)
        try:
            window.show(); window.document_tree.setCurrentItem(window.document_tree.topLevelItem(0))
            window.document_tree.parentWidget().parentWidget().parentWidget() and None or None; app.processEvents()
            window.grab().save(str(output / 'metadata-python.png'))
            dialog = CreatorsDialog(creators, window); dialog.show(); app.processEvents()
            dialog.grab().save(str(output / 'creators-python.png')); dialog.close()
            query = dict(match='all', conditions=[dict(field='itemType', operator='is', value='book'),
                dict(field='year', operator='after', value='2020')])
            saved = service.save_saved_search('近年图书', query); window.refresh()
            window.saved_searches.setCurrentIndex(window.saved_searches.findData(saved['id']))
            app.processEvents(); window.grab().save(str(output / 'searches-python.png'))
            dialog = SearchDialog(query, window); dialog.show(); app.processEvents()
            dialog.grab().save(str(output / 'search-rules-python.png')); dialog.close()
            service.parse_document(doc['id'])
            dialog = FullTextDialog(window, {}, '全部文献'); dialog.query.setText('metadata')
            dialog.show_search(service.search_fulltext('metadata')); dialog.show(); app.processEvents()
            dialog.grab().save(str(output / 'fulltext-python.png')); dialog.close()
        finally:
            window.close(); service.close()
    print('Synthetic native metadata screenshots saved')


if __name__ == '__main__':
    main()
