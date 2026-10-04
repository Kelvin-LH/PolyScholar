# SPDX-License-Identifier: AGPL-3.0-only
"""Native verification workflow with synthetic data; public API only with --live-github."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import pymupdf
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
from polyscholar.app import STYLE, Window
from polyscholar.service import LocalService
from polyscholar.ui.verification import VerificationDialog
from verification_fixtures import github_report, research_report, REPOSITORY
from code_review_fixtures import CodeFixture, code_report
from integrations.github_snapshot import GitHubClient


def wait(predicate):
    deadline = time.monotonic() + 10
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), 'Native verification operation did not finish'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-github', help='Explicit public repository check; sends only its identifier')
    args = parser.parse_args()
    app = QApplication([])
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='polyscholar-verification-ui-') as directory:
        root = Path(directory)
        source = root / 'synthetic.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((60, 80), 'Synthetic local external verification target.')
            pdf.save(source)
        resources = root / 'resources'
        worker = resources / 'integrations/github_snapshot.py'
        worker.parent.mkdir(parents=True)
        report = github_report()
        worker.write_text('import sys,json,time\nrequest=json.loads(sys.stdin.buffer.readline())\ntime.sleep(.15)\n'
                          + f"sys.stdout.buffer.write({json.dumps(report).encode()!r})\n", encoding='utf-8')
        service = LocalService(root / 'data', resources_dir=resources)
        document = service.import_pdf(source)
        service.set_scores(document['id'], [{'kind': 'confidence', 'score': 90, 'rationale': 'Synthetic textual evidence.'}])
        before = service.document_scores(document['id'])
        window = Window(service)
        try:
            window.resize(960, 720)
            window.show()
            window.document_tree.setCurrentItem(window.document_tree.topLevelItem(0))
            app.processEvents()
            assert '文本内置信度' in window.score_cards['confidence'].header.text()
            assert window.inspector_scroll.horizontalScrollBar().maximum() == 0
            window.verification_button.click()
            dialog = window.findChild(VerificationDialog)
            assert dialog and dialog.isVisible()
            dialog.repository.setText(REPOSITORY)
            dialog.check_button.click()
            worker_thread = window.io_worker
            assert worker_thread and not dialog.check_button.isEnabled()
            dialog.start_check()
            assert window.io_worker is worker_thread
            dialog.close()
            wait(lambda: window.io_worker is None)
            assert len(service.list_verifications(document['id'])) == 1
            window.open_verification()
            assert window.findChild(VerificationDialog) is dialog
            assert dialog.check_button.isEnabled() and dialog.history.count() == 1
            assert '提交 SHA' in dialog.browser.toPlainText()
            assert '未执行代码' in dialog.browser.toPlainText()
            research = research_report(document['sha256'])
            research['sources'][0]['excerpt'] += '\n<img src="https://evil.invalid/pixel"> External text.'
            research_file = root / 'research.json'
            research_file.write_text(json.dumps(research), encoding='utf-8')
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(research_file), '')):
                dialog.import_button.click()
                wait(lambda: window.io_worker is None)
            assert dialog.history.count() == 2
            assert '未独立核实' in dialog.browser.toPlainText()
            assert '不计算差值' in dialog.browser.toPlainText()
            assert '<img' in dialog.browser.toPlainText()
            target = root / 'verification.json'
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
                dialog.export_button.click()
            exported = json.loads(target.read_text(encoding='utf-8'))
            assert exported['report']['kind'] == 'research'
            assert service.document_scores(document['id']) == before
            # Reports written through MCP are displayed here, never start an agent.
            # MCP 写回的深入报告在此查看；窗口不承担 agent 调度。
            fixture = CodeFixture()
            fixture.write_worker(resources, root / 'code-requests.jsonl')
            base = service.store.save_verification(document['id'], GitHubClient(fixture).collect(REPOSITORY), document['sha256'])
            session = service.begin_code_review(document['id'], base['id'], depth='deep', authorized=True)
            service.read_code(document['id'], session['session_id'], 'train.py', 1, 2)
            code = code_report(session)
            code['findings'][0]['code_evidence'][0].update(start_line=1, end_line=2)
            code['findings'][0]['claim'] += ' <script>untrusted</script>'
            code_file = root / 'code.json'
            code_file.write_text(json.dumps(code), encoding='utf-8')
            saved = service.import_code_review(document['id'], code_file)
            dialog.refresh(saved['id'])
            app.processEvents()
            assert '深入代码核验' in dialog.history.currentText()
            assert 'agent 判断' in dialog.browser.toPlainText()
            assert '<script>untrusted</script>' in dialog.browser.toPlainText()
            assert '<img src=' in dialog.browser.toPlainText()
            assert 'seed = 42' in dialog.browser.toPlainText()
            assert service.document_scores(document['id']) == before
            # Actual process cancellation on parent close, without waiting for the budget.
            # 关闭主窗口时实际终止网络进程，不等待 60 秒预算耗尽。
            marker = root / 'worker-running'
            worker.write_text('import sys,time,json\njson.loads(sys.stdin.buffer.readline())\n'
                              + f"open({str(marker)!r},'w').write('started')\ntime.sleep(30)\n", encoding='utf-8')
            dialog.check_button.click()
            wait(marker.is_file)
            start = time.monotonic()
            window.close()
            wait(lambda: window._closed)
            assert time.monotonic() - start < 5
            assert not service._children
        finally:
            window.close()
            service.close()
        if args.live_github:
            live = LocalService(root / 'live-data', resources_dir=ROOT)
            try:
                target = live.import_pdf(source)
                receipt = live.verify_github(target['id'], args.live_github, authorized=True)
                report = live.verification_report(target['id'], receipt['id'])['report']
                print('Public GitHub acceptance:', json.dumps({
                    'status': report['status'], 'error_code': report['error_code'],
                    'snapshot': report['snapshot'], 'readme_verified': report['readme'] is not None,
                }, ensure_ascii=False), flush=True)
                assert report['status'] == 'checked', 'Public API acceptance remains open'
            finally:
                live.close()
    print('Native verification: snapshots, independent scores, import/export, escaped external text, history and worker close passed')


if __name__ == '__main__':
    main()
