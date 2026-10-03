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

    def test_background_html_translation_persists_artifact(self):
        source = self.root / 'job-source.pdf'
        source.write_bytes(b'%PDF-1.7 source')
        document = self.service.import_pdf(source)
        self.service.update_document(document['id'], {'url': 'https://arxiv.org/abs/2312.04567'})
        self.service.save_settings({'model': 'test-model'})
        self.service.set_session_key('never-persist-this-key')
        page = '<html><body><p>original paragraph</p><p class="zh">translated paragraph</p></body></html>'
        captured = {}
        def fake_translate(identifier, endpoint, model, api_key, progress=None, on_partial=None, timeout=120, max_workers=4):
            captured.update(identifier=identifier, endpoint=endpoint, model=model)
            progress(1, 1)
            assert on_partial is not None, 'progressive preview callback must be wired'
            on_partial(page)
            return page, 1, 1
        # The patch must stay active while the background thread runs.
        with patch('polyscholar.service.html_translate.translate_paper', side_effect=fake_translate) as fake:
            job = self.service.start_translation(document['id'])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and self.service.list_jobs()[0]['state'] in ('queued', 'running'):
                time.sleep(0.02)
            self.assertTrue(fake.called)
        stored = self.service.list_jobs()[0]
        self.assertEqual(stored['state'], 'completed')
        self.assertEqual(stored['artifacts'], ['translated.html'])
        self.assertEqual(stored.get('progress'), {'current': 1, 'total': 1})
        self.assertEqual(captured['identifier'], '2312.04567')
        artifact_file = Path(stored['outputDir']) / 'translated.html'
        self.assertEqual(artifact_file.read_text(encoding='utf-8'), page)
        exported = self.root / 'translated-export.html'
        self.service.export_translation(job['id'], 0, exported)
        self.assertEqual(exported.read_text(encoding='utf-8'), page)
        self.assertTrue(self.service.job_output_dir(job['id']).is_dir())
        # The translation belongs to the paper: newest-first access from anywhere.
        translations = self.service.document_translations(document['id'])
        self.assertEqual(len(translations), 1)
        self.assertEqual(Path(translations[0]['path']).read_text(encoding='utf-8'), page)
        opened = self.service.open_translation(document['id'])
        self.assertEqual(Path(opened).read_text(encoding='utf-8'), page)
        with self.service.store.connection() as db:
            data = str(list(db.execute('SELECT data FROM desktop_jobs'))) + str(list(db.execute('SELECT data FROM desktop_settings')))
        self.assertNotIn('never-persist-this-key', data)

    def test_translation_requires_arxiv_link_and_sanitizes_failures(self):
        source = self.root / 'job-source.pdf'
        source.write_bytes(b'%PDF-1.7 source')
        document = self.service.import_pdf(source)
        self.service.save_settings({'model': 'test-model'})
        self.service.set_session_key('private-key')
        with self.assertRaisesRegex(ValueError, 'arXiv'):
            self.service.start_translation(document['id'])
        self.service.update_document(document['id'], {'url': 'https://arxiv.org/abs/2312.04567'})
        def explode(identifier, endpoint, model, api_key, progress=None, on_partial=None, timeout=120, max_workers=4):
            raise ValueError('secret endpoint rejected identifier 999')
        with patch('polyscholar.service.html_translate.translate_paper', side_effect=explode):
            self.service.start_translation(document['id'])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and self.service.list_jobs()[0]['state'] in ('queued', 'running'):
                time.sleep(0.02)
        stored = self.service.list_jobs()[0]
        self.assertEqual(stored['state'], 'failed')
        self.assertNotIn('999', stored['error'])
        self.assertNotIn('secret', stored['error'])

    def test_partial_preview_writer_throttles_and_writes(self):
        job = dict(id='job-x', outputDir=str(self.root / 'jobs' / 'job-x'))
        Path(job['outputDir']).mkdir(parents=True)
        import time as time_module
        with patch.object(time_module, 'monotonic', return_value=100.0):
            self.service._write_partial_html(job, '<html>v1</html>')
        self.assertEqual((Path(job['outputDir']) / 'translated.html').read_text(encoding='utf-8'), '<html>v1</html>')
        # Inside the 2s throttle window a second write is skipped.
        with patch.object(time_module, 'monotonic', return_value=101.0):
            self.service._write_partial_html(job, '<html>v2</html>')
        self.assertEqual((Path(job['outputDir']) / 'translated.html').read_text(encoding='utf-8'), '<html>v1</html>')
        with patch.object(time_module, 'monotonic', return_value=103.0):
            self.service._write_partial_html(job, '<html>v3</html>')
        self.assertEqual((Path(job['outputDir']) / 'translated.html').read_text(encoding='utf-8'), '<html>v3</html>')

    def test_translation_requires_arxiv_link_and_sanitizes_failures(self):
        source = self.root / 'job-source.pdf'
        source.write_bytes(b'%PDF-1.7 source')
        document = self.service.import_pdf(source)
        self.service.save_settings({'model': 'test-model'})
        self.service.set_session_key('private-key')
        with self.assertRaisesRegex(ValueError, 'arXiv'):
            self.service.start_translation(document['id'])
        self.service.update_document(document['id'], {'url': 'https://arxiv.org/abs/2312.04567'})
        def explode(identifier, endpoint, model, api_key, progress=None, timeout=120, max_workers=4):
            raise ValueError('secret endpoint rejected identifier 999')
        with patch('polyscholar.service.html_translate.translate_paper', side_effect=explode):
            self.service.start_translation(document['id'])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and self.service.list_jobs()[0]['state'] in ('queued', 'running'):
                time.sleep(0.02)
        stored = self.service.list_jobs()[0]
        self.assertEqual(stored['state'], 'failed')
        self.assertNotIn('999', stored['error'])
        self.assertNotIn('secret', stored['error'])

if __name__ == '__main__':
    unittest.main()
