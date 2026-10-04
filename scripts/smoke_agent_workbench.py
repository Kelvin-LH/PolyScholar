# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic native review/arXiv/HTML checks; never execute API calls.

合成原生界面检查，网络边界使用测试替身，不等同真实引擎或API验收。
"""
from pathlib import Path
import argparse
import sys, tempfile, json, threading
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QScrollArea
from PySide6.QtGui import QPdfWriter, QPainter
from PySide6.QtTest import QTest
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService
from polyscholar.ui.scores import _render_kind
from scripts.native_wait import wait_for

def main():
    parser = argparse.ArgumentParser(description='Synthetic native Agent workbench checks; no network/API.')
    parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    malformed = _render_kind('paper', {'detail': {'dimensions': [{'key': ['synthetic'], 'score': 'missing'}]}})
    assert 'synthetic' in malformed and '—' in malformed
    assert '&lt;img' in _render_kind('paper', {'rationale': '<img src=remote>'})
    app = QApplication([])
    app.setStyleSheet(STYLE)

    def capture(widget, name):
        if args.screenshots is not None:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            assert widget.grab().save(str(args.screenshots / name))
    with tempfile.TemporaryDirectory(prefix='polyscholar-agent-ui-') as temporary:
        root = Path(temporary)
        pdf = root / 'synthetic.pdf'
        writer = QPdfWriter(str(pdf))
        painter = QPainter(writer)
        painter.drawText(100, 100, 'Synthetic local agent review fixture')
        painter.end()
        del painter, writer
        service = LocalService(data_dir=root / 'data', resources_dir=root / 'resources')
        document = service.import_pdf(pdf)
        service.update_document(document['id'], dict(title='Efficient Reading with Local Research Tools', year='2025', itemType='arxiv-preprint', url='https://arxiv.org/abs/2312.04567', tags=['待读', '科研工具'], authors='Alex Chen; Morgan Lee', abstract='A synthetic paper illustrating local literature management and source-backed reading.', notes='本机合成示例：重点复核评阅中的证据出处与实验局限。'))
        other = service.create_bibliographic_item(dict(title='Reproducible Workflows for Academic PDFs', itemType='article-journal', year='2024'))
        empty = service.create_bibliographic_item(dict(title='待补充文献 · 本地阅读清单', itemType='book'))
        detail = dict(rubric_version='示例评阅规则 1.0', dimensions=[dict(key='rigor', score=19, max=25, rationale='报告记录了实验设置；独立重复验证尚缺。')], strengths=[dict(point='研究过程有可追溯记录。', evidence=['方法，第 2 节'])], weaknesses=[dict(point='仅在小规模样例上评估，泛化仍需验证。', evidence=['结果，第 4 节'])], aggregation=dict(method='median', spread=6, unresolved_disagreement=True), sub_scores=[dict(agent_id='评阅 A', model='示例模型', total=84, original_output=dict(rationale='优先核查样本覆盖范围。'))])
        service.set_scores(document['id'], [dict(kind='paper', score=84, rationale='流程清晰、复现信息较完整，实验覆盖范围仍有限。', detail=json.dumps(detail, ensure_ascii=False)), dict(kind='confidence', score=72, rationale='报告指出证据主要来自单组实验；应进一步核查原始材料。', detail=json.dumps(dict(dimensions=[dict(key='traceability', score=18, max=25, rationale='主要判断给出章节出处。')]), ensure_ascii=False)), dict(kind='summary', score=None, rationale='研究本地文献阅读、整理与证据记录。', detail=json.dumps(dict(problem=[dict(text='减少文献整理与阅读间的切换。', agents=['评阅 A'], evidence='引言，第 1 节')]), ensure_ascii=False))])
        service.set_scores(other['id'], [dict(kind='paper', score=91, rationale='合成排序样例。')])
        window = None
        try:
            window = Window(service)
            window.resize(1024, 700)
            window.show()
            app.processEvents()
            assert window.width() == 1024, (window.width(), window.minimumSizeHint().width())
            assert window.nav.count() == window.stack.count() == 5 and window.nav.item(3).text() == '证据摘要'
            window.activateWindow()
            app.processEvents()
            QTest.keyClick(window, Qt.Key.Key_5, Qt.KeyboardModifier.ControlModifier)
            app.processEvents()
            assert window.nav.currentRow() == 4
            window.find_action.trigger()
            app.processEvents()
            assert window.nav.currentRow() == 0 and window.search.hasFocus()
            with patch.object(QFileDialog, 'getOpenFileNames', return_value=([], '')) as opened:
                window.open_action.trigger()
                assert opened.call_count == 1
            assert all((hasattr(window, name) for name in ('open_zotero_migration', 'open_citation_import', 'open_import_export', 'open_trash', 'open_duplicates', 'new_bibliographic')))
            for (order, values) in [(Qt.SortOrder.AscendingOrder, [84, 91, None]), (Qt.SortOrder.DescendingOrder, [91, 84, None])]:
                window.document_tree.sortItems(1, order)
                app.processEvents()
                assert [window.document_list.item(i).data(1, Qt.ItemDataRole.UserRole) for i in range(3)] == values
            for row in range(window.document_list.count()):
                if window.document_list.item(row).data(Qt.ItemDataRole.UserRole)['id'] == document['id']:
                    window.document_list.setCurrentRow(row)
            assert window.score_headers['paper'].text().endswith('84 / 100')
            assert window.abstract.toPlainText().startswith('A synthetic paper')
            window.fields['title'].setText('Efficient Reading with Local Research Tools')
            window.save_doc()
            assert service.store.document(document['id'])['notes'].startswith('本机合成示例')
            app.processEvents()
            capture(window, 'library-1024.png')
            window.resize(1440, 960)
            app.processEvents()
            capture(window, 'library-python.png')
            window.open_score_detail()
            dialog = window.score_detail_dialog
            app.processEvents()
            assert dialog.tabs.currentIndex() == 0 and '报告评分' in dialog.tabs.widget(0).toPlainText()
            assert '原始记录' == dialog.tabs.tabText(dialog.tabs.count() - 1)
            capture(dialog, 'score-detail-python.png')
            exported = root / 'review.json'
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(exported), '')):
                dialog._export()
                wait_for(lambda : window.io_worker is None and (not dialog._busy))
            assert json.loads(exported.read_text(encoding='utf-8'))['paper']['score'] == 84 and '已导出' in dialog.status.text()
            protected = service.store.root / 'library.sqlite3'
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(protected), '')):
                dialog._export()
                wait_for(lambda : window.io_worker is None and (not dialog._busy))
            assert '已导出' not in dialog.status.text()
            assert service.document_scores(document['id'])['paper']['score'] == 84
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            app.processEvents()
            assert dialog._closed
            window.nav.setCurrentRow(window.nav.count()-1)
            app.processEvents()
            capture(window, 'settings-python.png')
            window.nav.setCurrentRow(0)
            window.open_arxiv()
            arxiv = window.arxiv_dialog
            arxiv.input.setPlainText('2312.04567')
            entry = dict(identifier='2312.04567', title='Synthetic arXiv entry', authors=['Synthetic Author'], date='2025-01-01', abstract='Preview fixture', url='https://arxiv.org/abs/2312.04567', pdf_url='https://arxiv.org/pdf/2312.04567', doi='')
            with patch.object(service, 'arxiv_lookup', return_value=[entry]):
                arxiv.start_fetch()
                wait_for(lambda : window.io_worker is None and (not arxiv._busy))
            assert arxiv.preview.count() == 1 and arxiv.import_button.isEnabled()
            with patch.object(service, 'arxiv_import', return_value=dict(imported=[document], errors=[])):
                arxiv.start_import()
                wait_for(lambda : window.io_worker is None and (not arxiv._busy))
            assert '已导入 1 篇' in arxiv.status.text() and (not arxiv.import_button.isEnabled())
            with patch.object(service, 'arxiv_lookup', side_effect=ValueError('合成网络失败，请重试。')):
                arxiv.start_fetch()
                wait_for(lambda : window.io_worker is None and (not arxiv._busy))
            assert arxiv.preview.count() == 0 and arxiv.fetch_button.isEnabled() and ('合成网络失败' in arxiv.status.text())
            arxiv.close()
            job = service.store.new_job(document['id'], 'html-llm')
            output = Path(job['outputDir'])
            output.mkdir(parents=True)
            html = '<html><body><h1>合成 HTML 译文</h1><p>本机阅读验证。</p></body></html>'
            (output / 'translated.html').write_text(html, encoding='utf-8')
            job.update(state='completed', artifacts=['translated.html'])
            service.store.put_job(job)
            window.refresh()
            window.open_original()
            wait_for(lambda : window.io_worker is None)
            window.reader_result.setCurrentIndex(next((index for index in range(window.reader_result.count()) if window.reader_result.itemData(index) == (job['id'], 0))))
            wait_for(lambda : window.io_worker is None)
            assert window.translated_stack.currentIndex() == 1 and '合成 HTML 译文' in window.html_view.toPlainText()
            target = root / 'exported.html'
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
                window.export_translation(job)
            assert target.read_text(encoding='utf-8') == html
            # A genuine local HTML artifact is readable without inventing a PDF.
            # 无 PDF 条目读取实际 HTML 产物，不创建或冒用 PDF 身份。
            fileless = service.create_bibliographic_item({
                'title': 'Synthetic fileless arXiv record', 'itemType': 'arxiv-preprint',
                'url': 'https://arxiv.org/abs/2401.01234',
            })
            window.refresh()
            row = next(index for index in range(window.document_list.count())
                       if window.document_list.item(index).data(Qt.ItemDataRole.UserRole)['id'] == fileless['id'])
            window.document_list.setCurrentRow(row)
            assert not window.read_button.isEnabled()
            fileless_job = service.store.new_job(fileless['id'], 'html-llm')
            fileless_output = Path(fileless_job['outputDir'])
            fileless_output.mkdir(parents=True)
            fileless_html = '<html><body><h1>无 PDF 的本地 HTML 译文</h1><p>合成阅读验证。</p></body></html>'
            (fileless_output / 'fileless.html').write_text(fileless_html, encoding='utf-8')
            fileless_job.update(state='completed', artifacts=['fileless.html'])
            service.store.put_job(fileless_job)
            service._notify_job(fileless_job)
            wait_for(lambda: window.read_button.isEnabled())
            assert service.primary_pdf_id(fileless['id']) is None
            assert window.read_button.isEnabled() and window.read_button.text() == '阅读HTML译文'
            window.open_original()
            wait_for(lambda: window.io_worker is None)
            assert window.translated_stack.currentIndex() == 1
            assert '无 PDF 的本地 HTML 译文' in window.html_view.toPlainText()
            assert window.pdf_docs[0].pageCount() == 0
            fileless_export = root / 'fileless-export.html'
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(fileless_export), '')):
                window.export_translation(fileless_job)
            assert fileless_export.read_text(encoding='utf-8') == fileless_html
            window.pages.setText('1-2')
            window.task_mode.setCurrentIndex(window.task_mode.findData('html-llm'))
            assert not window.pages.isEnabled() and (not window.pages.text())
            window.open_arxiv()
            arxiv = window.arxiv_dialog
            arxiv.input.setPlainText('2312.04567')
            release = threading.Event()

            def delayed(_):
                assert release.wait(5)
                return [entry]
            try:
                with patch.object(service, 'arxiv_lookup', side_effect=delayed):
                    arxiv.start_fetch()
                    assert window.io_worker is not None
                    QTest.keyClick(arxiv, Qt.Key.Key_Escape)
                    app.processEvents()
                    assert arxiv._closed
                    window.close()
                    assert not window._closed
                    release.set()
                    wait_for(lambda : window._closed)
            finally:
                release.set()
                window.close()
                service.close()
        finally:
            if window is not None:
                window.close()
                wait_for(lambda : window._closed, timeout=10)
            service.close()
    print('Synthetic native scores, sorting, protected export, arXiv preview/retry/Escape/managed close, retained navigation and native HTML read/export with or without a PDF passed; no network/API.')
if __name__ == '__main__':
    main()
