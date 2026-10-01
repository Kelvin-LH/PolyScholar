# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
from pathlib import Path
import tempfile
import unittest
from polyscholar.store import LocalStore, SETTINGS

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = LocalStore(self.root / 'library')
        self.source = self.root / 'source.pdf'
        self.source.write_bytes(b'%PDF-1.7\n synthetic original')
        self.document = self.store.import_pdf(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def completed_job(self):
        job = self.store.new_job(self.document['id'], 'babeldoc')
        output = Path(job['outputDir'])
        output.mkdir(parents=True)
        (output / 'translated.pdf').write_bytes(b'%PDF-1.7\n translated')
        job.update(state='completed', artifacts=['translated.pdf'])
        self.store.put_job(job)
        return job

    def test_import_deduplicates_and_delete_preserves_external_source(self):
        self.assertEqual(self.store.import_pdf(self.source)['id'], self.document['id'])
        self.assertEqual(len(self.store.list_documents()), 1)
        original = self.store.object_path(self.document)
        self.assertFalse(original.stat().st_mode & 0o200)
        self.store.update_document(self.document['id'], {'title': 'Updated', 'notes': 'private'})
        self.assertEqual(self.store.document(self.document['id'])['title'], 'Updated')
        self.store.delete_document(self.document['id'])
        self.assertTrue(self.source.exists())
        self.assertFalse(original.exists())

    def test_settings_reject_secrets_and_invalid_endpoint(self):
        for endpoint in ('https://provider.test/?api_key=secret', 'https://user:secret@provider.test',
                         'https://provider.test/#secret', 'http://provider.test'):
            with self.assertRaises(ValueError):
                self.store.save_settings({'endpoint': endpoint})
        with self.assertRaises(ValueError):
            self.store.save_settings({'apiKey': 'secret'})
        settings = self.store.save_settings({'endpoint': 'http://127.0.0.1:8080/v1'})
        self.assertTrue(Path(settings['cachePath']).is_absolute())
        with self.store.connection() as db:
            raw = db.execute('SELECT data FROM desktop_settings').fetchone()[0]
        self.assertNotIn('secret', raw)
        self.assertEqual(self.store.get_settings()['model'], '')

    def test_cache_switch_preserves_existing_result_and_new_job_uses_new_cache(self):
        self.store.save_settings({'cachePath': str(self.root / 'cache-a')})
        first = self.completed_job()
        self.store.save_settings({'cachePath': str(self.root / 'cache-b')})
        second = self.store.new_job(self.document['id'], 'babeldoc')
        self.assertIn('cache-a', str(self.store.artifact_path(first['id'], 0)))
        self.assertIn('cache-b', second['outputDir'])
        for path in ('relative', str(self.store.objects)):
            with self.assertRaises(ValueError):
                self.store.prepare_cache(path)

    def test_export_confines_source_and_rejects_hardlinks_and_traversal(self):
        job = self.completed_job()
        artifact = self.store.artifact_path(job['id'], 0)
        exported = self.root / 'export.pdf'
        self.store.export_translation(job['id'], 0, exported)
        self.assertEqual(exported.read_bytes(), artifact.read_bytes())
        for target in (artifact, self.store.object_path(self.document), Path('relative.pdf')):
            with self.assertRaises(ValueError):
                self.store.export_translation(job['id'], 0, target)
        alias = self.root / 'alias.pdf'
        os.link(artifact, alias)
        with self.assertRaises(ValueError):
            self.store.export_translation(job['id'], 0, alias)
        job['artifacts'] = ['../outside.pdf']
        self.store.put_job(job)
        with self.assertRaises(ValueError):
            self.store.artifact_path(job['id'], 0)

    def test_single_job_gate_delete_guard_and_restart_recovery(self):
        self.store.new_job(self.document['id'], 'babeldoc')
        with self.assertRaises(ValueError):
            self.store.new_job(self.document['id'], 'pdfmathtranslate')
        with self.assertRaises(ValueError):
            self.store.delete_document(self.document['id'])
        reopened = LocalStore(self.store.root)
        self.assertEqual(reopened.list_jobs()[0]['state'], 'failed')
        reopened.delete_document(self.document['id'])

    def test_old_settings_without_cache_path_are_compatible(self):
        settings = dict(SETTINGS)
        settings.pop('cachePath')
        with self.store.connection() as db:
            db.execute('INSERT INTO desktop_settings VALUES(1,?)', (json.dumps(settings),))
        self.assertEqual(self.store.get_settings()['cachePath'], '')

if __name__ == '__main__':
    unittest.main()
