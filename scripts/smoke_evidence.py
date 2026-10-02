# SPDX-License-Identifier: AGPL-3.0-only
"""Exercise the native local evidence flow with actual PDFs, without model calls."""
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from polyscholar.app import Window
from polyscholar.service import LocalService


def wait_until(predicate,timeout=15):
    deadline=time.monotonic()+timeout
    while not predicate() and time.monotonic()<deadline:QTest.qWait(10)
    assert predicate(),'Native evidence operation did not complete'


def main():
    app=QApplication([])
    with tempfile.TemporaryDirectory(prefix='polyscholar-evidence-ui-') as directory:
        root=Path(directory);pdf=root/'evidence.pdf'
        with pymupdf.open() as document:
            page=document.new_page(width=400,height=500);page.insert_text((60,80),'First page local evidence.\nSecond line exact quote.')
            page=document.new_page(width=400,height=500);page.insert_text((80,110),'Rotated cropped page evidence.')
            page.set_cropbox(pymupdf.Rect(20,30,380,470));page.set_rotation(90);document.save(pdf)
        blank=root/'scan.pdf'
        with pymupdf.open() as document:
            document.new_page();document.save(blank)
        service=LocalService(data_dir=root/'data')
        source=service.import_pdf(pdf);scanned=service.import_pdf(blank)
        window=Window(service)
        try:
            window.show();app.processEvents();window.evidence_doc.setCurrentIndex(window.evidence_doc.findData(source['id']))
            window.parse_evidence()
            assert not window.parse_button.isEnabled() and not window.evidence_doc.isEnabled()
            window.parse_evidence()  # Double actions must not launch another worker.
            wait_until(lambda:window.io_worker is None)
            assert window.evidence_blocks.count()==2
            cursor=window.evidence_text.textCursor();cursor.select(QTextCursor.SelectionType.Document);window.evidence_text.setTextCursor(cursor)
            window.claim_text.setPlainText('A manually authored note, not a verified conclusion.')
            assert window.save_claim_button.isEnabled()
            window.save_evidence_claim();wait_until(lambda:window.io_worker is None)
            claims=service.list_claims(source['id']);assert len(claims)==1 and not claims[0]['stale']
            window.evidence_claims.setCurrentRow(0)
            assert 'First page local evidence.' in window.saved_quote.toPlainText()
            assert '\n' in claims[0]['evidence'][0]['quote']
            service.save_claim(source['id'],'A note without evidence.',[])
            window.refresh_evidence_claims(source['id'])
            assert any('缺少原文证据' in window.evidence_claims.item(index).text() for index in range(window.evidence_claims.count()))
            window.evidence_blocks.setCurrentRow(1)
            block=window.evidence_blocks.currentItem().data(Qt.ItemDataRole.UserRole)
            window.locate_evidence();wait_until(lambda:window.io_worker is None and window.pdf_docs[0].pageCount()==2)
            navigator=window.pdf_views[0].pageNavigator();wait_until(lambda:navigator.currentPage()==1)
            size=window.pdf_docs[0].pagePointSize(1);box=block['bbox'];position=navigator.currentLocation()
            assert abs(position.x()-(box[0]+box[2])*.5*size.width())<1
            assert abs(position.y()-(box[1]+box[3])*.5*size.height())<1
            window.parse_evidence();wait_until(lambda:window.io_worker is None)
            assert all(claim['stale'] for claim in service.list_claims(source['id']))
            assert '引用已失效' in window.evidence_claims.item(0).text()
            window.evidence_claims.setCurrentRow(0)
            assert '旧解析版本' in window.saved_quote.toPlainText()
            window.locate_saved_claim();wait_until(lambda:window.io_worker is None)
            assert window.pdf_views[0].pageNavigator().currentPage()==0
            window.refresh();assert window.evidence_doc.currentData()==source['id']
            window.evidence_doc.setCurrentIndex(window.evidence_doc.findData(scanned['id']))
            assert window.claim_text.toPlainText()=='' and window.evidence_blocks.count()==0
            window.parse_evidence();wait_until(lambda:window.io_worker is None)
            assert service.current_document_ir(scanned['id'])['status']=='no_text'
            assert '不执行 OCR' in window.evidence_status.text() and not window.save_claim_button.isEnabled()
            original_parse=service.parse_document
            def slow_parse(identifier):
                time.sleep(.1);return original_parse(identifier)
            with patch.object(service,'parse_document',side_effect=slow_parse):
                window.parse_evidence();window.close();assert not window._closed
                wait_until(lambda:window._closed)
        finally:
            window.close();service.close()
    print('Native evidence: actual PDF parse, quote notes, rotated/cropped navigation, stale references, no-text state and safe busy close passed')


if __name__=='__main__':main()
