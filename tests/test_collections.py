# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import sqlite3
import tempfile
from pathlib import Path
import unittest
from polyscholar.store import LocalStore

class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=LocalStore(self.root/'library')
        self.addCleanup(self.store.close)
        self.source=self.root/'sample.pdf';self.source.write_bytes(b'%PDF-1.7 sample')
        self.document=self.store.import_pdf(self.source)

    def test_shared_membership_is_not_a_file_copy_and_removal_preserves_item(self):
        first=self.store.create_collection('项目一');second=self.store.create_collection('项目二')
        for collection in (first,second,first):self.store.set_membership(self.document['id'],collection['id'])
        self.assertEqual(len(self.store.document_collections(self.document['id'])),2)
        self.assertEqual(len(list(self.store.objects.glob('*.pdf'))),1)
        self.store.set_membership(self.document['id'],first['id'],False)
        self.assertEqual(self.store.search_documents(collection_id=first['id']),[])
        self.assertEqual(self.store.search_documents(collection_id=second['id'])[0]['id'],self.document['id'])
        self.assertTrue(self.store.object_path(self.document).exists())
        self.store.close()
        reopened=LocalStore(self.store.root)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.document_collections(self.document['id']),[second['id']])

    def test_three_levels_move_cycle_rejected_and_delete_branch_keeps_documents(self):
        a=self.store.create_collection('A');b=self.store.create_collection('B',a['id']);c=self.store.create_collection('C',b['id'])
        self.store.set_membership(self.document['id'],c['id'])
        self.assertEqual(self.store.search_documents(collection_id=a['id']),[])
        self.assertEqual(len(self.store.search_documents(collection_id=a['id'],include_descendants=True)),1)
        for parent in (a['id'],b['id'],c['id']):
            with self.assertRaises(ValueError):self.store.update_collection(a['id'],'A',parent)
        self.assertIsNone(next(item for item in self.store.list_collections() if item['id']==a['id'])['parentId'])
        self.store.update_collection(c['id'],'C renamed',None)
        self.store.delete_collection(a['id'])
        self.assertEqual([item['name'] for item in self.store.list_collections()],['C renamed'])
        self.assertEqual(len(self.store.list_documents()),1)
        self.store.delete_collection(c['id'])
        self.assertEqual(len(self.store.search_documents(unfiled=True)),1)
        self.assertTrue(self.source.exists())
        self.assertTrue(self.store.object_path(self.document).exists())

    def test_collection_keyword_and_multiple_tags_are_intersected(self):
        self.store.update_document(self.document['id'],{'title':'研究 α','tags':[' 方法 ','方法','基线'],'notes':'私有笔记'})
        self.assertEqual(self.store.document(self.document['id'])['tags'],['方法','基线'])
        c=self.store.create_collection('研究');self.store.set_membership(self.document['id'],c['id'])
        other_source=self.root/'other.pdf';other_source.write_bytes(b'%PDF-1.7 other');other=self.store.import_pdf(other_source)
        self.store.update_document(other['id'],{'title':'研究 α','tags':['方法']})
        matched=self.store.search_documents(collection_id=c['id'],text='Α',tags=['方法','基线'])
        self.assertEqual([d['id'] for d in matched],[self.document['id']])
        self.assertEqual(self.store.search_documents(tags=['方法','缺失']),[])
        self.assertEqual([d['id'] for d in self.store.search_documents(unfiled=True)],[other['id']])
        self.store.rename_tag('基线','方法')
        self.assertEqual(self.store.list_tags(),['方法'])
        self.store.rename_tag('方法',None)
        self.assertEqual(self.store.list_tags(),[])
        self.assertEqual(self.store.document(self.document['id'])['notes'],'私有笔记')

    def test_invalid_or_duplicate_collection_and_membership_do_not_mutate(self):
        collection=self.store.create_collection('A')
        for name in ('','  ','line\nbreak','x'*129):
            with self.assertRaises(ValueError):self.store.create_collection(name)
        with self.assertRaises(ValueError):self.store.create_collection('A')
        for doc_id,c_id in [('missing',collection['id']),(self.document['id'],'missing')]:
            with self.assertRaises(ValueError):self.store.set_membership(doc_id,c_id)
        with self.assertRaises(ValueError):self.store.set_membership(self.document['id'],collection['id'],1)
        with self.assertRaises(ValueError):self.store.search_documents(collection_id=collection['id'],unfiled=True)
        self.assertEqual(self.store.document_collections(self.document['id']),[])
        self.assertEqual(len(self.store.list_collections()),1)

    def test_v1_database_is_backed_up_and_content_is_preserved(self):
        original=self.store.object_path(self.document).read_bytes()
        with self.store.connection() as db:
            db.executescript('DROP TABLE desktop_memberships;DROP TABLE desktop_collections;PRAGMA user_version=1;')
        self.store.close()
        reopened=LocalStore(self.store.root)
        self.addCleanup(reopened.close)
        backup=self.store.root/'library-before-v2.sqlite3'
        self.assertTrue(backup.exists())
        db=sqlite3.connect(backup)
        try:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_documents').fetchone()[0],1)
        finally:db.close()
        self.assertEqual(reopened.document(self.document['id'])['title'],self.document['title'])
        self.assertEqual(hashlib.sha256(reopened.read_pdf(self.document['id'])).digest(),hashlib.sha256(original).digest())
        with reopened.connection() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
        reopened.create_collection('After migration')
        self.assertEqual(len(reopened.list_collections()),1)
