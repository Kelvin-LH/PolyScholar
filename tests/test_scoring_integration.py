# SPDX-License-Identifier: AGPL-3.0-only
"""Review migrations and lifecycle regressions; 评阅迁移及生命周期回归。"""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from polyscholar.store import LocalStore


class ScoringIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'library'
        self.store = LocalStore(self.root)
        self.addCleanup(lambda: self.store.close())
        source = Path(self.temporary.name) / 'source.pdf'
        source.write_bytes(b'%PDF-1.7 scoring migration fixture')
        self.document = self.store.import_pdf(source)
        self.identifier = self.document['id']

    def reopen(self):
        self.store.close()
        self.store = LocalStore(self.root)

    def test_main_v12_preserves_claims_and_fileless_items(self):
        claim = self.store.save_claim(self.identifier, 'manual research note', [])
        item = self.store.create_bibliographic_item({'title': 'Book without PDF', 'itemType': 'book'})
        with self.store.connection() as db:
            db.executescript('DROP TABLE desktop_scores;DROP TABLE desktop_score_reports;PRAGMA user_version=12;')
        self.reopen()
        self.assertEqual(self.store.list_claims(self.identifier)[0]['id'], claim['id'])
        self.assertIsNone(self.store.document(item['id'])['sha256'])
        self.store.set_scores(item['id'], [{'kind': 'paper', 'score': 81}])
        self.assertEqual(self.store.document_scores(item['id'])['paper']['score'], 81)
        self.assertTrue((self.root / 'library-before-v13.sqlite3').is_file())

    def test_feature_v12_pdf_only_schema_is_repaired_without_losing_reviews(self):
        self.store.set_scores(self.identifier, [{'kind': 'confidence', 'score': 65}])
        report = '{"rubric_version":"1.0.0","notes":"original report"}'
        self.store.set_score_report(self.identifier, 'confidence', 'A', report)
        self.store.close()
        db = sqlite3.connect(self.root / 'library.sqlite3')
        try:
            db.executescript('''PRAGMA foreign_keys=OFF;PRAGMA legacy_alter_table=ON;
                BEGIN IMMEDIATE;
                CREATE TABLE feature_documents(id TEXT PRIMARY KEY, sha256 TEXT NOT NULL,data TEXT NOT NULL);
                INSERT INTO feature_documents SELECT * FROM desktop_documents;
                DROP TABLE desktop_documents;ALTER TABLE feature_documents RENAME TO desktop_documents;
                DROP TABLE desktop_claim_provenance;DROP TABLE desktop_model_summaries;
                DROP TABLE desktop_claim_evidence;DROP TABLE desktop_claims;
                PRAGMA user_version=12;COMMIT;''')
        finally:
            db.close()
        self.store = LocalStore(self.root)
        self.assertEqual(self.store.document_scores(self.identifier)['confidence']['score'], 65)
        self.assertEqual(self.store.score_reports(self.identifier, 'confidence', 'A')[0]['data']['notes'], 'original report')
        item = self.store.create_bibliographic_item({'title': 'New bibliography'})
        self.assertIsNone(item['sha256'])
        with self.store.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 15)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_branch_schema_repair_keeps_exact_quote_and_model_provenance(self):
        pages = [dict(number=1, width=612, height=792, rotation=0,
                      cropBox=[0, 0, 612, 792], transform=[1, 0, 0, 1, 0, 0],
                      blocks=[dict(text='Exact observation: 12 samples.',
                                   bbox=[.1, .2, .7, .3], kind='text', order=0)])]
        revision = self.store.replace_document_ir(self.identifier, 'fixture/1', pages)
        block = self.store.document_blocks(self.identifier)[0]
        self.store.save_model_summary(self.identifier, revision['id'],
            [dict(text='Author reports 12 samples', evidence=[dict(blockId=block['id'], quote='12 samples')],
                  category='data', attribution='author_report')], 'fixture-model', {})
        # Exercise both schema variants without repeating destructive legacy steps.
        # 覆盖两种结构；不重放旧分支曾经删除证据的破坏性操作。
        for pdf_only in (False, True):
            self.store.close()
            db = sqlite3.connect(self.root / 'library.sqlite3')
            try:
                db.execute('PRAGMA user_version=12')
                if pdf_only:
                    db.executescript('''PRAGMA foreign_keys=OFF;PRAGMA legacy_alter_table=ON;
                        BEGIN IMMEDIATE;
                        CREATE TABLE branch_documents(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,data TEXT NOT NULL);
                        INSERT INTO branch_documents SELECT * FROM desktop_documents;
                        DROP TABLE desktop_documents;ALTER TABLE branch_documents RENAME TO desktop_documents;
                        COMMIT;''')
                db.commit()
            finally:
                db.close()
            self.store = LocalStore(self.root)
            claim = self.store.list_claims(self.identifier)[0]
            self.assertEqual(claim['evidence'][0]['quote'], '12 samples')
            self.assertTrue(claim['evidence'][0]['exactQuoteValidated'])
            self.assertEqual(claim['provenance']['model'], 'fixture-model')

    def test_two_kind_scores_rebuild_retains_rows(self):
        with self.store.connection() as db:
            db.executescript('''DROP TABLE desktop_scores;
                CREATE TABLE desktop_scores(document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('paper','confidence')),score REAL,rationale TEXT NOT NULL,
                detail TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(document_id,kind));
                PRAGMA user_version=12;''')
            db.execute('INSERT INTO desktop_scores VALUES(?,?,?,?,?,?,?)', (self.identifier, 'paper', 75, 'old', '{}', 't', 't'))
        self.reopen()
        self.assertEqual(self.store.document_scores(self.identifier)['paper']['rationale'], 'old')
        self.store.set_scores(self.identifier, [{'kind': 'summary', 'detail': '{"problem":"research"}'}])
        self.assertIsNone(self.store.document_scores(self.identifier)['summary']['score'])

    def test_trash_restore_and_purge_review_lifecycle(self):
        self.store.set_scores(self.identifier, [{'kind': 'paper', 'score': 85}])
        self.store.set_score_report(self.identifier, 'paper', 'A', '{"notes":"retained"}')
        self.store.trash_document(self.identifier)
        with self.assertRaises(ValueError):
            self.store.set_scores(self.identifier, [{'kind': 'paper', 'score': 45}])
        self.store.restore_document(self.identifier)
        self.assertEqual(self.store.document_scores(self.identifier)['paper']['score'], 85)
        self.store.trash_document(self.identifier)
        self.store.purge_document(self.identifier)
        with self.store.connection() as db:
            for table in ('desktop_scores', 'desktop_score_reports'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_score_write_and_audit_are_atomic(self):
        with self.store.connection() as db:
            db.execute("CREATE TRIGGER fail_score_audit BEFORE INSERT ON desktop_audit WHEN NEW.point='score_updated' BEGIN SELECT RAISE(ABORT,'fixture');END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.set_scores(self.identifier, [{'kind': 'paper', 'score': 50}])
        self.assertIsNone(self.store.document_scores(self.identifier)['paper'])

    def test_invalid_batch_cannot_overwrite_existing_scores(self):
        self.store.set_scores(self.identifier, [{'kind': 'paper', 'score': 87}])
        for value in (float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):
                self.store.set_scores(self.identifier, [{'kind': 'paper', 'score': value}])
        with self.assertRaises(ValueError):
            self.store.set_scores(self.identifier, [{'kind': 'paper', 'score': 20}, {'kind': 'paper', 'score': 60}])
        self.assertEqual(self.store.document_scores(self.identifier)['paper']['score'], 87)


if __name__ == '__main__':
    unittest.main()
