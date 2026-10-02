# SPDX-License-Identifier: AGPL-3.0-only
"""Independent zero-file bibliographic workflow vectors.

独立无文件书目向量，真实生成的PDF只用于后续附件和本地解析验证。
"""
import hashlib
from pathlib import Path
import tempfile
import unittest
import pymupdf
from polyscholar.service import LocalService


class BibliographicVectors(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root/'data')
        self.store = self.service.store

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def pdf(self, name, text):
        path = self.root/name
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((60,80),text)
            pdf.save(path)
        return path

    def test_zero_file_identity_has_no_object_and_rejects_pdf_operations(self):
        collection = self.service.create_collection('零文件集合')
        book = self.service.create_bibliographic_item({'itemType':'book','title':'无文件图书','date':'2024',
            'tags':['本地'],'notes':'本地笔记'},collection_id=collection['id'])
        self.assertIsNone(book['sha256'])
        self.assertEqual(book['fileKind'],'bibliographic')
        self.assertEqual(self.service.list_attachments(book['id']),[])
        self.assertIsNone(self.service.primary_pdf_id(book['id']))
        self.assertEqual(list(self.store.objects.iterdir()),[])
        with self.store.connection() as db:
            self.assertIsNone(db.execute('SELECT sha256 FROM desktop_documents WHERE id=?',(book['id'],)).fetchone()[0])
        for action in (lambda:self.service.read_pdf(book['id']),lambda:self.service.parse_document(book['id']),
                       lambda:self.service.start_translation(book['id']),lambda:self.store.new_job(book['id'],'babeldoc')):
            with self.assertRaisesRegex(ValueError,'PDF'):
                action()
        self.assertEqual(self.store.list_jobs(),[])
        self.assertEqual(self.service.search_documents(collection_id=collection['id'],tags=['本地'])[0]['id'],book['id'])
        self.assertIn('无文件图书',self.service.format_metadata(book['id'],'csl-json'))
        self.service.trash_document(book['id'])
        self.assertEqual(self.service.list_documents(),[])
        self.service.restore_document(book['id'])
        self.assertEqual(self.service.document_collections(book['id']),[collection['id']])
        self.service.trash_document(book['id'])
        self.assertTrue(self.service.purge_document(book['id'])['cleanupComplete'])
        self.assertEqual(self.store.list_documents(),[])
        self.assertEqual(self.service.list_pending_cleanup(),[])

    def test_real_pdf_added_after_creation_has_distinct_identity_and_primary_state(self):
        book = self.service.create_bibliographic_item({'title':'后续添加PDF','itemType':'book'})
        path = self.pdf('source.pdf','Real primary source for a zero-file bibliography.')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        file = self.service.import_attachment(book['id'],path)
        self.assertNotEqual(file['id'],book['id'])
        self.assertEqual(self.service.primary_pdf_id(book['id']),file['id'])
        self.assertEqual(self.service.read_pdf(file['id']),path.read_bytes())
        self.service.parse_document(file['id'])
        self.assertIn('Real primary',self.service.document_blocks(file['id'])[0]['text'])
        self.assertIsNone(self.store.document(book['id'])['sha256'])
        self.service.trash_document(file['id'])
        self.assertIsNone(self.service.primary_pdf_id(book['id']))
        second = self.service.import_attachment(book['id'],self.pdf('supplement.pdf','Distinct alternative source.'))
        self.assertIsNone(self.service.primary_pdf_id(book['id']))
        self.service.set_primary_pdf(book['id'],second['id'])
        self.assertEqual(self.service.primary_pdf_id(book['id']),second['id'])
        self.service.restore_document(file['id'])
        self.assertEqual(self.service.primary_pdf_id(book['id']),second['id'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
        self.service.close()
        self.service = LocalService(self.root/'data')
        self.store = self.service.store
        self.assertEqual(self.service.primary_pdf_id(book['id']),second['id'])
        self.assertEqual(len(self.service.list_attachments(book['id'])),2)

    def test_purging_primary_clears_pointer_without_selecting_existing_supplement(self):
        book = self.service.create_bibliographic_item({'title':'主PDF永久删除'})
        a = self.service.import_attachment(book['id'],self.pdf('a.pdf','First real primary.'))
        b = self.service.import_attachment(book['id'],self.pdf('b.pdf','Existing supplement.'))
        self.service.trash_document(a['id'])
        self.assertIsNone(self.service.primary_pdf_id(book['id']))
        self.assertTrue(self.service.purge_document(a['id'])['cleanupComplete'])
        self.assertIsNone(self.store.document(book['id']).get('primaryPdfId'))
        self.assertIsNone(self.service.primary_pdf_id(book['id']))
        self.assertEqual(self.service.list_attachments(book['id'])[0]['id'],b['id'])
        c = self.service.import_attachment(book['id'],self.pdf('c.pdf','New explicitly imported primary.'))
        self.assertEqual(self.service.primary_pdf_id(book['id']),c['id'])

    def test_merge_keeps_zero_file_source_as_non_file_record_and_real_evidence(self):
        a = self.service.create_bibliographic_item({'title':'无文件主书目','itemType':'book'})
        b = self.service.create_bibliographic_item({'title':'另一书目','itemType':'book','notes':'保留来源书目'})
        child = self.service.import_attachment(b['id'],self.pdf('child.pdf','Preserved merged bibliographic evidence.'))
        self.service.parse_document(child['id'])
        block = self.service.document_blocks(child['id'])[0]
        claim = self.service.save_claim(child['id'],'Local note',[{'blockId':block['id'],'quote':'Preserved'}])
        scope = [a['id'],b['id']]
        preview = self.service.merge_preview(scope,a['id'])
        self.service.merge_documents(scope,a['id'],{},preview['revision'])
        self.assertEqual(len(self.service.list_documents()),1)
        rows = self.service.list_attachments(a['id'])
        self.assertEqual([row['id'] for row in rows],[child['id']])
        self.assertEqual(self.store.document(b['id'])['parentDocumentId'],a['id'])
        self.assertIsNone(self.store.document(b['id'])['sha256'])
        self.assertEqual(self.service.list_claims(child['id'])[0]['id'],claim['id'])
        self.assertEqual(self.service.search_fulltext('Preserved')['items'][0]['parentDocumentId'],a['id'])
        self.assertEqual(len(self.service.list_merge_history(a['id'])),1)
        self.service.trash_document(a['id'])
        self.service.restore_document(a['id'])
        self.assertEqual(len(self.service.list_attachments(a['id'])),1)
        self.service.trash_document(a['id'])
        self.assertTrue(self.service.purge_document(a['id'])['cleanupComplete'])
        self.assertTrue((self.root/'child.pdf').exists())


if __name__=='__main__':
    unittest.main()
