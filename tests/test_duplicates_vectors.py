# SPDX-License-Identifier: AGPL-3.0-only
"""Independent bibliographic merge vectors, using synthetic IR rather than engine output.

独立书目合并向量；合成 IR 不作为真实解析或翻译验收。
"""
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from polyscholar.service import LocalService


class DuplicateVectors(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root / 'data')
        self.store = self.service.store

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def document(self, name, **metadata):
        path = self.root / f'{name}.pdf'
        path.write_bytes(b'%PDF-1.7 independent ' + name.encode())
        document = self.service.import_pdf(path)
        if metadata:
            document = self.service.update_document(document['id'], metadata)
        return document

    def test_identifier_candidates_exclude_invalid_and_empty_values(self):
        a = self.document('doi-a', doi='https://doi.org/10.1234/Alpha')
        b = self.document('doi-b', doi=' DOI:10.1234/alpha ')
        self.document('invalid-a', doi='not a DOI', isbn='0306406153')
        self.document('invalid-b', doi='not a DOI', isbn='0306406153')
        self.document('ean-a', isbn='4006381333931')
        self.document('ean-b', isbn='4006381333931')
        c = self.document('isbn-a', itemType='book', isbn='0-306-40615-2')
        d = self.document('isbn-b', itemType='book', isbn='978-0-306-40615-7')
        result = self.service.list_duplicate_candidates(limit=1)
        self.assertEqual(result['total'], 2)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['items']), 1)
        items = self.service.list_duplicate_candidates()['items']
        reasons = {frozenset(row['documentIds']): row['reasons'] for row in items}
        self.assertEqual(set(reasons), {frozenset([a['id'],b['id']]),frozenset([c['id'],d['id']])})
        self.assertIn('doi', reasons[frozenset([a['id'],b['id']])])
        self.assertIn('isbn', reasons[frozenset([c['id'],d['id']])])

    def test_title_candidates_require_creator_identity_year_and_type(self):
        creator = dict(role='author', type='person', literal='Ada Lovelace')
        a = self.document('a', title='  Shared   TITLE  ', year='2020', creators=[creator])
        b = self.document('b', title='shared title', year='2021', creators=[creator])
        self.document('c', title='shared title', year='2024', creators=[creator])
        self.document('d', title='shared title', year='', creators=[creator])
        self.document('e', title='shared title', year='2020', creators=[dict(role='author',type='organization',literal='Ada Lovelace')])
        self.document('f', title='shared title', year='2020', itemType='book', creators=[creator])
        result = self.service.list_duplicate_candidates()
        self.assertEqual(result['total'], 1)
        self.assertEqual(set(result['items'][0]['documentIds']), {a['id'], b['id']})
        self.assertIn('title_creator_year', result['items'][0]['reasons'])

    def test_merge_preserves_source_ids_hidden_child_and_current_evidence(self):
        a = self.document('master', title='Master title', notes='Master note', tags=['A'], date='2020')
        b = self.document('source', title='Selected title', notes='Source note', tags=['B'], date='2021', doi='10.1234/source')
        extra = self.root / 'hidden.pdf'
        extra.write_bytes(b'%PDF-1.7 hidden independent child')
        child = self.service.import_attachment(b['id'], extra)
        self.service.trash_document(child['id'])
        collection = self.service.create_collection('From source')
        self.service.set_membership(b['id'], collection['id'])
        self.store.replace_document_ir(b['id'], 'independent fixture', [dict(number=1,width=100,height=100,
            blocks=[dict(text='Preserved independent source',bbox=[0,0,1,1],order=0)])])
        block = self.service.document_blocks(b['id'])[0]
        claim = self.service.save_claim(b['id'], 'Retained claim', [dict(blockId=block['id'],quote='independent')])
        original_path = self.store.object_path(b)
        original_hash = hashlib.sha256(original_path.read_bytes()).hexdigest()
        scope = [a['id'], b['id']]
        preview = self.service.merge_preview(scope, a['id'])
        self.service.merge_documents(scope, a['id'], {'title':b['id'],'doi':b['id'],'date':b['id'],'year':b['id']}, preview['revision'])
        roots = self.service.list_documents()
        self.assertEqual(len(roots), 1)
        merged = roots[0]
        self.assertEqual(merged['title'], 'Selected title')
        self.assertEqual(merged['year'], '2021')
        self.assertEqual(set(merged['tags']), {'A','B'})
        self.assertIn('Master note', merged['notes'])
        self.assertIn('Source note', merged['notes'])
        self.assertEqual(self.service.document_collections(a['id']), [collection['id']])
        self.assertEqual(self.store.document(b['id'])['parentDocumentId'], a['id'])
        self.assertEqual(self.store.document(b['id'])['notes'], 'Source note')
        self.assertEqual(self.store.document(child['id'])['parentDocumentId'], a['id'])
        self.assertEqual(self.service.list_trash()[0]['documentId'], child['id'])
        self.assertEqual(self.service.list_claims(b['id'])[0]['id'], claim['id'])
        hit = self.service.search_fulltext('independent')['items'][0]
        self.assertEqual(hit['documentId'], b['id'])
        self.assertEqual(hit['parentDocumentId'], a['id'])
        self.assertEqual(hashlib.sha256(original_path.read_bytes()).hexdigest(), original_hash)
        self.assertEqual(self.service.import_pdf(self.root / 'source.pdf')['id'], a['id'])
        history = self.service.list_merge_history(a['id'])
        self.assertEqual(len(history), 1)
        self.service.close()
        self.service = LocalService(self.root / 'data')
        self.store = self.service.store
        self.service.restore_document(child['id'])
        self.assertEqual(len(self.service.list_attachments(a['id'])), 3)
        self.assertEqual(len(self.service.list_merge_history(a['id'])), 1)
        self.service.trash_document(a['id'])
        self.assertTrue(self.service.purge_document(a['id'])['cleanupComplete'])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_merge_history').fetchone()[0], 0)
        self.assertTrue((self.root/'source.pdf').exists())

    def test_preview_drift_and_audit_failure_preserve_original_roots(self):
        a = self.document('a')
        b = self.document('b')
        scope = [a['id'],b['id']]
        preview = self.service.merge_preview(scope,a['id'])
        collection = self.service.create_collection('Changed membership')
        self.service.set_membership(b['id'],collection['id'])
        with self.assertRaises(ValueError):
            self.service.merge_documents(scope,a['id'],{},preview['revision'])
        self.assertEqual(len(self.service.list_documents()),2)
        preview = self.service.merge_preview(scope,a['id'])
        with self.store.connection() as db:
            db.execute("CREATE TRIGGER fail_merge BEFORE INSERT ON desktop_audit WHEN NEW.point='documents_merged' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(sqlite3.Error):
            self.service.merge_documents(scope,a['id'],{},preview['revision'])
        self.assertEqual(len(self.service.list_documents()),2)
        self.assertEqual(len(self.service.list_attachments(a['id'])),1)
        self.assertEqual(self.service.document_collections(b['id']),[collection['id']])
        self.assertEqual(self.service.list_merge_history(a['id']),[])

    def test_actual_live_child_process_blocks_family_merge_until_reaped(self):
        a = self.document('a')
        b = self.document('b')
        path = self.root / 'parser-child.pdf'
        path.write_bytes(b'%PDF-1.7 independently guarded child')
        child_document = self.service.import_attachment(b['id'], path)
        scope = [a['id'],b['id']]
        preview = self.service.merge_preview(scope, a['id'])
        # 真正运行的合成子进程只检验生命周期，不作为实际解析器验收。
        # A live synthetic process verifies lifecycle guards, not parser acceptance.
        child = subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])
        key = 'independent-live-child'
        self.service._children[key] = child
        self.service._child_documents[key] = child_document['id']
        try:
            with self.assertRaises(ValueError):
                self.service.merge_documents(scope,a['id'],{},preview['revision'])
            self.assertEqual(len(self.service.list_documents()),2)
        finally:
            child.terminate()
            child.wait(timeout=5)
            self.service._children.pop(key)
            self.service._child_documents.pop(key)
        self.service.merge_documents(scope,a['id'],{},preview['revision'])
        self.assertEqual(len(self.service.list_documents()),1)

    def test_repeated_merge_deduplicates_contributions_and_respects_manual_notes(self):
        documents = [self.document(str(index), notes=note) for index,note in enumerate(('unique-A','unique-B','unique-A','unique-A'))]
        ids = [document['id'] for document in documents]
        for scope in (ids[:2],[ids[0],ids[2]]):
            preview = self.service.merge_preview(scope, ids[0])
            self.service.merge_documents(scope,ids[0],{},preview['revision'])
        notes = self.store.document(ids[0])['notes']
        self.assertEqual(notes.count('unique-A'),1)
        self.assertEqual(notes.count('unique-B'),1)
        self.assertEqual(notes.count('[文献 '),2)
        self.service.update_document(ids[0],{'notes':'用户手改后的真实笔记'})
        scope = [ids[0],ids[3]]
        preview = self.service.merge_preview(scope,ids[0])
        self.service.merge_documents(scope,ids[0],{},preview['revision'])
        notes = self.store.document(ids[0])['notes']
        self.assertIn('用户手改后的真实笔记',notes)
        self.assertEqual(notes.count('unique-A'),1)
        self.assertNotIn('unique-B',notes)
        self.assertEqual(self.store.document(ids[1])['notes'],'unique-B')

    def test_field_sources_reject_inconsistent_dates_and_unrelated_documents(self):
        a = self.document('a', date='2020')
        b = self.document('b', date='2021')
        c = self.document('c')
        scope = [a['id'],b['id']]
        preview = self.service.merge_preview(scope,a['id'])
        for sources in ({'date':b['id'],'year':a['id']},{'title':c['id']},{'sha256':b['id']},{'itemType':b['id']}):
            with self.assertRaises(ValueError):
                self.service.merge_documents(scope,a['id'],sources,preview['revision'])
        self.assertEqual(len(self.service.list_documents()),3)
        self.assertEqual(self.service.list_merge_history(a['id']),[])


if __name__ == '__main__':
    unittest.main()
