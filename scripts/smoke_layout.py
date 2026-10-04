# SPDX-License-Identifier: AGPL-3.0-only
"""Native roadmap UI acceptance with synthetic PDFs; no API or private library."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService


def check_layout(window, app):
    app.processEvents()
    page = window.stack.widget(0)
    assert window.inspector_scroll.horizontalScrollBar().maximum() == 0
    assert window.document_tree.horizontalScrollBar().maximum() == 0, (
        window.width(), window.document_tree.width(),
        [window.document_tree.columnWidth(i) for i in range(3)],
    )
    for control in (
        window.search, window.citations_button, window.saved_searches,
        window.save_search_button, window.fulltext_button,
    ):
        position = control.mapTo(page, control.rect().topRight())
        assert position.x() < page.width(), (control.text() if hasattr(control, 'text') else '', position)
    for card in window.score_cards.values():
        assert len(card.preview.text().splitlines()) <= 2
        assert card.preview.height() <= card.preview.fontMetrics().lineSpacing() * 2 + 4


def main():
    app = QApplication([])
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-layout-') as directory:
        root = Path(directory)
        service = LocalService(data_dir=root / 'data')
        window = Window(service)
        try:
            window.show()
            assert window.nav.count() == window.stack.count() == 5
            assert not window.score_detail_button.isEnabled()
            assert not window.read_button.isEnabled()
            for card in window.score_cards.values():
                assert '暂无' in card.preview.text() and 'MCP' in card.preview._source
                assert not card.expand_button.isVisible()
            window.resize(960, 720)
            check_layout(window, app)
            documents = []
            for index in range(2):
                source = root / f'{index}.pdf'
                with pymupdf.open() as pdf:
                    pdf.new_page().insert_text((60, 80), f'Unique local layout PDF {index}')
                    pdf.save(source)
                document = service.import_pdf(source)
                service.update_document(document['id'], {
                    'title': ('很长的文献标题' * 30) if index == 0 else 'Second local paper',
                })
                documents.append(document)
            rationale = '<literal> First rationale.\n' + '完整理由不能丢失，仍可逐条核对。' * 150 + '\nEND'
            service.set_scores(documents[0]['id'], [
                {'kind': 'paper', 'score': 80, 'rationale': rationale},
                {'kind': 'confidence', 'score': 90, 'rationale': rationale},
                {'kind': 'summary', 'score': None, 'rationale': '两位代理完成四节提炼。'},
            ])
            window.refresh()
            scored = next(window.document_tree.topLevelItem(i) for i in range(2)
                          if window.document_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)['id'] == documents[0]['id'])
            other = next(window.document_tree.topLevelItem(i) for i in range(2)
                         if window.document_tree.topLevelItem(i) is not scored)
            window.document_tree.setCurrentItem(scored)
            assert window.score_detail_button.isEnabled()
            assert not window.metadata_section.toggle.isChecked()
            card = window.score_cards['paper']
            for width, height in ((1440, 960), (960, 720)):
                window.resize(width, height)
                check_layout(window, app)
                assert '…' in card.preview.text()
                card.expand_button.click()
                assert card.full_text.isVisible()
                assert card.full_text.toPlainText() == rationale
                assert service.document_scores(documents[0]['id'])['paper']['rationale'] == rationale
                window.refresh()
                assert card.expand_button.isChecked()
                card.expand_button.click()
            # Disclosure retains the actual draft and rejects invalid writes.
            # 折叠保留真实草稿，非法保存不改变数据库。
            window.metadata_section.toggle.click()
            window.fields['date'].setText('2024-02-30')
            window.metadata_section.toggle.click()
            window.metadata_section.toggle.click()
            assert window.fields['date'].text() == '2024-02-30'
            with patch.object(QMessageBox, 'warning') as warning:
                window.save_doc()
                assert warning.called
            assert window.fields['date'].text() == '2024-02-30'
            assert service.document(documents[0]['id'])['date'] != '2024-02-30'
            window.fields['date'].setText('2024-02-29')
            window.fields['year'].setText('2024')
            window.save_doc()
            check_layout(window, app)
            window.resources_section.toggle.click()
            check_layout(window, app)
            window.metadata_section.set_expanded(False)
            window.resources_section.set_expanded(False)
            scored = next(window.document_tree.topLevelItem(i) for i in range(2)
                          if window.document_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)['id'] == documents[0]['id'])
            other = next(window.document_tree.topLevelItem(i) for i in range(2)
                         if window.document_tree.topLevelItem(i) is not scored)
            card.expand_button.click()
            window.document_tree.setCurrentItem(other)
            assert not card.expand_button.isChecked()
            assert not window.score_detail_button.isEnabled()
            assert not card.full_text.isVisible() and '暂无' in card.preview._source
            # The integrated branch owns a native PDF/HTML reader; its workflows
            # are verified by smoke_agent_workbench rather than an external browser.
            # 整合后 PDF/HTML 使用原生阅读，具体流程由 agent_workbench 冒烟检查。
            # Export starts with the library's multi-selection, then reuses the service.
            # 引用导出以库多选为初始范围，预览与文件继续共用服务格式器。
            scored.setSelected(True)
            other.setSelected(True)
            window.refresh()
            assert len(window.document_tree.selectedItems()) == 2
            window.citations_button.click()
            assert window.citation_dialog.isVisible() and len(window.citation_ids()) == 2
            for label in ('CSL-JSON', 'BibTeX', 'RIS'):
                window.citation_format.setCurrentText(label)
                preview = window.citation_preview.toPlainText()
                target = root / (label + '.txt')
                with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
                    window.export_citations()
                assert target.read_text(encoding='utf-8') == preview
            with patch.object(QFileDialog, 'getSaveFileName', return_value=('', '')), patch.object(service, 'export_metadata') as export:
                window.export_citations()
                export.assert_not_called()
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(root / 'denied'), '')), patch.object(service, 'export_metadata', side_effect=PermissionError()), patch.object(QMessageBox, 'warning') as warning:
                window.export_citations()
                assert warning.called
            window.citation_dialog.close()
            assert not window.citation_dialog.isVisible()
            # An empty filter must not retain another document's conclusion or export.
            window.search.setText('no matching title')
            window.citations_button.click()
            assert not window.citation_ids() and not window.citation_export_button.isEnabled()
            window.citation_dialog.close()
            assert not window.read_button.isEnabled()
            print('Native layout passed: empty state, long text, disclosure, drafts, selection, three-format export and failure paths; DPR=%g' % window.devicePixelRatioF())
        finally:
            window.close()
            service.close()


if __name__ == '__main__':
    main()
