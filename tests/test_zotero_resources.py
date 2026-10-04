# SPDX-License-Identifier: AGPL-3.0-only
"""迁移真实链接文件、快照伴随文件与批注缓存的边界。

Boundary checks for linked files, snapshot companions and annotation cache bytes.
"""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from polyscholar.service import LocalService
from test_zotero_migration import fixture


class ZoteroResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        fixture(self.source)
        self.linked = self.root / 'linked'
        self.linked.mkdir()
        (self.linked / 'relative.pdf').write_bytes((self.source / 'storage/CCCCCCCC/paper.pdf').read_bytes())
        self.service = LocalService(self.root / 'local')

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_explicit_linked_directory_and_relative_pdf(self):
        with closing(sqlite3.connect(self.source / 'zotero.sqlite')) as db, db:
            db.execute("UPDATE itemAttachments SET linkMode=2,path='attachments:relative.pdf' WHERE itemID=3")
        preview = self.service.preview_zotero_migration(self.source, linked_directory=self.linked)
        resource = next(row for row in preview['resources'] if row['sourceId'] == '3')
        self.assertEqual(resource['status'], 'pdf-ready')
        self.assertEqual(resource['sourcePath'], str(self.linked / 'relative.pdf'))
        receipt = self.service.import_zotero_preview(preview, ['1'])
        imported = self.service.store.document(receipt['mappings']['3'])
        self.assertEqual(self.service.store.read_pdf(imported['id']), (self.linked / 'relative.pdf').read_bytes())
        with self.assertRaises(ValueError):
            self.service.store.write_export(self.linked / 'relative.pdf', b'replacement')

    def test_snapshot_companions_and_annotation_image_keep_distinct_resource_ids(self):
        with closing(sqlite3.connect(self.source / 'zotero.sqlite')) as db, db:
            db.executescript('''
                UPDATE itemAttachments SET contentType='text/html',path='storage:page.html' WHERE itemID=3;
                CREATE TABLE libraries(libraryID INT,type TEXT); INSERT INTO libraries VALUES(1,'user');
                INSERT INTO itemTypesCombined VALUES(5,'annotation'); INSERT INTO items VALUES(7,5,'GGGGGGGG',1);
                CREATE TABLE itemAnnotations(itemID INT,parentItemID INT,type INT,text TEXT,comment TEXT,position TEXT);
                INSERT INTO itemAnnotations VALUES(7,3,3,'','Image annotation','{"pageIndex":0,"rects":[[0,0,10,10]]}');
            ''')
        folder = self.source / 'storage/CCCCCCCC'
        (folder / 'paper.pdf').unlink()
        (folder / 'page.html').write_text('<img src="assets/picture.png">', encoding='utf-8')
        (folder / 'assets').mkdir()
        (folder / 'assets/picture.png').write_bytes(b'synthetic companion bytes')
        cache = self.source / 'cache/library'
        cache.mkdir(parents=True)
        image = cache / 'GGGGGGGG.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic cache bytes, not decoded')
        preview = self.service.preview_zotero_migration(self.source)
        companions = [row for row in preview['resources'] if row.get('kind') == 'snapshot-companion']
        self.assertEqual(len(companions), 1)
        cached = [row for row in preview['resources'] if row.get('kind') == 'annotation-cache']
        self.assertEqual(cached[0]['status'], 'archive-ready')
        identifiers = [row.get('resourceId', row['sourceId']) for row in preview['resources']]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        receipt = self.service.import_zotero_preview(preview, ['1'])
        archived = self.service.read_zotero_migration(receipt['id'])['archive']['resources']
        for resource in archived:
            if resource.get('archivedPath'):
                output = self.root / (resource.get('resourceId', resource['sourceId']).replace(':', '_').replace('/', '_') + '.bin')
                self.service.export_zotero_resource(receipt['id'], resource.get('resourceId', resource['sourceId']), output)
                self.assertEqual(output.read_bytes(), Path(resource['sourcePath']).read_bytes())
        self.assertEqual(len({row['archivedPath'] for row in archived if row.get('archivedPath')}), 3)

    def _attachment(self, mode, path, content_type='application/pdf'):
        with closing(sqlite3.connect(self.source / 'zotero.sqlite')) as db, db:
            db.execute('UPDATE itemAttachments SET linkMode=?,path=?,contentType=? WHERE itemID=3',
                       (mode, path, content_type))

    def test_linked_scope_required_and_traversal_rejected(self):
        self._attachment(2, 'attachments:relative.pdf')
        preview = self.service.preview_zotero_migration(self.source)
        self.assertEqual(preview['resources'][0]['status'], 'pending-linked')
        self._attachment(2, 'attachments:../source/storage/CCCCCCCC/paper.pdf')
        preview = self.service.preview_zotero_migration(self.source, linked_directory=self.linked)
        self.assertEqual(preview['resources'][0]['status'], 'outside-linked-root')
        self.assertNotEqual(preview['resources'][0]['status'], 'pdf-ready')

    def test_absolute_path_outside_linked_scope_rejected(self):
        self._attachment(2, str(self.source / 'storage/CCCCCCCC/paper.pdf'))
        preview = self.service.preview_zotero_migration(self.source, linked_directory=self.linked)
        self.assertEqual(preview['resources'][0]['status'], 'outside-linked-root')
        self.assertNotIn('sourcePath', preview['resources'][0])

    def test_linked_source_change_invalidates_import(self):
        self._attachment(2, 'attachments:relative.pdf')
        preview = self.service.preview_zotero_migration(self.source, linked_directory=self.linked)
        (self.linked / 'relative.pdf').write_bytes(b'changed source bytes')
        with self.assertRaises(ValueError):
            self.service.import_zotero_preview(preview, ['1'])
        self.assertEqual(self.service.list_documents(), [])

    def test_snapshot_new_file_invalidates_import(self):
        self._attachment(0, 'storage:page.html', 'text/html')
        folder = self.source / 'storage/CCCCCCCC'
        (folder / 'paper.pdf').unlink()
        (folder / 'page.html').write_text('<p>fixture</p>')
        preview = self.service.preview_zotero_migration(self.source)
        (folder / 'new.css').write_text('p { color: green }')
        with self.assertRaises(ValueError):
            self.service.import_zotero_preview(preview, ['1'])
        self.assertEqual(self.service.list_documents(), [])

    def test_resource_count_limit_is_not_silent_truncation(self):
        self._attachment(0, 'storage:page.html', 'text/html')
        folder = self.source / 'storage/CCCCCCCC'
        (folder / 'paper.pdf').unlink()
        (folder / 'page.html').write_text('<p>fixture</p>')
        (folder / 'child.css').write_bytes(b'css')
        self.service._zotero_importer.policy.max_resources = 1
        with self.assertRaisesRegex(ValueError, '资源数量'):
            self.service.preview_zotero_migration(self.source)
        self.assertEqual(self.service.list_documents(), [])
        self.assertEqual(list(self.service.store.root.glob('zotero-*')), [])

    @unittest.skipIf(__import__('os').name == 'nt', 'POSIX symlink/FIFO vector')
    def test_snapshot_links_and_fifo_are_reported_without_following(self):
        import os
        self._attachment(0, 'storage:page.html', 'text/html')
        folder = self.source / 'storage/CCCCCCCC'
        (folder / 'paper.pdf').unlink()
        (folder / 'page.html').write_text('<p>fixture</p>')
        (folder / 'outside').symlink_to(self.linked, target_is_directory=True)
        os.mkfifo(folder / 'pipe')
        preview = self.service.preview_zotero_migration(self.source)
        companions = [r for r in preview['resources'] if r.get('kind') == 'snapshot-companion']
        self.assertEqual({r['relativePath'] for r in companions}, {'outside', 'pipe'})
        self.assertTrue(all(r['status'] == 'unsafe' and 'sourcePath' not in r for r in companions))
        self.assertEqual(preview['resources'][0]['ancillaryStatus'], 'partial')

    def test_group_annotation_cache_and_embedded_note_image_are_archived(self):
        with closing(sqlite3.connect(self.source / 'zotero.sqlite')) as db, db:
            db.executescript("""
                CREATE TABLE libraries(libraryID INT,type TEXT); INSERT INTO libraries VALUES(1,'group');
                CREATE TABLE groups(libraryID INT,groupID INT); INSERT INTO groups VALUES(1,42);
                INSERT INTO itemTypesCombined VALUES(5,'annotation'); INSERT INTO items VALUES(7,5,'GGGGGGGG',1);
                CREATE TABLE itemAnnotations(itemID INT,parentItemID INT,type INT,text TEXT,position TEXT);
                INSERT INTO itemAnnotations VALUES(7,3,4,'Ink','{}');
                INSERT INTO items VALUES(8,2,'HHHHHHHH',1);
                INSERT INTO itemAttachments VALUES(8,6,4,'image/png','storage:embedded.png');
            """)
        cache = self.source / 'cache/groups/42'
        cache.mkdir(parents=True)
        (cache / 'GGGGGGGG.png').write_bytes(b'synthetic group ink cache')
        embedded = self.source / 'storage/HHHHHHHH'
        embedded.mkdir()
        (embedded / 'embedded.png').write_bytes(b'synthetic embedded image')
        preview = self.service.preview_zotero_migration(self.source)
        resources = {row['resourceId']: row for row in preview['resources']}
        self.assertEqual(resources['7:annotation-cache']['status'], 'archive-ready')
        self.assertEqual(resources['8']['status'], 'archive-ready')
        receipt = self.service.import_zotero_preview(preview, ['1'])
        for identifier in ('7:annotation-cache', '8'):
            output = self.root / (identifier.replace(':', '_') + '.bin')
            self.service.export_zotero_resource(receipt['id'], identifier, output)
            self.assertEqual(output.read_bytes(), Path(resources[identifier]['sourcePath']).read_bytes())


if __name__ == '__main__':
    unittest.main()
