# SPDX-License-Identifier: AGPL-3.0-only
"""Managed attachment families, atomic import, migration and source protection."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from polyscholar.service import LocalService
from polyscholar.store import LocalStore


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root / 'data')
        self.addCleanup(self.service.close)
        self.store = self.service.store
        self.original = self.pdf('main.pdf', b'original')
        self.parent = self.service.import_pdf(self.original)

    def pdf(self, name, text):
        path = self.root / name
        path.write_bytes(b'%PDF-1.7 ' + text)
        return path

    def test_managed_children_have_distinct_identity_and_root_only_library(self):
        supplement = self.pdf('extra.pdf', b'supplement')
        child = self.service.import_attachment(self.parent['id'], supplement)
        self.assertNotEqual(child['id'], self.parent['id'])
        self.assertEqual(child['documentId'], child['id'])
        self.assertEqual(child['parentDocumentId'], self.parent['id'])
        self.assertEqual(child['role'], 'supplement')
        self.assertEqual(self.store.document(child['id'])['parentDocumentId'], self.parent['id'])
        self.assertEqual(len(self.store.list_documents()), 2)
        self.assertEqual([row['id'] for row in self.service.list_documents()], [self.parent['id']])
        self.assertEqual([row['id'] for row in self.service.search_documents()], [self.parent['id']])
        self.assertEqual(self.service.read_pdf(child['id']), supplement.read_bytes())
        self.assertEqual([row['role'] for row in self.service.list_attachments(self.parent['id'])], ['original', 'supplement'])
        duplicate = self.pdf('copy.pdf', b'supplement')
        repeated = self.service.import_attachment(self.parent['id'], duplicate, 'translation')
        self.assertEqual(repeated['id'], child['id'])
        self.assertEqual(repeated['role'], 'supplement')
        self.assertIn(str(duplicate.resolve()), self.store.document(child['id'])['sourcePaths'])
        self.assertEqual(len(self.store.list_documents()), 2)
        ordinary = self.service.import_pdf(duplicate)
        self.assertEqual(ordinary['id'], self.parent['id'])
        collection = self.store.create_collection('Root collection')
        with self.assertRaises(ValueError):
            self.store.set_membership(child['id'], collection['id'])
        self.store.set_membership(ordinary['id'], collection['id'])
        self.assertEqual(self.store.list_collections()[0]['count'], 1)
        self.assertEqual(len(self.service.search_documents(collection_id=collection['id'])), 1)

    def test_cross_parent_dedup_and_child_parent_cycle_refused(self):
        child = self.service.import_attachment(self.parent['id'], self.pdf('child.pdf', b'child'))
        other = self.service.import_pdf(self.pdf('other.pdf', b'other'))
        for parent, path in [(other['id'], self.root/'child.pdf'), (self.parent['id'], self.root/'other.pdf'),
                             (self.parent['id'], self.original), (child['id'], self.pdf('nested.pdf', b'nested'))]:
            with self.assertRaises(ValueError):
                self.service.import_attachment(parent, path)
        self.assertEqual(len(self.store.list_documents()), 3)
        with self.store.connection() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO desktop_attachment_links VALUES(?,?,?,?)',
                           (child['id'], other['id'], 'supplement', 'illegal child parent'))
        with self.assertRaises(ValueError):
            self.service.delete_document(child['id'])
        with self.assertRaises(ValueError):
            self.service.delete_attachment(other['id'], child['id'])
        with self.assertRaises(ValueError):
            self.service.delete_attachment(self.parent['id'], self.parent['id'])

    def test_link_failure_rolls_back_document_audit_and_new_object(self):
        before = len(self.store.list_documents())
        with self.store.connection() as db:
            audits = db.execute('SELECT COUNT(*) FROM desktop_audit').fetchone()[0]
            db.execute("CREATE TRIGGER reject_attachment BEFORE INSERT ON desktop_attachment_links BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.import_attachment(self.parent['id'], self.pdf('failed.pdf', b'failed'))
        self.assertEqual(len(self.store.list_documents()), before)
        self.assertEqual(len(list(self.store.objects.glob('*.pdf'))), before)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_audit').fetchone()[0], audits)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_attachment_links').fetchone()[0], 0)
        self.assertTrue((self.root/'failed.pdf').exists())

    def test_failed_object_write_does_not_publish_document_or_link(self):
        with patch('polyscholar.store.os.fsync', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                self.service.import_attachment(self.parent['id'], self.pdf('disk.pdf', b'disk'))
        self.assertEqual(len(self.store.list_documents()), 1)
        self.assertEqual(len(self.service.list_attachments(self.parent['id'])), 1)
        self.assertEqual(len(list(self.store.objects.iterdir())), 1)

    def test_family_delete_blocks_child_job_then_removes_owned_data_only(self):
        child_path = self.pdf('child.pdf', b'child')
        child = self.service.import_attachment(self.parent['id'], child_path)
        revision = self.store.replace_document_ir(child['id'], 'synthetic/1', [dict(number=1,width=100,height=100,rotation=0,
            cropBox=[0,0,100,100],transform=[1,0,0,1,0,0],blocks=[dict(text='A source quote.',bbox=[.1,.1,.8,.2],kind='text',order=0)])])
        block = self.store.document_blocks(child['id'])[0]
        self.store.save_claim(child['id'], 'A note', [dict(blockId=block['id'],quote='source quote')])
        job = self.store.new_job(child['id'], 'babeldoc')
        for state in ('queued', 'running'):
            job['state'] = state
            self.store.put_job(job)
            for action in (lambda:self.service.delete_document(self.parent['id']),
                           lambda:self.service.delete_attachment(self.parent['id'],child['id'])):
                with self.assertRaises(ValueError): action()
            self.assertEqual(len(self.store.list_documents()), 2)
        job['state'] = 'completed';self.store.put_job(job)
        output=self.store.output_path(job);output.mkdir(parents=True);(output/'result.pdf').write_bytes(b'%PDF-1.7 translated')
        self.service.delete_document(self.parent['id'])
        self.service.purge_document(self.parent['id'])
        self.assertEqual(self.store.list_documents(), [])
        self.assertEqual(self.store.list_jobs(), [])
        self.assertFalse(output.exists())
        self.assertEqual(list(self.store.objects.glob('*.pdf')), [])
        self.assertTrue(self.original.exists());self.assertTrue(child_path.exists())
        with self.store.connection() as db:
            for table in ('desktop_attachment_links','desktop_ir_revisions','desktop_claims','desktop_claim_evidence'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_delete_single_attachment_preserves_root_and_other_child(self):
        a = self.service.import_attachment(self.parent['id'],self.pdf('a.pdf',b'a'))
        b = self.service.import_attachment(self.parent['id'],self.pdf('b.pdf',b'b'),'translation')
        self.service.delete_attachment(self.parent['id'], a['id'])
        self.service.purge_document(a['id'])
        self.assertEqual([row['id'] for row in self.service.list_attachments(self.parent['id'])], [self.parent['id'],b['id']])
        self.assertTrue(self.store.object_path(self.parent).exists())
        self.assertTrue(self.store.object_path(b).exists())
        self.assertFalse(self.store.object_path(a).exists())
        self.assertTrue((self.root/'a.pdf').exists())

    def test_child_sources_and_hash_duplicates_remain_export_protected(self):
        source=self.pdf('private.pdf',b'private')
        child=self.service.import_attachment(self.parent['id'],source)
        alias=self.pdf('alias.pdf',b'private')
        self.service.import_attachment(self.parent['id'],alias)
        copied=self.pdf('copied.pdf',b'private')
        for target in (source,alias,copied,self.store.object_path(child)):
            with self.assertRaises(ValueError): self.store.write_export(target,b'%PDF-1.7 overwrite')
            self.assertEqual(target.read_bytes(),source.read_bytes())

    def test_v5_migration_preserves_library_and_recoverable_backup(self):
        parent_id=self.parent['id'];self.service.close()
        database=self.root/'data/library.sqlite3'
        with closing(sqlite3.connect(database)) as db:
            db.executescript('DROP TABLE desktop_attachment_links; PRAGMA user_version=5;')
        store=LocalStore(self.root/'data');self.addCleanup(store.close)
        self.assertEqual(store.list_root_documents()[0]['id'],parent_id)
        self.assertEqual(store.read_pdf(parent_id),self.original.read_bytes())
        with store.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],11)
        with closing(sqlite3.connect(self.root/'data/library-before-v6.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)
            self.assertEqual(db.execute('SELECT id FROM desktop_documents').fetchone()[0],parent_id)


if __name__ == '__main__':
    unittest.main()
