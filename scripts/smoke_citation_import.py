# SPDX-License-Identifier: AGPL-3.0-only
"""三种本地引文导入的原生验真；不联网、不使用私人材料。

Native verification of three local citation formats, with no network or private material.
"""
from pathlib import Path
import json
import sys
import tempfile
import threading
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService

FIXTURES = {
    'bibtex':'''@article{valid,
 title={Synthetic <b>article</b>}, author={Chen, Li and {Synthetic Collective}},
 editor={Smith, Alex}, year={2024}, journal={Synthetic Journal},
 doi={10.1234/synthetic-bib}, note={Original local note}, unknown={loss warning}}
@online{invalid, title={Unsupported online record}, year={2024}}
''',
    'ris':'''TY  - JOUR
TI  - Synthetic RIS article
AU  - Chen, Li
AU  - Unparsed Collective
A2  - Smith, Alex
PY  - 2024
JO  - Synthetic Journal
DO  - 10.1234/synthetic-ris
N1  - Original RIS note
ZZ  - Unknown field
ER  -
TY  - ELEC
TI  - Unsupported online record
ER  -
''',
    'csl-json':json.dumps([
        {'id':'valid','type':'article-journal','title':'Synthetic CSL article',
         'author':[{'family':'Chen','given':'Li'},{'literal':'Synthetic Collective'}],
         'editor':[{'family':'Smith','given':'Alex'}],
         'issued':{'date-parts':[[2024,2,29]]},'container-title':'Synthetic Journal',
         'DOI':'10.1234/synthetic-csl','note':'Original CSL note','unknown':'loss warning'},
        {'id':'invalid','type':'webpage','title':'Unsupported online record'}])}


def wait(predicate):
    deadline = time.monotonic()+15
    while not predicate() and time.monotonic()<deadline:
        QTest.qWait(10)
    assert predicate(),'Native citation import did not settle'


def idle(window,dialog):
    wait(lambda:window.io_worker is None and not dialog._busy and dialog._pending_action is None)


def open_preview(window,path,fmt):
    window.open_citation_import()
    dialog = window.citation_import_dialog
    dialog.path.setText(str(path))
    dialog.format.setCurrentIndex(dialog.format.findData(fmt))
    wait(lambda:dialog.preview_button.isEnabled())
    QTest.mouseClick(dialog.preview_button,Qt.MouseButton.LeftButton)
    idle(window,dialog)
    return dialog


def select_valid(dialog):
    wait(lambda:dialog.select_all_button.isEnabled())
    QTest.mouseClick(dialog.select_all_button,Qt.MouseButton.LeftButton)


def click_import(dialog):
    wait(lambda:dialog.import_button.isEnabled())
    QTest.mouseClick(dialog.import_button,Qt.MouseButton.LeftButton)


