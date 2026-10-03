# SPDX-License-Identifier: AGPL-3.0-only
"""Capture native screens using synthetic documents in an isolated temporary library."""
import argparse
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea
from PySide6.QtGui import QPdfWriter,QPainter
from polyscholar.app import Window,STYLE
from polyscholar.service import LocalService

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'design/screenshots')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    app=QApplication([]);app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-ui-') as tmp:
        root=Path(tmp);service=LocalService(data_dir=root/'data')
        parent=service.create_collection('课题资料');child=service.create_collection('相关方法',parent['id'])
        for i,title in enumerate(['Synthetic PDF · 本地阅读示例','Synthetic PDF · 方法整理']):
            path=root/f'sample-{i}.pdf';writer=QPdfWriter(str(path));painter=QPainter(writer)
            painter.drawText(100,100,f'Synthetic local UI test {i}');painter.end();del painter,writer
            doc=service.import_pdf(path)
            service.update_document(doc['id'],{'title':title,'authors':'Demo Author','tags':['方法','待阅读'],'notes':'合成 PDF，仅用于界面验证。'})
            service.set_membership(doc['id'],child['id'])
            supplement=root/f'supplement-{i}.pdf';writer=QPdfWriter(str(supplement));painter=QPainter(writer)
            painter.drawText(100,100,f'Synthetic supplement {i}');painter.end();del painter,writer
            service.import_attachment(doc['id'],supplement,'supplement')
        window=Window(service)
        try:
            window.show();app.processEvents();window.document_tree.setCurrentItem(window.document_tree.topLevelItem(0))
            app.processEvents();window.grab().save(str(args.output/'library-python.png'))
            window.attachment_list.setCurrentRow(1)
            attachment_scroll=window.attachment_list.parentWidget()
            while attachment_scroll and not isinstance(attachment_scroll,QScrollArea):attachment_scroll=attachment_scroll.parentWidget()
            if attachment_scroll:attachment_scroll.ensureWidgetVisible(window.attachment_delete_button)
            app.processEvents();window.grab().save(str(args.output/'attachments-python.png'))
            window.nav.setCurrentRow(5);app.processEvents();window.grab().save(str(args.output/'settings-python.png'))
            service.parse_document(doc['id'])
            block=service.document_blocks(doc['id'])[0]
            service.save_claim(doc['id'],'合成 PDF 的本地证据笔记。',[{'blockId':block['id'],'quote':block['text']}])
            window.nav.setCurrentRow(3);window.evidence_doc.setCurrentIndex(window.evidence_doc.findData(doc['id']));window.load_evidence()
            window.evidence_blocks.item(0).setCheckState(Qt.CheckState.Checked)
            window.evidence_claims.setCurrentRow(0);app.processEvents();window.grab().save(str(args.output/'evidence-python.png'))
            window.resize(1024,700);window.nav.setCurrentRow(0);app.processEvents();window.grab().save(str(args.output/'library-1024.png'))
        finally:
            window.close();service.close()
    print('Native screenshots saved:',args.output)

if __name__=='__main__':main()
