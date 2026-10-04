# SPDX-License-Identifier: AGPL-3.0-only
"""AI score storage, validation and list-sorting surface (schema v10)."""
from pathlib import Path
import tempfile
import unittest

from polyscholar.service import LocalService
from polyscholar.store import LocalStore

PAPER = dict(kind='paper', score=87.5, rationale='创新点充分；实验覆盖不足。', detail='{"dimensions":[]}')

class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = LocalService(Path(self.temp.name) / 'library')
        source = Path(self.temp.name) / 'a.pdf'
        source.write_bytes(b'%PDF-1.7 scores sample')
        self.document = self.service.import_pdf(source)

    def tearDown(self):
        self.service.close()

    def test_store_and_read_scores(self):
        result = self.service.set_scores(self.document['id'], [PAPER, dict(kind='confidence', score=42, rationale='数据可核验')])
        self.assertEqual(result['paper']['score'], 87.5)
        self.assertEqual(result['confidence']['score'], 42)
        scores = self.service.document_scores(self.document['id'])
        self.assertEqual(scores['paper']['rationale'], '创新点充分；实验覆盖不足。')
        self.assertEqual(scores['confidence']['detail'], {})

    def test_list_documents_exposes_scores_for_sorting(self):
        self.service.set_scores(self.document['id'], [PAPER])
        listed = self.service.list_documents()[0]
        self.assertEqual(listed['scores']['paper'], 87.5)
        self.assertIsNone(listed['scores'].get('confidence'))

    def test_scores_are_per_document_for_sorting(self):
        others = []
        for index in (2, 3):
            source = Path(self.temp.name) / f'b{index}.pdf'
            source.write_bytes(b'%PDF-1.7 ' + str(index).encode())
            others.append(self.service.import_pdf(source))
        self.service.set_scores(self.document['id'], [PAPER])
        self.service.set_scores(others[0]['id'], [dict(kind='paper', score=95.0)])
        listed = {d['id']: (d['scores'].get('paper') or -1) for d in self.service.list_documents()}
        self.assertEqual(listed[self.document['id']], 87.5)
        self.assertEqual(listed[others[0]['id']], 95.0)
        self.assertEqual(listed[others[1]['id']], -1)

    def test_upsert_replaces_previous_values(self):
        self.service.set_scores(self.document['id'], [PAPER])
        self.service.set_scores(self.document['id'], [dict(kind='paper', score=60, rationale='复核后下调')])
        self.assertEqual(self.service.document_scores(self.document['id'])['paper']['score'], 60)

    def test_summary_kind_stores_overview_without_score(self):
        summary = dict(kind='summary', rationale='解决端到端驾驶的因果推理缺失;方法为 VLM 生成动作;适用于自动驾驶;问题:仿真到实车差距。',
                       detail='{"problem":"因果推理缺失","method":"VLM 动作生成","domains":["自动驾驶"],"findings":["仿真差距明显"]}')
        self.service.set_scores(self.document['id'], [summary])
        stored = self.service.document_scores(self.document['id'])['summary']
        self.assertIsNone(stored['score'])
        self.assertIn('端到端驾驶', stored['rationale'])
        self.assertEqual(stored['detail']['method'], 'VLM 动作生成')
        listed = self.service.list_documents()[0]['scores']
        self.assertIn('summary', listed)

    def test_validation_rejects_out_of_range_and_bad_payload(self):
        for entries in [dict(kind='paper', score=101), dict(kind='paper', score=-1),
                        dict(kind='paper', score='80'), dict(kind='movie', score=50),
                        dict(kind='paper', rationale='x' * 65537), dict(kind='paper', detail='not-json'),
                        dict(kind='paper', extra='x')]:
            with self.assertRaises(ValueError):
                self.service.set_scores(self.document['id'], [entries])

    def test_schema_is_v12_with_scores_table(self):
        with self.service.store.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 15)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('desktop_scores', tables)

    def test_legacy_two_kind_constraint_is_rebuilt_by_migration(self):
        """真实事故回归:老库的 desktop_scores 只有 paper/confidence 两种 CHECK,
        代码加了 summary 类型也不会更新既有表;v12 必须重建表并保留旧行。"""
        legacy_ddl = """
            DROP TABLE desktop_scores;
            CREATE TABLE desktop_scores(document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('paper','confidence')),
                score REAL, rationale TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(document_id,kind));
            PRAGMA user_version=10;"""
        with self.service.store.connection() as db:
            db.executescript(legacy_ddl)
        self.service.store.close()
        self.service.store = LocalStore(self.service.store.root)
        self.addCleanup(self.service.store.close)
        with self.service.store.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 15)
            ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='desktop_scores'").fetchone()[0]
            self.assertIn("'summary'", ddl)
            db.execute("INSERT INTO desktop_scores VALUES(?,?,?,?,?,?,?)",
                       (self.document['id'], 'summary', None, '提炼', '{}', 't', 't'))
        self.assertEqual(self.service.document_scores(self.document['id'])['summary']['score'], None)

if __name__ == '__main__':
    unittest.main()
