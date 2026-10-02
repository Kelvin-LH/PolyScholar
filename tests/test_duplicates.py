# SPDX-License-Identifier: AGPL-3.0-only
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from polyscholar.service import LocalService
from polyscholar.store import LocalStore
from polyscholar.duplicates import normalized_isbn, normalized_doi

class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=LocalService(self.root/'data');self.store=self.service.store
        self.docs=[self.import_doc(str(i)) for i in range(3)]
    def tearDown(self):self.service.close();self.temp.cleanup()
    def import_doc(self,name):
        source=self.root/(name+'.pdf');source.write_bytes(('%PDF-1.7 duplicate '+name).encode())
        return self.service.import_pdf(source)
    def update(self,index,**fields):
        self.docs[index]=self.service.update_document(self.docs[index]['id'],fields)
    def merge(self,ids,master=None,sources=None):
        preview=self.service.merge_preview(ids,master)
        return self.service.merge_documents(ids,preview['masterId'],sources or {},preview['revision'])

    def test_title_author_known_year_and_identifier_formats(self):
        for index in (0,1):self.update(index,title=' Shared   title ',authors='Legacy Full Name',year=str(2020+index))
        self.update(2,title='Shared title',creators=[dict(type='organization',literal='Legacy Full Name')],year='2021')
        result=self.service.list_duplicate_candidates()
        self.assertEqual(result['total'],1);self.assertEqual(result['items'][0]['reasons'],['title_creator_year'])
        self.update(1,year='2022');self.assertEqual(self.service.list_duplicate_candidates()['total'],0)
        self.update(1,year='2021',creators=[dict(role='editor',literal='Legacy Full Name')])
        self.assertEqual(self.service.list_duplicate_candidates()['total'],0)
        self.assertEqual(normalized_doi(' DOI:10.1234/A.B '),'10.1234/a.b')
        self.assertIsNone(normalized_doi('10.1234/a\x80b'))
        self.assertEqual(normalized_isbn('0-306-40615-2'),normalized_isbn('9780306406157'))
        self.assertIsNone(normalized_isbn('4006381333931'))
        self.assertIsNone(normalized_isbn('9780306406158'))

    def test_candidates_exact_total_and_explicit_resource_rejection(self):
        for index in range(3):self.update(index,doi='10.1234/same')
        result=self.service.list_duplicate_candidates(limit=1)
        self.assertEqual((result['total'],len(result['items']),result['truncated']),(3,1,True))
        with patch('polyscholar.duplicates.MAX_CANDIDATE_PAIRS',2):
            with self.assertRaises(ValueError):self.service.list_duplicate_candidates()
        with patch('polyscholar.duplicates.MAX_CANDIDATE_ROOTS',2):
            with self.assertRaises(ValueError):self.service.list_duplicate_candidates()
        with patch('polyscholar.duplicates.MAX_CANDIDATE_BYTES',10):
            with self.assertRaises(ValueError):self.service.list_duplicate_candidates()
        self.service.trash_document(self.docs[2]['id'])
        self.assertEqual(self.service.list_duplicate_candidates()['total'],1)

    def test_retained_pdf_identity_ir_claims_jobs_fulltext_and_field_selection(self):
        ids=[doc['id'] for doc in self.docs[:2]]
        self.update(0,title='Master',notes='Keep A',tags=['A'],date='2020')
        self.update(1,title='Preferred',notes='Keep B',tags=['B'],date='2021',creators=[dict(type='person',family='Doe',given='Jane')])
        child_source=self.root/'child.pdf';child_source.write_bytes(b'%PDF-1.7 child duplicate')
        child=self.service.import_attachment(ids[1],child_source)
        self.store.replace_document_ir(ids[1],'fixture',[dict(number=1,width=100,height=100,blocks=[dict(text='Retained source text',order=0,bbox=[.1,.2,.7,.8])])])
        block=self.service.document_blocks(ids[1])[0]
        claim=self.service.save_claim(ids[1],'Retained claim',[dict(blockId=block['id'],quote='Retained')])
        job=self.store.new_job(ids[1],'babeldoc');job['state']='failed';self.store.put_job(job)
        before_source=self.store.document(ids[1]);before_path=self.store.object_path(before_source)
        collection=self.service.create_collection('Source collection');self.service.set_membership(ids[1],collection['id'])
        self.service.trash_document(child['id'])
        result=self.merge(ids,ids[0],dict(title=ids[1],creators=ids[1],date=ids[1],year=ids[1]))
        master=self.store.document(ids[0]);self.assertEqual(master['title'],'Preferred');self.assertEqual(master['year'],'2021')
        self.assertEqual(master['tags'],['A','B']);self.assertIn('Keep A',master['notes']);self.assertIn('Keep B',master['notes'])
        source=self.store.document(ids[1]);source.pop('parentDocumentId')
        # Root-only derived selection flags change when an identity becomes a child.
        # 根条目转为子记录后，根专用选择状态不属于保留元数据。
        before_source.pop('primaryPdfId');before_source.pop('hasAnyPdf')
        self.assertEqual(source,before_source);self.assertTrue(before_path.exists())
        self.assertEqual(self.service.list_claims(ids[1])[0]['id'],claim['id'])
        self.assertEqual(self.service.list_jobs()[0]['id'],job['id'])
        self.assertEqual(self.service.document_collections(ids[0]),[collection['id']])
        self.assertEqual(self.service.search_fulltext('Retained')['items'][0]['parentDocumentId'],ids[0])
        self.assertEqual(self.service.import_pdf(self.root/'1.pdf')['id'],ids[0])
        self.assertEqual(self.service.list_trash()[0]['parentDocumentId'],ids[0])
        self.service.restore_document(child['id'])
        self.assertEqual(len(self.service.list_attachments(ids[0])),3)
        history=self.service.list_merge_history(ids[0])[0]
        self.assertEqual(history['id'],result['historyId']);self.assertEqual(history['snapshot']['documents'][0]['title'],'Master')
        self.service.close();self.service=LocalService(self.root/'data');self.store=self.service.store
        self.assertEqual(len(self.service.list_merge_history(ids[0])),1)
        self.service.trash_document(ids[0]);self.assertEqual(self.service.search_fulltext('Retained')['total'],0)
        self.service.purge_document(ids[0])
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_merge_history').fetchone()[0],0)

    def test_revision_drift_and_audit_failure_are_atomic(self):
        ids=[doc['id'] for doc in self.docs[:2]];preview=self.service.merge_preview(ids)
        self.service.update_document(ids[1],dict(notes='Changed after preview'))
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.service.merge_documents(ids,ids[0],{},preview['revision'])
        preview=self.service.merge_preview(ids)
        with self.store.connection() as db:db.execute("CREATE TRIGGER fail_merge BEFORE INSERT ON desktop_audit WHEN NEW.point='documents_merged' BEGIN SELECT RAISE(ABORT,'failure'); END")
        with self.assertRaises(sqlite3.Error):self.service.merge_documents(ids,ids[0],{},preview['revision'])
        self.assertEqual(len(self.service.list_documents()),3)
        self.assertEqual(self.service.list_merge_history(ids[0]),[])
        self.assertEqual(self.service.merge_preview(ids)['revision'],preview['revision'])
        for master in (0,False,'',[],{}):
            with self.assertRaises(ValueError):self.service.merge_preview(ids,master)
        for fields in (None,[],dict(itemType=ids[0]),dict(title='unselected')):
            with self.assertRaises(ValueError):self.service.merge_documents(ids,ids[0],fields,preview['revision'])

    def test_multiple_merges_preserve_source_history_attribution(self):
        ids=[doc['id'] for doc in self.docs]
        first=self.merge(ids[:2],ids[0])
        second=self.merge([ids[2],ids[0]],ids[2])
        history=self.service.list_merge_history(ids[2])
        self.assertEqual({entry['id'] for entry in history},{first['historyId'],second['historyId']})
        self.assertEqual(next(entry for entry in history if entry['id']==first['historyId'])['masterId'],ids[0])
        self.assertEqual(len(self.service.list_attachments(ids[2])),3)

    def test_note_limits_and_date_conflict_reject_without_merging(self):
        ids=[doc['id'] for doc in self.docs[:2]]
        self.update(0,date='2020',notes='A'*40000);self.update(1,date='2021',notes='B'*40000)
        preview=self.service.merge_preview(ids)
        with self.assertRaisesRegex(ValueError,'年份'):
            self.service.merge_documents(ids,ids[0],dict(date=ids[1]),preview['revision'])
        with self.assertRaisesRegex(ValueError,'笔记'):
            self.service.merge_documents(ids,ids[0],{},preview['revision'])
        self.assertEqual(len(self.service.list_documents()),3)
        self.assertEqual(self.service.list_merge_history(ids[0]),[])

    def test_repeated_merge_deduplicates_contributions_and_preserves_manual_edit(self):
        ids=[doc['id'] for doc in self.docs]
        for index,note in enumerate(('A','B','A')):
            self.update(index,notes=note)
        self.merge(ids[:2],ids[0])
        self.merge([ids[2],ids[0]],ids[2])
        master=self.store.document(ids[2])
        self.assertEqual([entry['text'] for entry in master['mergeNoteSources']],['A','B'])
        self.assertEqual(master['notes'].count('\nA'),1)
        self.assertEqual(master['notes'].count('\nB'),1)
        self.assertEqual(self.store.document(ids[1])['notes'],'B')
        new=self.import_doc('new')
        self.service.update_document(new['id'],dict(notes='A'))
        self.service.update_document(ids[2],dict(notes='Hand edited original note'))
        self.merge([ids[2],new['id']],ids[2])
        edited=self.store.document(ids[2])
        self.assertEqual([entry['text'] for entry in edited['mergeNoteSources']],['Hand edited original note','A'])
        self.assertNotIn('\nB',edited['notes'])
        self.assertEqual(self.store.document(ids[0])['notes'].count('\nB'),1)

    def test_note_expansion_requires_safe_internal_structure_and_exact_text(self):
        from polyscholar.duplicates import note_contributions
        for stored in (None,[dict(documentId='id',text='A',extra=True)],[dict(documentId='id',text='\ud800')],
                       [dict(documentId='id',text='A')]*2001):
            result=note_contributions(dict(id='root',notes='Literal [文献 id]\nA',mergeNoteSources=stored))
            self.assertEqual(result,[dict(documentId='root',text='Literal [文献 id]\nA')])
        exact=dict(id='root',notes='[文献 id]\nA',mergeNoteSources=[dict(documentId='id',text='A')])
        self.assertEqual(note_contributions(exact),[dict(documentId='id',text='A')])

    def test_sql_deadline_interrupts_query_and_restores_handler(self):
        with self.store.connection() as db:
            with self.assertRaisesRegex(ValueError,'30 秒'):
                with self.store._bounded_duplicate_sql(db,0):
                    db.execute("WITH RECURSIVE seq(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM seq WHERE x<500) SELECT SUM(a.x*b.x) FROM seq a CROSS JOIN seq b").fetchone()
            self.assertEqual(db.execute('SELECT 123').fetchone()[0],123)
        self.assertEqual(self.service.list_duplicate_candidates()['total'],0)

    def test_v9_migration_failure_backup_and_restart(self):
        self.service.close();path=self.root/'data'/'library.sqlite3'
        with closing(sqlite3.connect(path)) as db:db.executescript('DROP TABLE desktop_merge_history;PRAGMA user_version=9;')
        with patch('polyscholar.store.MERGE_SCHEMA','CREATE TABLE broken('):
            with self.assertRaises(sqlite3.Error):LocalStore(self.root/'data')
        with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9)
        with closing(sqlite3.connect(self.root/'data'/'library-before-v10.sqlite3')) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9)
        self.service=LocalService(self.root/'data');self.store=self.service.store
        self.assertEqual(len(self.service.list_documents()),3)
        self.assertEqual(self.service.list_merge_history(self.docs[0]['id']),[])

if __name__=='__main__':unittest.main()
