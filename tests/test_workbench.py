# SPDX-License-Identifier: AGPL-3.0-only
"""Native service HTML ownership tests use controlled local child processes.
原生服务 HTML 归属测试使用受控本地子进程，不调用网络或模型 API。
"""
from pathlib import Path
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from polyscholar.service import LocalService
from polyscholar.store import LocalStore
from integrations.managed_process import run_captured


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.resources = self.root / 'resources'
        (self.resources / 'integrations').mkdir(parents=True)
        self.service = LocalService(self.root / 'library', resources_dir=self.resources)
        self.addCleanup(self.service.close)
        self.service.save_settings({'model': 'controlled-fixture', 'timeoutSeconds': 2})
        self.token = 'synthetic-workbench-credential-unique'
        self.service.set_session_key(self.token)
        source = self.root / 'source.pdf'
        source.write_bytes(b'%PDF-1.7 controlled workbench fixture')
        imported = self.service.import_pdf(source)
        self.document = self.service.update_document(imported['id'], {
            'title': 'arXiv test fixture', 'itemType': 'arxiv-preprint',
            'url': 'https://arxiv.org/abs/2401.12345',
        })
        self.completed = threading.Event()
        self.service.subscribe_jobs(lambda event: self.completed.set()
            if event['state'] in ('failed', 'completed') else None)

    def worker(self, body):
        header = '''import json, os, sys, time
request = json.loads(sys.stdin.buffer.read())
token = request['token']
assert token not in ' '.join(sys.argv)
assert all(token not in value for value in os.environ.values())
'''
        (self.resources / 'integrations/html_worker.py').write_text(header + body, encoding='utf-8')

    def join_job(self, job):
        with self.service._lock:
            thread = self.service._threads.get(job['id'])
        if thread:
            thread.join(timeout=6)
            self.assertFalse(thread.is_alive(), 'controlled child must finish within the test deadline')
        result = next(value for value in self.service.list_jobs() if value['id'] == job['id'])
        self.assertNotIn(job['id'], self.service._threads)
        self.assertNotIn(job['id'], self.service._html_cancellations)
        self.assertNotIn(job['id'], self.service._children)
        return result

    def test_whole_paper_language_and_identifier_validation_before_job(self):
        with self.assertRaises(ValueError):
            self.service.start_html_translation(self.document['id'], pages='1-2')
        self.service.save_settings({'targetLanguage': 'de'})
        with self.assertRaises(ValueError):
            self.service.start_html_translation(self.document['id'])
        self.service.save_settings({'targetLanguage': 'zh'})
        ordinary = self.service.create_bibliographic_item({'title': 'ordinary paper'})
        with self.assertRaises(ValueError):
            self.service.start_html_translation(ordinary['id'])
        self.assertEqual(self.service.list_jobs(), [])

    def test_html_url_workflow_accepts_fileless_arxiv_identity(self):
        self.worker("print(json.dumps({'ok':True,'html':'<html>fixture</html>','total':1,'translated':1}))\n")
        document = self.service.create_bibliographic_item({
            'title': 'fileless preprint', 'url': 'https://arxiv.org/abs/2401.12345',
            'itemType': 'arxiv-preprint',
        })
        result = self.join_job(self.service.start_html_translation(document['id']))
        self.assertEqual(result['state'], 'completed')

    def test_controlled_child_stdin_credentials_and_protected_html_export(self):
        self.worker("print(json.dumps({'ok':True,'html':'<html><body>译文</body></html>','total':3,'translated':2}))\n")
        job = self.service.start_html_translation(self.document['id'])
        result = self.join_job(job)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['fallbackBlocks'], 1)
        self.assertEqual(self.service.document_translations(self.document['id'])[0]['format'], 'html')
        destination = self.root / 'export.html'
        self.service.export_translation(job['id'], 0, destination)
        self.assertIn('译文', destination.read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            self.service.export_translation(job['id'], 0, self.service.store.root / 'private.html')
        # Scan only this disposable fixture; never inspect actual user data.
        # 只扫描本测试临时夹具，绝不读取真实用户文献或凭据。
        # Windows byte-range locks prevent reading the live instance lock.
        # Windows 字节锁阻止读取活动实例锁；关库后扫描完整目录，不跳过文件。
        self.service.close()
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(self.token.encode(), path.read_bytes(), str(path.relative_to(self.root)))

    def test_zero_translated_blocks_cannot_publish_success(self):
        self.worker("print(json.dumps({'ok':True,'html':'<html>original English</html>','total':3,'translated':0}))\n")
        result = self.join_job(self.service.start_html_translation(self.document['id']))
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(result['artifacts'])
        self.assertEqual(self.service.document_translations(self.document['id']), [])

    def observe_child(self, children, ready):
        def captured(*args, **kwargs):
            original = kwargs.get('on_spawn')
            def started(child):
                if original:
                    original(child)
                children.append(child)
                ready.set()
            kwargs['on_spawn'] = started
            return run_captured(*args, **kwargs)
        return captured

    def test_deadline_stops_actual_child_and_preserves_reopen(self):
        self.worker('time.sleep(30)\n')
        children, ready = [], threading.Event()
        with patch('integrations.managed_process.run_captured', side_effect=self.observe_child(children, ready)):
            job = self.service.start_html_translation(self.document['id'])
            self.assertTrue(ready.wait(3))
            result = self.join_job(job)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['errorCode'], 'timeout')
        self.assertIsNotNone(children[0].poll())
        self.service.close()
        reopened = LocalStore(self.service.store.root)
        try:
            self.assertEqual(reopened.document(self.document['id'])['title'], 'arXiv test fixture')
        finally:
            reopened.close()

    def test_close_stops_actual_child_and_releases_library(self):
        self.worker('time.sleep(30)\n')
        children, ready = [], threading.Event()
        with patch('integrations.managed_process.run_captured', side_effect=self.observe_child(children, ready)):
            self.service.start_html_translation(self.document['id'])
            self.assertTrue(ready.wait(3))
            started = time.monotonic()
            self.service.close()
            self.assertLess(time.monotonic() - started, 8)
        self.assertIsNotNone(children[0].poll())
        self.assertFalse(self.service._threads)
        self.assertFalse(self.service._children)
        reopened = LocalStore(self.service.store.root)
        reopened.close()

    def test_enriched_pdf_import_rolls_back_metadata_and_membership_failure(self):
        source = self.root / 'atomic.pdf'
        source.write_bytes(b'%PDF-1.7 atomic arxiv fixture')
        before = {document['id'] for document in self.service.list_documents()}
        before_objects = set(self.service.store.objects.iterdir())
        with self.assertRaises(ValueError):
            self.service.import_enriched_pdf(source, {'title': 'valid'}, 'missing-collection')
        self.assertEqual({document['id'] for document in self.service.list_documents()}, before)
        self.assertEqual(set(self.service.store.objects.iterdir()), before_objects)
        with self.assertRaises(ValueError):
            self.service.import_enriched_pdf(source, {'title': ''})
        self.assertEqual(set(self.service.store.objects.iterdir()), before_objects)
        first = self.service.import_enriched_pdf(source, {'title': 'Remote title'})
        self.service.update_document(first['id'], {'title': 'My local title'})
        repeated = self.service.import_enriched_pdf(source, {'title': 'Other remote title', 'doi': '10.1/fixture'})
        self.assertEqual(repeated['title'], 'My local title')
        self.assertEqual(repeated['doi'], '10.1/fixture')

    def test_audit_failure_does_not_leak_worker_ownership(self):
        self.worker("print(json.dumps({'ok':True,'html':'<html>fixture</html>','total':1,'translated':1}))\n")
        original = self.service.store.audit
        def reject_translation(point, *args):
            if point == 'translation_finished':
                raise OSError('controlled audit failure')
            return original(point, *args)
        failures = []
        with patch.object(self.service.store, 'audit', side_effect=reject_translation), \
             patch('threading.excepthook', side_effect=lambda event: failures.append(event.exc_type)):
            job = self.service.start_html_translation(self.document['id'])
            self.join_job(job)
        self.assertEqual(failures, [OSError])
        self.service.close()
        reopened = LocalStore(self.service.store.root)
        reopened.close()


if __name__ == '__main__':
    unittest.main()
