# SPDX-License-Identifier: AGPL-3.0-only
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from polyscholar.service import LocalService
from polyscholar.store import LocalStore

class TrashTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=LocalService(self.root/'data');self.store=self.service.store
        self.original=self.root/'original.pdf';self.original.write_bytes(b'%PDF-1.7 root trash')
        self.parent=self.service.import_pdf(self.original)
        self.child_source=self.root/'child.pdf';self.child_source.write_bytes(b'%PDF-1.7 child trash')
        self.child=self.service.import_attachment(self.parent['id'],self.child_source)
    def tearDown(self):self.service.close();self.temp.cleanup()
    def complete_job(self,identifier):
        job=self.store.new_job(identifier,'babeldoc');output=Path(job['outputDir']);output.mkdir(parents=True)
        (output/'result.pdf').write_bytes(b'%PDF-1.7 result')
        job.update(state='completed',artifacts=['result.pdf']);self.store.put_job(job)
        return job

    def test_restore_keeps_relationships_notes_claims_jobs_artifacts_and_originals(self):
        collection=self.service.create_collection('Keep');self.service.set_membership(self.parent['id'],collection['id'])
        self.service.update_document(self.parent['id'],dict(tags=['Protected'],notes='Private note'))
        self.store.replace_document_ir(self.parent['id'],'fixture',[dict(number=1,width=100,height=100,blocks=[dict(text='Source evidence',bbox=[0,0,1,1],order=0)])])
        block=self.service.document_blocks(self.parent['id'])[0]
        claim=self.service.save_claim(self.parent['id'],'Note',[dict(blockId=block['id'],quote='Source')])
        job=self.complete_job(self.child['id'])
        self.service.delete_document(self.parent['id'])
        self.assertEqual(self.service.list_documents(),[]);self.assertEqual(self.service.list_jobs(),[])
        self.assertEqual(self.service.list_tags(),[]);self.assertEqual(self.service.list_collections()[0]['count'],0)
        self.assertEqual(self.service.search_fulltext('Source')['total'],0)
        self.assertEqual(len(self.store.list_documents()),2);self.assertEqual(len(self.store.list_jobs()),1)
        self.service.rename_tag('Protected','Changed')
        self.assertEqual(self.store.document(self.parent['id'])['tags'],['Protected'])
        self.service.restore_document(self.parent['id'])
        self.assertEqual(self.service.list_claims(self.parent['id'])[0]['id'],claim['id'])
        self.assertEqual(self.service.read_artifact_pdf(job['id']),b'%PDF-1.7 result')
        self.assertEqual(self.service.document_collections(self.parent['id']),[collection['id']])
        self.assertEqual(self.service.search_fulltext('Source')['total'],1)
        self.assertTrue(self.original.exists());self.assertTrue(self.child_source.exists())
        self.service.delete_document(self.parent['id']);self.service.delete_collection(collection['id']);self.service.restore_document(self.parent['id'])
        self.assertEqual(self.service.document_collections(self.parent['id']),[])

    def test_inactive_guards_reimport_and_independent_child_marker(self):
        self.service.trash_document(self.child['id']);self.service.trash_document(self.parent['id'])
        rows=self.service.list_trash();child=next(r for r in rows if r['id']==self.child['id'])
        self.assertTrue(child['restoreBlocked'])
        with self.assertRaises(ValueError):self.service.restore_document(self.child['id'])
        for identifier,source in ((self.parent['id'],self.original),(self.child['id'],self.child_source)):
            for operation in (lambda:self.service.read_pdf(identifier),lambda:self.service.update_document(identifier,dict(notes='changed')),
                              lambda:self.service.parse_document(identifier),lambda:self.service.summarize_document(identifier,[]),
                              lambda:self.service.format_metadata(identifier,'ris'),lambda:self.store.new_job(identifier,'babeldoc'),
                              lambda:self.service.import_pdf(source),lambda:self.service.clear_fulltext_index(identifier),
                              lambda:self.service.rebuild_fulltext_index(identifier)):
                with self.assertRaises(ValueError):operation()
        self.service.restore_document(self.parent['id'])
        self.assertEqual(len(self.service.list_attachments(self.parent['id'])),1)
        self.assertEqual(self.service.deletion_preview(self.child['id'])['counts']['pdfs'],1)
        self.service.restore_document(self.child['id'])
        self.assertEqual(len(self.service.list_attachments(self.parent['id'])),2)

    def test_cleanup_failure_persists_restart_retry_and_keeps_external_sources(self):
        job=self.complete_job(self.child['id']);self.service.delete_document(self.parent['id'])
        preview=self.service.deletion_preview(self.parent['id'])
        self.assertEqual(preview['counts']['pdfs'],2);self.assertEqual(preview['counts']['jobs'],1)
        with patch.object(self.store,'_remove_managed_path',side_effect=PermissionError('private raw path')):
            result=self.service.purge_document(self.parent['id'])
        self.assertFalse(result['cleanupComplete']);self.assertEqual(result['remainingCleanup'],3)
        self.assertEqual(self.service.list_trash(),[])
        self.assertEqual(self.service.list_pending_cleanup()[0]['errorCode'],'cleanup_failed')
        self.service.close();self.service=LocalService(self.root/'data');self.store=self.service.store
        retry=self.service.retry_cleanup()
        self.assertTrue(retry['cleanupComplete']);self.assertEqual(retry['remainingCleanup'],0)
        self.assertEqual(self.service.list_pending_cleanup(),[])
        self.assertFalse(Path(job['outputDir']).exists())
        self.assertTrue(self.original.exists());self.assertTrue(self.child_source.exists())

    def test_db_audit_failure_never_unlinks_files_and_operations_roll_back(self):
        object_path=self.store.object_path(self.parent)
        with self.store.connection() as db:
            db.execute("CREATE TRIGGER fail_trash BEFORE INSERT ON desktop_audit WHEN NEW.point='document_trashed' BEGIN SELECT RAISE(ABORT,'failure'); END")
        with self.assertRaises(sqlite3.Error):self.service.trash_document(self.parent['id'])
        self.assertEqual(self.service.list_trash(),[])
        with self.store.connection() as db:db.execute('DROP TRIGGER fail_trash')
        self.service.trash_document(self.parent['id'])
        with self.store.connection() as db:
            db.execute("CREATE TRIGGER fail_purge BEFORE INSERT ON desktop_audit WHEN NEW.point='document_deleted' BEGIN SELECT RAISE(ABORT,'failure'); END")
        with patch.object(self.store,'_remove_managed_path') as remove:
            with self.assertRaises(sqlite3.Error):self.service.purge_document(self.parent['id'])
            remove.assert_not_called()
        self.assertTrue(object_path.exists());self.assertEqual(len(self.service.list_trash()),1)
        self.assertEqual(self.service.list_pending_cleanup(),[])
        self.assertEqual(len(self.store.list_documents()),2)

    def test_retry_refuses_replaced_jobs_parent_and_preserves_replacement(self):
        job=self.complete_job(self.parent['id']);output=Path(job['outputDir'])
        self.service.trash_document(self.parent['id'])
        with patch.object(self.store,'_remove_managed_path',side_effect=PermissionError()):
            result=self.service.purge_document(self.parent['id'])
        old_jobs=output.parent.with_name('jobs-old');output.parent.rename(old_jobs)
        output.mkdir(parents=True);replacement=output/'replacement.pdf';replacement.write_bytes(b'%PDF-1.7 replacement')
        retry=self.service.retry_cleanup(result['cleanupId'])
        self.assertFalse(retry['cleanupComplete']);self.assertTrue(replacement.exists())
        shutil.rmtree(output.parent);old_jobs.rename(output.parent)
        self.assertTrue(self.service.retry_cleanup()['cleanupComplete'])

    def test_active_family_translation_and_live_parser_block_trash(self):
        job=self.store.new_job(self.child['id'],'babeldoc')
        with self.assertRaises(ValueError):self.service.trash_document(self.parent['id'])
        job['state']='failed';self.store.put_job(job)
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'])
        self.service._children['live-parser']=child;self.service._child_documents['live-parser']=self.child['id']
        try:
            with self.assertRaises(ValueError):self.service.trash_document(self.parent['id'])
        finally:
            child.terminate();child.wait(timeout=5)
            self.service._children.pop('live-parser');self.service._child_documents.pop('live-parser')
        self.service.trash_document(self.parent['id'])

    def test_v8_migration_failure_preserves_version_and_recovery(self):
        self.service.close();path=self.root/'data'/'library.sqlite3'
        with closing(sqlite3.connect(path)) as db:db.executescript('DROP TABLE desktop_trash;DROP TABLE desktop_purge_cleanup;PRAGMA user_version=8;')
        with patch('polyscholar.store.TRASH_SCHEMA','CREATE TABLE invalid('):
            with self.assertRaises(sqlite3.Error):LocalStore(self.root/'data')
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_documents').fetchone()[0],2)
        with closing(sqlite3.connect(self.root/'data'/'library-before-v9.sqlite3')) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8)
        self.service=LocalService(self.root/'data');self.store=self.service.store
        self.assertEqual(self.service.list_trash(),[])
        self.assertEqual(len(self.service.list_attachments(self.parent['id'])),2)

if __name__=='__main__':unittest.main()
