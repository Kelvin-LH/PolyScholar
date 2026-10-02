# SPDX-License-Identifier: AGPL-3.0-only
"""使用隔离合成文献捕获回收站原生效果图。

Capture the native trash dialog with isolated synthetic documents.
"""
import argparse
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService


def create(path, text):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((60,80),text)
        pdf.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'design/screenshots/trash-python.png')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    app = QApplication([])
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-trash-capture-') as directory:
        root = Path(directory)
        create(root/'original.pdf','Synthetic local evidence for trash preview.')
        create(root/'supplement.pdf','Synthetic supplement independently moved to trash.')
        service = LocalService(data_dir=root/'data')
        parent = service.import_pdf(root/'original.pdf')
        service.update_document(parent['id'],{'title':'合成文献 · 可恢复的本地资料','notes':'用于原生界面展示的合成笔记。','tags':['待整理']})
        child = service.import_attachment(parent['id'],root/'supplement.pdf','supplement')
        collection = service.create_collection('课题资料')
        service.set_membership(parent['id'],collection['id'])
        service.parse_document(parent['id'])
        block = service.document_blocks(parent['id'])[0]
        service.save_claim(parent['id'],'合成原文的证据笔记。',[{'blockId':block['id'],'quote':block['text']}])
        service.trash_document(child['documentId'])
        service.trash_document(parent['id'])
        window = Window(service)
        try:
            window.show()
            app.processEvents()
            window.open_trash()
            dialog = window.trash_dialog
            deadline = time.monotonic()+15
            while (window.io_worker is not None or dialog._busy or dialog._pending_action is not None) and time.monotonic()<deadline:
                QTest.qWait(10)
            assert window.io_worker is None and not dialog._busy
            for index in range(dialog.items.count()):
                if dialog.items.item(index).data(Qt.ItemDataRole.UserRole)['documentId']==parent['id']:
                    dialog.items.setCurrentRow(index)
                    break
            while (window.io_worker is not None or dialog._busy or dialog._pending_action is not None) and time.monotonic()<deadline:
                QTest.qWait(10)
            dialog.resize(1024,700)
            app.processEvents()
            assert dialog.grab().save(str(args.output))
        finally:
            window.close()
            service.close()
    print('Native synthetic trash screenshot saved:',args.output)


if __name__=='__main__':
    main()
