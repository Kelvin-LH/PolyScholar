# SPDX-License-Identifier: AGPL-3.0-only
import copy
import sqlite3
from contextlib import closing
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from polyscholar.store import LocalStore


class DocumentIRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = LocalStore(self.root / 'library')
        self.addCleanup(self.store.close)
        source = self.root / 'one.pdf'
        source.write_bytes(b'%PDF-1.7 original source')
        self.doc = self.store.import_pdf(source)
        self.pages = [dict(number=1, width=612, height=792, rotation=90,
                           cropBox=[10, 20, 622, 812], transform=[0, 1, -1, 0, 792, 0],
                           blocks=[dict(text='Exact observation: 12 samples.', bbox=[.1, .2, .7, .3], kind='text', order=0)])]

    def parse(self, pages=None):
        return self.store.replace_document_ir(self.doc['id'], 'test-parser/1', self.pages if pages is None else pages)

    def test_revisions_and_original_objects_survive_reparse_and_restart(self):
        original = self.store.object_path(self.doc).read_bytes()
        first = self.parse()
        block = self.store.document_blocks(self.doc['id'])[0]
        self.assertEqual(block['pageTransform']['cropBox'], [10, 20, 622, 812])
        self.assertEqual(block['pageTransform']['matrix'], [0, 1, -1, 0, 792, 0])
        claim = self.store.save_claim(self.doc['id'], 'A proposed interpretation', [dict(blockId=block['id'], quote='12 samples')])
        self.assertEqual(claim['status'], 'evidence_linked')
        self.assertFalse(claim['stale'])
        second = self.parse()
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(self.store.document_blocks(self.doc['id'], first['id'])[0]['id'], block['id'])
        self.assertTrue(self.store.list_claims(self.doc['id'])[0]['stale'])
        self.assertTrue(self.store.list_claims(self.doc['id'])[0]['evidence'][0]['stale'])
        self.assertEqual(self.store.object_path(self.doc).read_bytes(), original)
        self.store.close()
        self.store = LocalStore(self.root / 'library')
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.current_document_ir(self.doc['id'])['id'], second['id'])
        self.assertEqual(len(self.store.document_blocks(self.doc['id'], first['id'])), 1)
        self.assertTrue(self.store.list_claims(self.doc['id'])[0]['stale'])

    def test_cross_document_and_mismatched_quotes_rejected_atomically(self):
        revision = self.parse()
        block = self.store.document_blocks(self.doc['id'])[0]
        other_path = self.root / 'two.pdf'
        other_path.write_bytes(b'%PDF-1.7 different')
        other = self.store.import_pdf(other_path)
        for document_id, evidence in [(other['id'], [dict(blockId=block['id'], quote='12 samples')]),
                                      (self.doc['id'], [dict(blockId=block['id'], quote='13 samples')]),
                                      (self.doc['id'], [dict(blockId=block['id'], quote='')])]:
            with self.assertRaises(ValueError):
                self.store.save_claim(document_id, 'Proposed claim', evidence)
        self.assertEqual(self.store.list_claims(self.doc['id']), [])
        self.assertEqual(self.store.list_claims(other['id']), [])
        with self.assertRaises(ValueError):
            self.store.document_blocks(other['id'], revision['id'])
        self.assertEqual(self.store.save_claim(other['id'], 'Unproven', [])['status'], 'insufficient_evidence')

    def test_invalid_geometry_and_order_do_not_change_current_revision(self):
        revision = self.parse()
        invalid = []
        for bbox in ([0, 0, float('nan'), 1], [0, 0, 1, float('inf')], [-.1, 0, .5, .5], [0, 0, 0, .5], [True, 0, .5, .5]):
            pages = copy.deepcopy(self.pages)
            pages[0]['blocks'][0]['bbox'] = bbox
            invalid.append(pages)
        for field, value in [('number', 0), ('number', True), ('number', 2), ('width', float('inf')), ('height', 0), ('rotation', 45), ('rotation', True), ('cropBox', [0, 0, 0, 1]), ('transform', [0]*5), ('blocks', None)]:
            pages = copy.deepcopy(self.pages)
            pages[0][field] = value
            invalid.append(pages)
        for order in (-1, 1.2, True):
            pages = copy.deepcopy(self.pages)
            pages[0]['blocks'][0]['order'] = order
            invalid.append(pages)
        duplicate = copy.deepcopy(self.pages)
        duplicate[0]['blocks'].append(copy.deepcopy(duplicate[0]['blocks'][0]))
        invalid.extend([duplicate, [], self.pages * 2])
        for pages in invalid:
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                self.parse(pages)
            self.assertEqual(self.store.current_document_ir(self.doc['id'])['id'], revision['id'])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_ir_revisions').fetchone()[0], 1)

    def test_limits_and_audit_events_commit_only_with_valid_changes(self):
        revision = self.parse()
        with self.store.connection() as db:
            before = db.execute('SELECT point FROM desktop_audit ORDER BY sequence').fetchall()
        for name, limit in [('MAX_IR_PAGES', 0), ('MAX_IR_BLOCKS', 0), ('MAX_IR_TEXT_BYTES', 1), ('MAX_PARSER_BYTES', 1)]:
            with patch('polyscholar.document_ir.' + name, limit), self.assertRaises(ValueError):
                self.parse()
        with self.assertRaises(ValueError):
            self.store.save_claim(self.doc['id'], 'a' * 65537, [])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT point FROM desktop_audit ORDER BY sequence').fetchall(), before)
        self.assertEqual(self.store.current_document_ir(self.doc['id'])['id'], revision['id'])
        self.store.save_claim(self.doc['id'], 'Unproven', [])
        with self.store.connection() as db:
            events = db.execute('SELECT point FROM desktop_audit ORDER BY sequence').fetchall()
            self.assertEqual(events[-2:], [('document_parsed',), ('claim_created',)])

    def test_composite_foreign_keys_reject_forged_document_and_revision_links(self):
        first = self.parse()
        block = self.store.document_blocks(self.doc['id'])[0]
        second = self.parse()
        other_path = self.root / 'foreign.pdf'
        other_path.write_bytes(b'%PDF-1.7 foreign source')
        other = self.store.import_pdf(other_path)
        claim = self.store.save_claim(other['id'], 'Foreign claim', [])
        with self.assertRaises(sqlite3.IntegrityError), self.store.connection() as db:
            db.execute('INSERT INTO desktop_claim_evidence VALUES(?,?,?,?,?)',
                       (claim['id'], other['id'], first['id'], block['id'], '12 samples'))
        claim = self.store.save_claim(self.doc['id'], 'Own claim', [])
        with self.assertRaises(sqlite3.IntegrityError), self.store.connection() as db:
            db.execute('INSERT INTO desktop_claim_evidence VALUES(?,?,?,?,?)',
                       (claim['id'], self.doc['id'], second['id'], block['id'], '12 samples'))
        with self.assertRaises(sqlite3.IntegrityError), self.store.connection() as db:
            db.execute('INSERT INTO desktop_ir_current VALUES(?,?)', (other['id'], first['id']))
        with self.store.connection() as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_no_text_pages_are_honest_and_delete_cascades(self):
        pages = copy.deepcopy(self.pages)
        pages[0]['blocks'] = []
        self.assertEqual(self.parse(pages)['status'], 'no_text')
        self.assertEqual(self.store.document_blocks(self.doc['id']), [])
        pages[0]['blocks'] = [dict(text='   ', bbox=[0, 0, 1, 1], kind='text', order=0)]
        self.assertEqual(self.parse(pages)['status'], 'no_text')
        self.store.save_claim(self.doc['id'], 'Without evidence', [])
        self.store.delete_document(self.doc['id'])
        with self.store.connection() as db:
            for table in ('desktop_ir_revisions', 'desktop_ir_current', 'desktop_ir_pages', 'desktop_ir_blocks', 'desktop_claims', 'desktop_claim_evidence'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_v3_migration_backups_preserve_library_jobs_collections_audit_and_object(self):
        collection = self.store.create_collection('Research')
        self.store.set_membership(self.doc['id'], collection['id'])
        self.store.audit('citation_exported')
        job = dict(id='history-job', documentId=self.doc['id'], state='completed')
        self.store.put_job(job)
        original = self.store.object_path(self.doc).read_bytes()
        with self.store.connection() as db:
            for table in ('desktop_claim_evidence', 'desktop_claims', 'desktop_ir_blocks', 'desktop_ir_pages', 'desktop_ir_current', 'desktop_ir_revisions'):
                db.execute('DROP TABLE ' + table)
            db.execute('PRAGMA user_version=3')
            db.execute('DROP TRIGGER desktop_audit_validate_insert')
            db.execute("CREATE TRIGGER desktop_audit_validate_insert BEFORE INSERT ON desktop_audit WHEN NEW.point IN ('document_parsed','claim_created') BEGIN SELECT RAISE(ABORT,'legacy enum'); END")
            audit = db.execute('SELECT * FROM desktop_audit').fetchall()
        self.store.close()
        self.store = LocalStore(self.root / 'library')
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.document(self.doc['id']), self.doc)
        self.assertEqual(self.store.document_collections(self.doc['id']), [collection['id']])
        self.assertEqual(self.store.list_jobs(), [job])
        self.assertEqual(self.store.object_path(self.doc).read_bytes(), original)
        with self.store.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertEqual(db.execute('SELECT * FROM desktop_audit').fetchall(), audit)
        with closing(sqlite3.connect(self.store.root / 'library-before-v4.sqlite3')) as backup:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(backup.execute('SELECT * FROM desktop_audit').fetchall(), audit)
            self.assertEqual(backup.execute('SELECT id FROM desktop_jobs').fetchall(), [('history-job',)])
        self.assertEqual(self.parse()['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
