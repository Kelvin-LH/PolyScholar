# SPDX-License-Identifier: AGPL-3.0-only
import io
import json
from pathlib import Path
import tempfile
import sys
import time
import unittest
from unittest.mock import patch
from polyscholar.service import LocalService

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root / 'data', self.root / 'resources')

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_missing_runtime_never_uses_system_python(self):
        info = self.service.discover_engine('babeldoc')
        self.assertFalse(info['available'])
        self.assertEqual(info['pythonPath'], '')
        self.assertIn('内置', info['message'])
        self.assertFalse(self.service.discover_engine('unknown')['available'])

    def test_model_listing_uses_session_key_and_deduplicates_ids(self):
        self.service.set_session_key('private-key')
        self.service.save_settings({'endpoint': 'https://provider.test/v1'})
        response = io.BytesIO(json.dumps({'data': [{'id': 'b'}, {'id': 'a'}, {'id': 'a'}]}).encode())
        with patch('polyscholar.service.build_opener') as factory:
            factory.return_value.open.return_value = response
            self.assertEqual(self.service.list_models(), ['a', 'b'])
            request = factory.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://provider.test/v1/models')
            self.assertEqual(request.headers['Authorization'], 'Bearer private-key')
        with self.service.store.connection() as db:
            raw = db.execute('SELECT data FROM desktop_settings').fetchone()[0]
        self.assertNotIn('private-key', raw)

    def test_model_errors_are_sanitized_and_invalid_endpoint_has_no_request(self):
        self.service.set_session_key('private-key')
        with patch('polyscholar.service.build_opener') as factory:
            factory.return_value.open.side_effect = RuntimeError('private-key provider body')
            with self.assertRaises(ValueError) as caught:
                self.service.list_models()
            self.assertNotIn('private-key', str(caught.exception))
        with patch('polyscholar.service.build_opener') as factory:
            with self.assertRaises(ValueError):
                self.service.list_models('https://user:secret@provider.test')
            factory.assert_not_called()

    def test_metadata_export_is_exchange_format_and_escapes_newlines(self):
        source = self.root / 'source.pdf'
        source.write_bytes(b'%PDF-1.7 synthetic')
        doc = self.service.import_pdf(source)
        self.service.update_document(doc['id'], {'title': 'Test\nER  - malicious', 'authors': 'A; B', 'year': '2026', 'doi': '10.1/example'})
        path = self.root / 'reference.json'
        self.service.export_metadata(doc['id'], 'csl-json', path)
        item = json.loads(path.read_text())[0]
        self.assertEqual(len(item['author']), 2)
        self.assertEqual(item['issued']['date-parts'], [[2026]])
        other = self.root / 'other.pdf'
        other.write_bytes(b'%PDF-1.7 another')
        second = self.service.import_pdf(other)
        self.service.export_metadata([doc['id'], second['id'], doc['id']], 'csl-json', path)
        self.assertEqual(len(json.loads(path.read_text())), 2)
        self.service.export_metadata(doc['id'], 'ris', self.root / 'reference.ris')
        self.assertEqual((self.root / 'reference.ris').read_text().count('\nER  -'), 1)

    def test_background_job_runs_local_worker_and_persists_real_artifact(self):
        worker = self.service.resources / 'integrations/job_worker.py'
        worker.parent.mkdir(parents=True)
        worker.write_text("import json,sys,pathlib\nr=json.loads(sys.stdin.readline())\np=pathlib.Path(r['output']);p.mkdir()\n(p/'translated.pdf').write_bytes(b'%PDF-1.7 translated')\nprint(json.dumps(dict(protocol_version=1,job_id=r['job_id'],event='completed')))\n")
        source = self.root / 'job-source.pdf'
        source.write_bytes(b'%PDF-1.7 source')
        document = self.service.import_pdf(source)
        self.service.save_settings({'model': 'test-model'})
        self.service.set_session_key('never-persist-this-key')
        with patch.object(self.service, 'discover_engine', return_value={'available': True, 'pythonPath': sys.executable}):
            job = self.service.start_translation(document['id'])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.service.list_jobs()[0]['state'] in ('queued', 'running'):
            time.sleep(0.02)
        stored = self.service.list_jobs()[0]
        self.assertEqual(stored['state'], 'completed')
        self.assertEqual(self.service.read_artifact_pdf(job['id']), b'%PDF-1.7 translated')
        exported = self.root / 'translated-export.pdf'
        self.service.export_translation(job['id'], 0, exported)
        self.assertEqual(exported.read_bytes(), b'%PDF-1.7 translated')
        with self.service.store.connection() as db:
            data = str(list(db.execute('SELECT data FROM desktop_jobs'))) + str(list(db.execute('SELECT data FROM desktop_settings')))
        self.assertNotIn('never-persist-this-key', data)

    def test_protocol_error_becomes_sanitized_failed_job(self):
        worker = self.service.resources / 'integrations/job_worker.py'
        worker.parent.mkdir(parents=True)
        worker.write_text("import sys\nsys.stdin.readline()\nprint('secret provider body')\n")
        source = self.root / 'job-source.pdf'
        source.write_bytes(b'%PDF-1.7 source')
        document = self.service.import_pdf(source)
        self.service.save_settings({'model': 'test-model'})
        self.service.set_session_key('private-key')
        with patch.object(self.service, 'discover_engine', return_value={'available': True, 'pythonPath': sys.executable}):
            self.service.start_translation(document['id'])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.service.list_jobs()[0]['state'] in ('queued', 'running'):
            time.sleep(0.02)
        stored = self.service.list_jobs()[0]
        self.assertEqual(stored['state'], 'failed')
        self.assertNotIn('secret', stored['error'])
        self.assertNotIn('private-key', stored['error'])

if __name__ == '__main__':
    unittest.main()
