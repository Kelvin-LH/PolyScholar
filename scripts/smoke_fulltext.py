# SPDX-License-Identifier: AGPL-3.0-only
"""Native local full-text flow on real generated PDFs; no model or network calls."""
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


def wait(predicate):
    deadline=time.monotonic()+15
    while not predicate() and time.monotonic()<deadline:QTest.qWait(10)
    assert predicate(),'Managed full-text operation did not finish'


def create(path,texts):
    with pymupdf.open() as pdf:
        for text in texts:
            page=pdf.new_page()
            if text:page.insert_text((60,80),text,fontname='china-s' if any(ord(char)>127 for char in text) else 'helv')
        pdf.save(path)


def select_pdf(dialog,identifier):
    for index in range(dialog.coverage.count()):
        if dialog.coverage.item(index).data(Qt.ItemDataRole.UserRole)['documentId']==identifier:dialog.coverage.setCurrentRow(index);return
    raise AssertionError('Expected scoped PDF coverage')


def state(dialog,identifier):
    return next(dialog.coverage.item(index).data(Qt.ItemDataRole.UserRole) for index in range(dialog.coverage.count()) if dialog.coverage.item(index).data(Qt.ItemDataRole.UserRole)['documentId']==identifier)


def main():
    app=QApplication([]);app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-fulltext-ui-') as directory:
        root=Path(directory);service=LocalService(data_dir=root/'data');create(root/'parent.pdf',['First page only.','Second page needle.']);create(root/'child.pdf',['Supplement needle. 中文术语']);create(root/'blank.pdf',['']);(root/'bad.pdf').write_bytes(b'%PDF-1.7 invalid')
        parent=service.import_pdf(root/'parent.pdf');service.update_document(parent['id'],{'title':'Scoped parent','tags':['scope']});child=service.import_attachment(parent['id'],root/'child.pdf','supplement');blank=service.import_pdf(root/'blank.pdf');bad=service.import_pdf(root/'bad.pdf')
        service.parse_document(parent['id']);service.parse_document(child['documentId'])
        window=Window(service)
        try:
            window.resize(1024,700);window.show();app.processEvents();window.open_fulltext();dialog=window.fulltext_dialog;wait(lambda:window.io_worker is None)
            assert dialog.coverage.count()==4 and state(dialog,blank['id'])['status']=='unparsed'
            dialog.query.setText('needle');dialog.search();assert not dialog.search_button.isEnabled();wait(lambda:window.io_worker is None)
            assert dialog.results.count()==2
            dialog.resize(1024,700);app.processEvents();assert dialog.width()<=1024 and dialog.height()<=700
            dialog.query.setText('文');dialog.search();wait(lambda:window.io_worker is None);assert dialog.results.count()==1 and dialog.results.item(0).data(Qt.ItemDataRole.UserRole)['documentId']==child['documentId']
            dialog.query.setText('needle');dialog.search();wait(lambda:window.io_worker is None)
            hit=next(dialog.results.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dialog.results.count()) if dialog.results.item(i).data(Qt.ItemDataRole.UserRole)['documentId']==parent['id'])
            assert hit['pageNumber']==2
            for index in range(dialog.results.count()):
                if dialog.results.item(index).data(Qt.ItemDataRole.UserRole)['documentId']==parent['id']:dialog.results.setCurrentRow(index);break
            dialog.locate();wait(lambda:window.io_worker is None and window.reader_document and window.reader_document['id']==parent['id']);wait(lambda:window.pdf_docs[0].pageCount()==2)
            assert window.pdf_views[0].pageNavigator().currentPage()==1
            for index in range(dialog.results.count()):
                if dialog.results.item(index).data(Qt.ItemDataRole.UserRole)['documentId']==child['documentId']:dialog.results.setCurrentRow(index);break
            dialog.locate();wait(lambda:window.io_worker is None and window.reader_document and window.reader_document['id']==child['documentId']);wait(lambda:window.pdf_docs[0].pageCount()==1)
            assert window.pdf_views[0].pageNavigator().currentPage()==0
            service.parse_document(child['documentId'])
            with patch.object(QMessageBox,'warning') as warning:
                dialog.locate();wait(lambda:window.io_worker is None);assert warning.called and '变化' in warning.call_args.args[2]
            dialog.search();wait(lambda:window.io_worker is None)
            select_pdf(dialog,parent['id']);before=service.current_document_ir(parent['id'])['id']
            with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):dialog.clear_selected();wait(lambda:window.io_worker is None)
            assert state(dialog,parent['id'])['status']=='cleared' and service.current_document_ir(parent['id'])['id']==before
            select_pdf(dialog,parent['id']);dialog.rebuild_selected();wait(lambda:window.io_worker is None);assert dialog.results.count()==2
            select_pdf(dialog,blank['id']);dialog.parse_selected();wait(lambda:window.io_worker is None);assert state(dialog,blank['id'])['status']=='no_text'
            select_pdf(dialog,bad['id']);dialog.parse_selected();wait(lambda:window.io_worker is None);assert state(dialog,bad['id'])['status']=='parse_failed'
            # Scope snapshots intersect metadata root filters while including child PDF text.
            dialog.close();window.search.setText('Scoped parent');window.open_fulltext();dialog=window.fulltext_dialog;wait(lambda:window.io_worker is None)
            dialog.query.setText('needle');dialog.search();wait(lambda:window.io_worker is None);assert dialog.coverage.count()==2 and dialog.results.count()==2
            assert window.width()==1024 and window.stack.widget(0).horizontalScrollBar().maximum()==0
            # Closing the dialog during IO hides it without deleting callbacks or the managed worker.
            started=threading.Event();release=threading.Event();real=service.search_fulltext
            def delayed(*args,**kwargs):started.set();release.wait(5);return real(*args,**kwargs)
            with patch.object(service,'search_fulltext',side_effect=delayed):
                dialog.search();wait(started.is_set);dialog.close();assert dialog._closed and not dialog.timer.isActive();release.set();wait(lambda:window.io_worker is None)
            window.close();service.close();service=LocalService(data_dir=root/'data');window=Window(service);window.show();app.processEvents();window.open_fulltext();dialog=window.fulltext_dialog;wait(lambda:window.io_worker is None)
            dialog.query.setText('needle');dialog.search();wait(lambda:window.io_worker is None);assert dialog.results.count()==2 and state(dialog,bad['id'])['status']=='parse_failed'
            # Main-window close uses its existing wait-for-IO lifecycle and stops the dialog timer.
            started=threading.Event();release=threading.Event();real=service.search_fulltext
            def delayed_close(*args,**kwargs):started.set();release.wait(5);return real(*args,**kwargs)
            with patch.object(service,'search_fulltext',side_effect=delayed_close):
                dialog.search();wait(started.is_set);window.close();assert window._closing and not window._closed and dialog._closed;release.set();wait(lambda:window._closed)
            assert not service._children and not dialog.timer.isActive()
        finally:window.close();service.close()
    print('Native local full-text: source/page navigation, current-index coverage, reindex, stale rejection, scope, persistence and managed close passed')


if __name__=='__main__':main()