def main():
    app = QApplication([])
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-citation-import-ui-') as directory:
        root = Path(directory)
        service = LocalService(data_dir=root/'data')
        collection = service.create_collection('Visible import target')
        window = Window(service)
        try:
            window.resize(1024,700)
            window.show()
            app.processEvents()
            paths = {}
            for fmt,data in FIXTURES.items():
                paths[fmt] = root/('references.'+{'bibtex':'bib','ris':'ris','csl-json':'json'}[fmt])
                paths[fmt].write_text(data,encoding='utf-8')
            canceled = open_preview(window,paths['bibtex'],'bibtex')
            select_valid(canceled)
            canceled.close()
            assert service.list_documents()==[]
            created = []
            for fmt,path in paths.items():
                dialog = open_preview(window,path,fmt)
                preview = dialog._preview
                wait(lambda:dialog.records.isEnabled() and dialog.select_all_button.isEnabled())
                assert preview and preview['total']==2 and preview['validCount']==1 and preview['invalidCount']==1
                assert dialog.width()<=1024
                invalid = dialog.records.item(1)
                assert not invalid.flags() & Qt.ItemFlag.ItemIsUserCheckable
                dialog.records.setCurrentRow(1)
                assert '不能导入' in dialog.details.toPlainText() and '错误' in dialog.details.toPlainText()
                QTest.keyClick(dialog.records,Qt.Key.Key_Space)
                assert not dialog.selected_indices()
                dialog.records.setCurrentRow(0)
                detail = dialog.details.toPlainText()
                assert 'Synthetic' in detail and '姓' in detail and '编者' in detail and preview['items'][0]['warnings']
                assert '<b>article</b>' in detail if fmt=='bibtex' else True
                assert not dialog.import_button.isEnabled()
                QTest.keyClick(dialog.records,Qt.Key.Key_Space)
                assert dialog.selected_indices()==[0]
                QTest.mouseClick(dialog.clear_button,Qt.MouseButton.LeftButton)
                assert not dialog.selected_indices()
                select_valid(dialog)
                dialog.collection.setCurrentIndex(dialog.collection.findData(collection['id']))
                assert '勾选 1 条' in dialog.selection_info.text() and collection['name'] in dialog.selection_info.text()
                wait(lambda:dialog.import_button.isEnabled())
                if fmt=='bibtex':
                    with patch.object(service,'import_metadata_preview',side_effect=ValueError('事务失败，预览保持。')),patch.object(QMessageBox,'warning') as warning:
                        click_import(dialog)
                        idle(window,dialog)
                        assert warning.called and dialog._preview==preview and dialog.selected_indices()==[0] and not service.list_documents()
                    dialog.path.setText(str(root/'other.bib'))
                    assert not dialog.import_button.isEnabled() and dialog._preview==preview
                    dialog.path.setText(str(path))
                    wait(lambda:dialog.import_button.isEnabled())
                    with patch.object(service,'preview_metadata_import',side_effect=ValueError('解析失败，预览保持。')),patch.object(QMessageBox,'warning') as warning:
                        dialog.load()
                        idle(window,dialog)
                        assert warning.called and dialog._preview==preview and dialog.selected_indices()==[0]
                        assert not dialog.import_button.isEnabled()
                    dialog.load()
                    idle(window,dialog)
                    select_valid(dialog)
                click_import(dialog)
                idle(window,dialog)
                wait(lambda:dialog.records.isEnabled())
                assert dialog._imported and not dialog.import_button.isEnabled() and not dialog.preview_button.isEnabled()
                assert not dialog.records.item(0).flags() & Qt.ItemFlag.ItemIsUserCheckable
                before = len(service.list_documents())
                dialog.import_selected()
                assert len(service.list_documents())==before
                current = service.list_documents()
                document = next(document for document in current if document['id'] not in created)
                created.append(document['id'])
                assert not document['hasAnyPdf'] and service.document_collections(document['id'])==[collection['id']]
                assert document['creators'][0]['family']=='Chen' and document['creators'][0]['given']=='Li'
                assert document['creators'][-1]['role']=='editor'
                assert document['notes']
                dialog.close()
            assert len(service.list_documents())==3
            # 文件变更被可信快照检查拒绝；选中草稿与数据库保持原样。
            # Trusted fingerprints reject changed files without discarding selected drafts or writing data.
            stale = open_preview(window,paths['ris'],'ris')
            select_valid(stale)
            paths['ris'].write_text(FIXTURES['ris'].replace('Synthetic RIS article','Changed source'),encoding='utf-8')
            with patch.object(QMessageBox,'warning') as warning:
                click_import(stale)
                idle(window,stale)
                assert warning.called and not stale._imported and stale.selected_indices()==[0] and len(service.list_documents())==3
            stale.close()
            window.collection_tree.setCurrentItem(window.collection_tree.topLevelItem(2))
            assert window.document_list.count()==3
            window.open_citation_import()
            target = window.citation_import_dialog
            assert target.collection.currentData()==collection['id']
            target.close()
            assert window.width()==1024 and window.inspector_scroll.horizontalScrollBar().maximum()==0
            # 关闭只隐藏预览，主窗口等待其拥有的解析线程，不会晚导入数据。
            # Closing hides the preview; the window waits for its parser worker, without importing later.
            window.open_citation_import()
            pending = window.citation_import_dialog
            pending.path.setText(str(paths['bibtex']))
            started = threading.Event()
            release = threading.Event()
            real = service.preview_metadata_import
            def delayed(*args):
                started.set()
                release.wait(5)
                return real(*args)
            with patch.object(service,'preview_metadata_import',side_effect=delayed):
                pending.load()
                wait(started.is_set)
                window.close()
                assert window._closing and pending._closed and not window._closed
                release.set()
                wait(lambda:window._closed)
            assert not pending.timer.isActive() and not service._children
            assert not service._citation_importer._workers
            service.close()
            service = LocalService(data_dir=root/'data')
            assert len(service.list_documents())==3
            assert all(service.document_collections(identifier)==[collection['id']] for identifier in created)
        finally:
            window.close()
            service.close()
    print('Native citation import: BibTeX/RIS/CSL-JSON, full identities/warnings/errors, selection/collection, drafts, changed-file rejection, restart and managed close passed')


if __name__=='__main__':
    main()
