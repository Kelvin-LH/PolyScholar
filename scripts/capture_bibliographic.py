# SPDX-License-Identifier: AGPL-3.0-only
"""隔离合成书目数据的原生截图。 / Native capture using isolated synthetic bibliography."""
import argparse
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'design/screenshots/bibliographic-python.png')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    app = QApplication([])
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-bibliographic-capture-') as directory:
        service = LocalService(data_dir=Path(directory)/'data')
        document = service.create_bibliographic_item({
            'title':'本地科研工作流 · 合成书目示例','itemType':'article-journal',
            'year':'2024','publicationTitle':'Synthetic Research Journal',
            'creators':[{'role':'author','type':'organization','literal':'Synthetic Research Collective','family':'','given':''}],
            'tags':['待添加 PDF','阅读计划'],
            'notes':'先整理书目信息，稍后添加真实 PDF；此截图仅使用合成数据。'})
        window = Window(service)
        try:
            window.resize(1024,700)
            window.show()
            app.processEvents()
            for row in range(window.document_list.count()):
                if window.document_list.item(row).data(Qt.ItemDataRole.UserRole)['id']==document['id']:
                    window.document_list.setCurrentRow(row)
                    break
            app.processEvents()
            assert window.width()==1024
            assert window.attachment_empty.isVisible() and not window.read_button.isEnabled()
            inspector = window.attachment_empty.parentWidget()
            while inspector is not None and not isinstance(inspector,QScrollArea):
                inspector = inspector.parentWidget()
            assert inspector is not None
            inspector.verticalScrollBar().setValue(inspector.verticalScrollBar().maximum())
            window.stack.widget(0).verticalScrollBar().setValue(window.stack.widget(0).verticalScrollBar().maximum())
            app.processEvents()
            assert window.grab().save(str(args.output))
            window.new_bibliographic()
            dialog = window.bibliographic_dialog
            dialog.fields['title'].setText('新的本地阅读计划')
            app.processEvents()
            assert dialog.grab().save(str(args.output.with_name('bibliographic-create-python.png')))
            dialog.close()
        finally:
            window.close()
            service.close()
    print('Native synthetic bibliography screenshot saved:',args.output)


if __name__=='__main__':
    main()
