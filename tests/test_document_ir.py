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
        self.store.purge_document(self.doc['id'])
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
            for table in ('desktop_claim_provenance', 'desktop_model_summaries', 'desktop_claim_evidence', 'desktop_claims', 'desktop_ir_blocks', 'desktop_ir_pages', 'desktop_ir_current', 'desktop_ir_revisions'):
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
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 10)
            self.assertEqual(db.execute('SELECT * FROM desktop_audit').fetchall(), audit)
        with closing(sqlite3.connect(self.store.root / 'library-before-v4.sqlite3')) as backup:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(backup.execute('SELECT * FROM desktop_audit').fetchall(), audit)
            self.assertEqual(backup.execute('SELECT id FROM desktop_jobs').fetchall(), [('history-job',)])
        self.assertEqual(self.parse()['status'], 'ready')

    def test_model_batch_provenance_survives_restart_and_is_distinct_from_manual(self):
        revision = self.parse()
        block = self.store.document_blocks(self.doc['id'])[0]
        manual = self.store.save_claim(self.doc['id'], 'Manual note', [])
        claims = [dict(text='Model interpretation', category='result', attribution='author_report', evidence=[dict(blockId=block['id'], quote='12 samples')]),
                  dict(text='No matching passage', evidence=[])]
        saved = self.store.save_model_summary(self.doc['id'], revision['id'], claims, 'deepseek-chat', {'prompt_tokens': 11})
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]['provenance']['source'], 'model')
        self.assertEqual(saved[0]['provenance']['model'], 'deepseek-chat')
        self.assertEqual(saved[0]['provenance']['category'], 'result')
        self.assertEqual(saved[0]['provenance']['attribution'], 'author_report')
        self.assertEqual(saved[0]['provenance']['usage'], {'prompt_tokens': 11})
        self.assertEqual(saved[0]['provenance']['inputBlocks'], [block['id']])
        self.assertEqual(saved[0]['provenance']['summaryId'], saved[1]['provenance']['summaryId'])
        self.assertEqual(saved[1]['status'], 'insufficient_evidence')
        self.assertEqual(manual['provenance'], {'source': 'manual'})
        self.store.close()
        self.store = LocalStore(self.root / 'library')
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.list_claims(self.doc['id']), [manual, *saved])
        self.parse()
        self.assertTrue(all(c['stale'] for c in self.store.list_claims(self.doc['id'])))

    def test_model_batch_rejects_stale_cross_document_and_bad_last_claim_without_partial_save(self):
        revision = self.parse()
        block = self.store.document_blocks(self.doc['id'])[0]
        valid = dict(text='Valid interpretation', evidence=[dict(blockId=block['id'], quote='12 samples')])
        for invalid in [dict(text='Bad quote', evidence=[dict(blockId=block['id'], quote='13 samples')]),
                        dict(text='Bad block', evidence=[dict(blockId='other', quote='12 samples')]),
                        dict(text='Blank quote', evidence=[dict(blockId=block['id'], quote=' ')]),
                        dict(text='Duplicate block', evidence=valid['evidence']*2),
                        dict(text='', evidence=[]), dict(text='Unexpected', evidence=[], key='private'),
                        dict(text='Invalid category', evidence=[], category='fake'), dict(text='Invalid attribution', evidence=[], attribution='truth')]:
            with self.assertRaises(ValueError):
                self.store.save_model_summary(self.doc['id'], revision['id'], [valid, invalid], 'model', {})
        other_path = self.root / 'other.pdf'
        other_path.write_bytes(b'%PDF-1.7 other')
        other = self.store.import_pdf(other_path)
        other_revision = self.store.replace_document_ir(other['id'], 'test', self.pages)
        with self.assertRaises(ValueError):
            self.store.save_model_summary(other['id'], other_revision['id'], [valid], 'model', {})
        self.parse()
        with self.assertRaises(ValueError):
            self.store.save_model_summary(self.doc['id'], revision['id'], [valid], 'model', {})
        self.assertEqual(self.store.list_claims(self.doc['id']), [])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_model_summaries').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM desktop_audit WHERE point='claim_created'").fetchone()[0], 0)

    def test_model_batch_sql_failure_rolls_back_claims_evidence_provenance_and_audit(self):
        revision = self.parse()
        block = self.store.document_blocks(self.doc['id'])[0]
        claims = [dict(text='First', evidence=[dict(blockId=block['id'], quote='12 samples')]), dict(text='Second', evidence=[])]
        with self.store.connection() as db:
            db.execute("CREATE TRIGGER reject_second BEFORE INSERT ON desktop_claims WHEN NEW.text='Second' BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_model_summary(self.doc['id'], revision['id'], claims, 'model', {})
        with self.store.connection() as db:
            for table in ('desktop_claims', 'desktop_claim_evidence', 'desktop_model_summaries', 'desktop_claim_provenance'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM desktop_audit WHERE point='claim_created'").fetchone()[0], 0)

    def test_v4_migration_preserves_manual_claim_and_versioned_quote(self):
        revision = self.parse()
        block = self.store.document_blocks(self.doc['id'])[0]
        manual = self.store.save_claim(self.doc['id'], 'Old manual note', [dict(blockId=block['id'], quote='12 samples')])
        with self.store.connection() as db:
            db.execute('DROP TABLE desktop_claim_provenance')
            db.execute('DROP TABLE desktop_model_summaries')
            db.execute('PRAGMA user_version=4')
        self.store.close()
        self.store = LocalStore(self.root / 'library')
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.list_claims(self.doc['id']), [manual])
        self.assertEqual(self.store.current_document_ir(self.doc['id'])['id'], revision['id'])
        with closing(sqlite3.connect(self.store.root / 'library-before-v5.sqlite3')) as backup:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertEqual(backup.execute('SELECT id FROM desktop_claims').fetchall(), [(manual['id'],)])
        with self.store.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 10)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_model_input_scope_retains_uncited_blocks_and_refuses_evidence_outside_selection(self):
        pages = copy.deepcopy(self.pages)
        pages[0]['blocks'].append(dict(text='Another passage.', bbox=[.1, .4, .7, .5], kind='text', order=1))
        revision = self.parse(pages)
        one, two = self.store.document_blocks(self.doc['id'])
        claims = [dict(text='Interpretation', evidence=[dict(blockId=one['id'], quote='12 samples')])]
        for selected in [[two['id']], ['foreign'], [one['id'], one['id']], [True], 'invalid']:
            with self.assertRaises(ValueError):
                self.store.save_model_summary(self.doc['id'], revision['id'], claims, 'model', {}, selected)
        self.assertEqual(self.store.list_claims(self.doc['id']), [])
        saved = self.store.save_model_summary(self.doc['id'], revision['id'], claims, 'model', {}, [one['id'], two['id']])
        self.assertEqual(saved[0]['provenance']['inputBlocks'], [one['id'], two['id']])

    def test_model_provenance_rejects_unknown_usage_and_invalid_counts(self):
        revision = self.parse()
        claims = [dict(text='Interpretation', evidence=[])]
        for usage in [{'apiKey': 'private'}, {'prompt_tokens': True}, {'completion_tokens': -1}, {'total_tokens': 1.5}, None]:
            with self.assertRaises(ValueError):
                self.store.save_model_summary(self.doc['id'], revision['id'], claims, 'model', usage)
        with self.assertRaises(ValueError):
            self.store.save_model_summary(self.doc['id'], revision['id'], claims, 'm'*1025, {})
        self.store.save_model_summary(self.doc['id'], revision['id'], claims, 'model', {})
        self.store.delete_document(self.doc['id'])
        self.store.purge_document(self.doc['id'])
        with self.store.connection() as db:
            for table in ('desktop_model_summaries', 'desktop_claim_provenance'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
