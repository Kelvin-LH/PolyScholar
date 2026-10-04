# SPDX-License-Identifier: AGPL-3.0-only
"""Native advanced metadata searches with generated PDFs; no network requests."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService
from polyscholar.ui.searches import SearchDialog


def condition(field,operator,value=''):return {'field':field,'operator':operator,'value':value}


def apply(window,query):
    dialog=SearchDialog(query,window)
    with patch('polyscholar.ui.library.SearchDialog',return_value=dialog),patch.object(dialog,'exec',return_value=QDialog.DialogCode.Accepted):window.edit_search()


def titles(window):return {window.document_list.item(i).text() for i in range(window.document_list.count())}


def main():
    app=QApplication([]);app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-searches-ui-') as directory:
        root=Path(directory);service=LocalService(data_dir=root/'data')
        docs=[]
        for index,(title,year,tag) in enumerate([('Local Alpha','2024','red'),('Local Beta','2023','blue'),('Gamma','2025','red')]):
            path=root/f'{index}.pdf'
            with pymupdf.open() as pdf:pdf.new_page().insert_text((60,80),f'Unique generated PDF {index}');pdf.save(path)
            doc=service.import_pdf(path);service.update_document(doc['id'],{'title':title,'year':year,'tags':[tag]});docs.append(doc)
        collection=service.create_collection('Subset');service.set_membership(docs[0]['id'],collection['id'])
        window=Window(service)
        try:
            window.resize(1024,700);window.show();app.processEvents()
            scroll=window.stack.currentWidget();assert window.width()==1024 and window.height()==700 and scroll.horizontalScrollBar().maximum()==0
            for control in (window.advanced_search_button,window.saved_searches,window.save_search_button,window.update_search_button,window.delete_search_button):
                assert control.mapTo(scroll.viewport(),control.rect().topRight()).x()<scroll.viewport().width()
            all_query={'match':'all','conditions':[condition('title','contains','Local'),condition('year','after','2023')]}
            apply(window,all_query);assert titles(window)=={'Local Alpha'}
            any_query={'match':'any','conditions':[condition('title','is','Local Beta'),condition('year','after','2024')]}
            apply(window,any_query);assert titles(window)=={'Local Beta','Gamma'}
            cancelled=SearchDialog(all_query,window)
            with patch('polyscholar.ui.library.SearchDialog',return_value=cancelled),patch.object(cancelled,'exec',return_value=QDialog.DialogCode.Rejected):window.edit_search()
            assert window.advanced_query==any_query and titles(window)=={'Local Beta','Gamma'}
            with patch.object(QMessageBox,'warning') as warning:
                apply(window,{'match':'all','conditions':[condition('year','after','invalid')]});assert warning.called
            assert window.advanced_query==any_query and titles(window)=={'Local Beta','Gamma'}
            with patch.object(QInputDialog,'getText',return_value=('保存搜索名称很长仍应在窄窗口内完整保留规则 '+ 'Local metadata '*5,True)):window.save_new_search()
            saved_id=window.saved_searches.currentData();assert saved_id and len(service.list_saved_searches())==1
            app.processEvents();assert scroll.horizontalScrollBar().maximum()==0 and window.width()==1024
            window.refresh();assert window.saved_searches.currentData()==saved_id and window.advanced_query==any_query
            # Existing quick/collection/tag restrictions intersect the advanced rules.
            window.search.setText('Beta');assert titles(window)=={'Local Beta'};window.search.clear()
            for i in range(window.tag_filter.count()):window.tag_filter.item(i).setSelected(window.tag_filter.item(i).text()=='red')
            assert titles(window)=={'Gamma'}
            window.clear_filters();assert titles(window)=={'Local Alpha','Local Beta','Gamma'} and window.saved_searches.currentData() is None
            window.saved_searches.setCurrentIndex(window.saved_searches.findData(saved_id));apply(window,all_query)
            assert window._saved_query_changed and service.list_saved_searches()[0]['query']==any_query
            with patch.object(QInputDialog,'getText',return_value=('Updated rule',False)):window.update_saved_search()
            assert service.list_saved_searches()[0]['query']==any_query
            with patch.object(QInputDialog,'getText',return_value=('Updated rule',True)):window.update_saved_search()
            assert service.list_saved_searches()[0]['query']==all_query and not window._saved_query_changed
            subset=window.collection_tree.topLevelItem(2);window.collection_tree.setCurrentItem(subset);assert titles(window)=={'Local Alpha'}
            window.clear_filters();window.saved_searches.setCurrentIndex(window.saved_searches.findData(saved_id))
            # Saved searches re-evaluate live metadata rather than retaining a snapshot.
            service.update_document(docs[1]['id'],{'year':'2024'});window.refresh();assert titles(window)=={'Local Alpha','Local Beta'}
            # A second application session loads saved rules from local SQLite.
            window.close();service.close();service=LocalService(data_dir=root/'data');window=Window(service);window.show();app.processEvents()
            window.saved_searches.setCurrentIndex(window.saved_searches.findData(saved_id));assert titles(window)=={'Local Alpha','Local Beta'}
            with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.No):window.delete_saved_search()
            assert len(service.list_saved_searches())==1
            with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):window.delete_saved_search()
            assert not service.list_saved_searches() and len(service.list_documents())==3
            dialog=SearchDialog(parent=window);dialog.add_condition(condition('year','after','2024'))
            field=dialog.table.cellWidget(0,0);operator=dialog.table.cellWidget(0,1)
            assert operator.findData('before')>=0
            field.setCurrentIndex(field.findData('title'));assert operator.findData('before')==-1
            operator.setCurrentIndex(operator.findData('is_empty'));assert not dialog.table.cellWidget(0,2).isEnabled() and dialog.query()['conditions'][0]['value']==''
            for _ in range(22):dialog.add_condition()
            assert dialog.table.rowCount()==20 and not dialog.add_button.isEnabled();dialog.remove_condition();assert dialog.table.rowCount()==19 and dialog.add_button.isEnabled()
        finally:window.close();service.close()
    print('Native metadata searches: all/any, scope intersection, cancellation, persistence, dynamic rules and safe deletion passed')


if __name__=='__main__':main()
