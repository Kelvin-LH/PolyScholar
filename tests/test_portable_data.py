# SPDX-License-Identifier: AGPL-3.0-only
"""Portable data directory migration from the legacy AppData location."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from polyscholar import service as service_module
from polyscholar.service import LocalService

class PortableMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.legacy = self.base / 'legacy' / 'PolyScholar'
        self.portable = self.base / 'portable' / 'data'
        (self.legacy / 'objects').mkdir(parents=True)
        (self.legacy / 'cache' / 'jobs').mkdir(parents=True)
        (self.legacy / 'cache' / 'jobs' / 'old.pdf').write_bytes(b'%PDF-1.4 old artifact')

    def _service(self):
        with patch.object(service_module, 'app_data_dir', return_value=self.portable), \
             patch.object(service_module, 'legacy_app_data_dir', return_value=self.legacy):
            return LocalService()

    def test_first_run_migrates_legacy_library_and_keeps_backup(self):
        legacy_service = LocalService(self.legacy)
        self.addCleanup(legacy_service.close)
        source = self.base / 'source.pdf'
        source.write_bytes(b'%PDF-1.7 portable migration sample')
        document = legacy_service.import_pdf(source)
        # A completed job whose artifact lives under the legacy cache must stay
        # readable from the portable directory after migration.
        job = legacy_service.store.new_job(document['id'], 'babeldoc')
        output = Path(job['outputDir'])
        output.mkdir(parents=True)
        (output / 'old-translated.pdf').write_bytes(b'%PDF-1.7 old translation')
        job.update(state='completed', artifacts=['old-translated.pdf'])
        legacy_service.store.put_job(job)
        legacy_service.close()
        # A stale WAL/SHM pair from the closed legacy app must be copied too.
        (self.legacy / 'library.sqlite3-wal').write_bytes(b'')

        service = self._service()
        self.addCleanup(service.close)
        migrated = service.list_documents()
        self.assertEqual([d['id'] for d in migrated], [document['id']])
        self.assertEqual(service.read_pdf(document['id'])[:5], b'%PDF-')
        # Job record rewritten to the portable cache; artifact bytes identical.
        migrated_job = service.list_jobs()[0]
        self.assertTrue(migrated_job['outputDir'].startswith(str(self.portable)))
        self.assertEqual(service.read_artifact_pdf(migrated_job['id'], 0), b'%PDF-1.7 old translation')
        # Legacy directory remains as an untouched backup.
        self.assertTrue((self.legacy / 'library.sqlite3').is_file())
        self.assertTrue((self.legacy / 'objects').is_dir())

    def test_existing_portable_data_is_never_overwritten(self):
        service = self._service()
        source = self.base / 'new.pdf'
        source.write_bytes(b'%PDF-1.7 created after migration')
        service.import_pdf(source)
        marker = service.list_documents()[0]['id']
        service.close()
        # A second launch with the legacy directory still present keeps portable data.
        service2 = self._service()
        self.addCleanup(service2.close)
        self.assertEqual([d['id'] for d in service2.list_documents()], [marker])
        self.assertEqual(len(service2.list_documents()), 1)

    def test_no_legacy_directory_creates_fresh_portable_data(self):
        import shutil
        shutil.rmtree(self.legacy)
        service = self._service()
        self.addCleanup(service.close)
        self.assertEqual(service.list_documents(), [])
        self.assertTrue(self.portable.is_dir())

if __name__ == '__main__':
    unittest.main()
