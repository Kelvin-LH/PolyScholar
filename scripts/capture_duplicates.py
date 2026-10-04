# SPDX-License-Identifier: AGPL-3.0-only
"""以隔离合成元数据和 PDF 捕获人工合并原生界面。

Capture native manual merge with isolated synthetic metadata and PDFs.
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


def wait(window,dialog):
    deadline=time.monotonic()+15
    while (window.io_worker is not None or dialog._busy or dialog._pending_action is not None) and time.monotonic()<deadline:
        QTest.qWait(10)
    assert window.io_worker is None and not dialog._busy


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'design/screenshots/duplicates-python.png')
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    app=QApplication([])
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-duplicates-capture-') as directory:
        root=Path(directory)
        service=LocalService(data_dir=root/'data')
        creators=[[{'role':'author','type':'person','family':'Chen','given':'Li','literal':''}],
                  [{'role':'author','type':'organization','literal':'Synthetic Research Collective','family':'','given':''},
                   {'role':'editor','type':'person','family':'Smith','given':'Alex','literal':''}]]
        for index in range(2):
            path=root/f'synthetic-{index}.pdf'
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((60,80),f'Synthetic merge preview source {index}.')
                pdf.save(path)
            document=service.import_pdf(path)
            service.update_document(document['id'],{'title':['合成文献 · 主记录','合成文献 · 另一份元数据'][index],
                'doi':'10.1234/synthetic-merge','year':'2024','creators':creators[index],
                'notes':['人工整理的原笔记。','另一来源的笔记，合并后保留出处。'][index]})
            service.parse_document(document['id'])
        window=Window(service)
        try:
            window.show()
            app.processEvents()
            window.open_duplicates()
            candidates=window.duplicates_dialog
            wait(window,candidates)
            candidates.open_preview()
            dialog=candidates.merge_dialog
            wait(window,dialog)
            dialog.detail_field.setCurrentIndex(dialog.detail_field.findData('creators'))
            dialog.resize(1024,700)
            app.processEvents()
            assert dialog.grab().save(str(args.output))
        finally:
            window.close()
            service.close()
    print('Native synthetic merge screenshot saved:',args.output)


if __name__=='__main__':
    main()
