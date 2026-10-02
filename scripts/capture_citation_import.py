# SPDX-License-Identifier: AGPL-3.0-only
"""隔离合成 CSL 数据的引文导入原生截图。

Native citation-import screenshot using isolated synthetic CSL data.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'design/screenshots/citation-import-python.png')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    app = QApplication([])
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-citation-import-capture-') as directory:
        root = Path(directory)
        service = LocalService(data_dir=root/'data')
        collection = service.create_collection('合成阅读计划')
        path = root/'synthetic-references.json'
        path.write_text(json.dumps([
            {'id':'synthetic-article','type':'article-journal','title':'合成文献 · 本地阅读与证据定位',
             'author':[{'family':'Chen','given':'Li'},{'literal':'Synthetic Research Collective'}],
             'editor':[{'family':'Smith','given':'Alex'}],
             'issued':{'date-parts':[[2024,2,29]]},'container-title':'Synthetic Journal',
             'unknown':'此字段用于展示转换损失提示'},
            {'id':'synthetic-book','type':'book','title':'合成图书 · 科研笔记方法',
             'author':[{'family':'Li','given':'Sam'}],'issued':{'date-parts':[[2023]]}},
            {'id':'unsupported','type':'webpage','title':'不支持的条目类型 · 不可勾选'}],ensure_ascii=False),encoding='utf-8')
        window = Window(service)
        try:
            window.show()
            app.processEvents()
            window.open_citation_import()
            dialog = window.citation_import_dialog
            dialog.resize(1024,700)
            dialog.path.setText(str(path))
            dialog.format.setCurrentIndex(dialog.format.findData('csl-json'))
            dialog.collection.setCurrentIndex(dialog.collection.findData(collection['id']))
            dialog.load()
            deadline = time.monotonic()+15
            while (window.io_worker is not None or dialog._busy) and time.monotonic()<deadline:
                QTest.qWait(10)
            assert dialog._preview and not dialog._busy
            dialog.select_valid()
            app.processEvents()
            assert dialog.width()==1024 and dialog.grab().save(str(args.output))
            assert service.list_documents()==[]
        finally:
            window.close()
            service.close()
    print('Native synthetic citation-import screenshot saved:',args.output)


if __name__=='__main__':
    main()
