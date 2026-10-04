# SPDX-License-Identifier: AGPL-3.0-only
"""独立迁移边界与回归向量，不代替真实 Zotero 应用验收。

Independent migration boundary vectors, not real Zotero application acceptance.
"""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from polyscholar.service import LocalService
from polyscholar.store import LocalStore
from test_zotero_migration import fixture


class ZoteroMigrationVectors(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        fixture(self.source)
        self.service = LocalService(self.root / 'local')

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_source_directory_remains_protected_after_restart(self):
        preview = self.service.preview_zotero_migration(self.source)
        self.service.import_zotero_preview(preview)
        self.service.close()
        self.service = LocalService(self.root / 'local')
        db = self.source / 'zotero.sqlite'
        before = db.read_bytes()
        with self.assertRaises(ValueError):
            self.service.store.write_export(db, b'export must not replace source database')
        self.assertEqual(db.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.service.store.write_export(self.source / 'new-export.pdf', b'%PDF-1.7')

    def test_partial_selection_keeps_transitive_children_without_sibling_fields(self):
        with closing(sqlite3.connect(self.source / 'zotero.sqlite')) as db:
            db.executescript('''
                CREATE TABLE tags(tagID INT,name TEXT);
                INSERT INTO tags VALUES(1,'Keep'),(2,'Private sibling tag');
                CREATE TABLE itemTags(itemID INT,tagID INT,type INT);
                INSERT INTO itemTags VALUES(1,1,0),(2,2,0);
                CREATE TABLE savedSearches(savedSearchID INT,savedSearchName TEXT);
                INSERT INTO savedSearches VALUES(1,'Private saved search');
                CREATE TABLE itemAnnotations(itemID INT,parentItemID INT,text TEXT,comment TEXT,position TEXT);
                INSERT INTO items VALUES(7,5,'GGGGGGGG',1);
                INSERT INTO itemTypesCombined VALUES(5,'annotation');
                INSERT INTO itemAnnotations VALUES(7,3,'Keep highlight','Comment','{"pageIndex":0}');
            ''')
        preview = self.service.preview_zotero_migration(self.source)
        receipt = self.service.import_zotero_preview(preview, ['1'])
        archive = self.service.read_zotero_migration(receipt['id'])['archive']
        self.assertEqual({item['itemID'] for item in archive['tables']['items']}, {1, 3, 6, 7})
        text = json.dumps(archive, ensure_ascii=False)
        self.assertNotIn('Second', text)
        self.assertNotIn('Private sibling tag', text)
        self.assertNotIn('Private saved search', text)
        self.assertIn('Keep highlight', text)
        self.assertEqual(receipt['counts']['native'], 1)
        self.assertEqual(receipt['counts']['pdfs'], 1)

    def test_resource_changed_after_preview_refuses_publication(self):
        preview = self.service.preview_zotero_migration(self.source)
        resource = self.source / 'storage' / 'CCCCCCCC' / 'paper.pdf'
        resource.write_bytes(resource.read_bytes() + b' changed')
        with self.assertRaises(ValueError):
            self.service.import_zotero_preview(preview)
        self.assertEqual(self.service.list_documents(), [])
        self.assertEqual(self.service.list_zotero_migrations(), [])

    def test_final_archive_budget_counts_adapted_fields_and_rolls_back(self):
        preview = self.service.preview_zotero_migration(self.source)
        importer = self.service._zotero_importer
        state = importer._previews[preview['token']]
        graph = state['graph']
        raw_size = sum(len(json.dumps(row, ensure_ascii=False).encode('utf-8')) for rows in graph.values() for row in rows)
        full_size = len(json.dumps(dict(tables=graph, items=preview['items'], resources=preview['resources']), ensure_ascii=False).encode('utf-8'))
        self.assertGreater(full_size, raw_size)
        importer.policy.max_archive_bytes = (raw_size + full_size) // 2
        with self.assertRaisesRegex(ValueError, '完整档案'):
            self.service.preview_zotero_migration(self.source)
        # 提交时增加的档案路径同样受最终上限约束，失败不留下记录或对象。
        # Paths added at commit count toward the final limit; rejection leaves no records or objects.
        state['maxArchiveBytes'] = 1
        with self.assertRaisesRegex(ValueError, '完整档案'):
            self.service.import_zotero_preview(preview)
        self.assertEqual(self.service.list_documents(), [])
        self.assertEqual(self.service.list_zotero_migrations(), [])
        self.assertEqual(list(self.service.store.objects.glob('*.pdf')), [])

    def test_failed_v12_schema_does_not_advance_v11(self):
        item = self.service.create_bibliographic_item({'title': 'Keep v11 metadata'})
        self.service.close()
        path = self.root / 'local' / 'library.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('DROP TABLE desktop_zotero_mapping; DROP TABLE desktop_zotero_migrations; PRAGMA user_version=11;')
        with patch('polyscholar.store.MIGRATION_SCHEMA', 'CREATE TABLE broken(;'):
            with self.assertRaises(sqlite3.Error):
                LocalStore(self.root / 'local')
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 11)
            self.assertEqual(db.execute('SELECT id FROM desktop_documents').fetchone()[0], item['id'])
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_zotero_migrations'").fetchone())
        self.assertTrue((self.root / 'local' / 'library-before-v12.sqlite3').is_file())
        self.service = LocalService(self.root / 'local')
        self.assertEqual(self.service.store.document(item['id'])['title'], 'Keep v11 metadata')


if __name__ == '__main__':
    unittest.main()
