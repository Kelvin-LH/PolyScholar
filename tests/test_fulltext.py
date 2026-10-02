# SPDX-License-Identifier: AGPL-3.0-only
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import pymupdf
from polyscholar.service import LocalService
from polyscholar.store import LocalStore

def pages(*texts):
    return [dict(number=i+1,width=600,height=800,blocks=[dict(text=text,bbox=[.1,.2,.7,.5],order=0)]) for i,text in enumerate(texts)]

class FulltextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=LocalService(self.root/'data')
        self.docs=[]
        for i in range(3):
            source=self.root/f'{i}.pdf';source.write_bytes(f'%PDF-1.7 index {i}'.encode())
            self.docs.append(self.service.import_pdf(source))
    def tearDown(self):self.service.close();self.temp.cleanup()
    def parse(self,index,*texts):return self.service.store.replace_document_ir(self.docs[index]['id'],'fixture',pages(*texts))

    def test_literal_unicode_short_and_long_candidates(self):
        self.parse(0,'Straße 中文 "quote" 100% _ NEAR(x) OR abc-xyz')
        self.parse(1,'strasse substring test')
        for needle,count in [('STRASSE',2),('中文',1),('中',1),('"quote"',1),('100%',1),('_',1),('NEAR(x)',1),('abc-xyz',1),('OR',1),('abc OR missing',0)]:
            with self.subTest(needle=needle):
                result=self.service.search_fulltext(needle)
                self.assertEqual(result['total'],count)
                for item in result['items']:
                    self.assertNotIn('text',item)
                    self.assertEqual(item['page'],item['pageNumber'])
                    self.assertEqual(item['bbox'],[.1,.2,.7,.5])
                    self.assertIn(item['snippet'].strip('…'),self.service.store.document_blocks(item['documentId'])[0]['text'])
        result=self.service.search_fulltext('strasse',limit=1)
        self.assertEqual((result['total'],len(result['items']),result['truncated']),(2,1,True))
        self.assertEqual(self.service.search_fulltext('')['items'],[])
        self.assertEqual(len(self.service.search_fulltext('')['coverage']),3)

    def test_family_scope_parent_metadata_and_no_old_revision_results(self):
        source=self.root/'attachment.pdf';source.write_bytes(b'%PDF-1.7 child index')
        child=self.service.import_attachment(self.docs[0]['id'],source)
        self.service.store.replace_document_ir(child['id'],'fixture',pages('Unique supplement phrase'))
        self.service.update_document(self.docs[0]['id'],dict(title='Parent title',tags=['Group']))
        collection=self.service.create_collection('Collection')
        self.service.set_membership(self.docs[0]['id'],collection['id'])
        query=dict(match='all',conditions=[dict(field='title',operator='is',value='Parent title')])
        result=self.service.search_fulltext('supplement',collection_id=collection['id'],tags=['Group'],query=query,metadata_text='Parent')
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['documentId'],child['id'])
        self.assertEqual(result['items'][0]['parentDocumentId'],self.docs[0]['id'])
        self.assertEqual({c['documentId'] for c in result['coverage']},{self.docs[0]['id'],child['id']})
        self.assertEqual(self.service.search_fulltext('supplement',unfiled=True)['total'],0)
        previous=self.parse(0,'old-only phrase')
        current=self.parse(0,'new-only phrase')
        self.assertNotEqual(previous['id'],current['id'])
        self.assertEqual(self.service.search_fulltext('old-only')['total'],0)
        self.assertEqual(self.service.search_fulltext('new-only')['items'][0]['revisionId'],current['id'])

    def test_failure_coverage_clear_rebuild_restart_preserve_ir_evidence(self):
        revision=self.parse(0,'Evidence survives clearing')
        block=self.service.store.document_blocks(self.docs[0]['id'])[0]
        claim=self.service.save_claim(self.docs[0]['id'],'Note',[dict(blockId=block['id'],quote='Evidence')])
        self.parse(1,'')
        with patch.object(self.service,'_parse_document',side_effect=ValueError('private raw error')):
            for identifier in (self.docs[0]['id'],self.docs[2]['id']):
                with self.assertRaises(ValueError):self.service.parse_document(identifier)
        coverage={row['documentId']:row for row in self.service.search_fulltext('')['coverage']}
        self.assertEqual(coverage[self.docs[0]['id']]['status'],'indexed')
        self.assertTrue(coverage[self.docs[0]['id']]['previousCurrent'])
        self.assertEqual(coverage[self.docs[0]['id']]['lastError'],'parse_failed')
        self.assertEqual(coverage[self.docs[1]['id']]['status'],'no_text')
        self.assertEqual(coverage[self.docs[2]['id']]['status'],'parse_failed')
        self.service.clear_fulltext_index(self.docs[0]['id'])
        self.assertEqual(self.service.search_fulltext('Evidence')['total'],0)
        self.assertEqual(self.service.current_document_ir(self.docs[0]['id'])['id'],revision['id'])
        self.assertEqual(self.service.list_claims(self.docs[0]['id'])[0]['id'],claim['id'])
        self.service.close();self.service=LocalService(self.root/'data')
        self.assertEqual(self.service.search_fulltext('Evidence')['total'],0)
        self.service.rebuild_fulltext_index(self.docs[0]['id'])
        self.assertEqual(self.service.search_fulltext('Evidence')['total'],1)
        self.assertEqual(self.service.search_fulltext('Evidence')['coverage'][0]['lastError'],'parse_failed')
        self.parse(0,'Successfully reparsed')
        self.assertIsNone(self.service.search_fulltext('')['coverage'][0]['lastError'])

    def test_index_write_and_rebuild_audit_failures_roll_back(self):
        revision=self.parse(0,'Original current text')
        with self.service.store.connection() as db:
            db.execute("CREATE TRIGGER index_fail BEFORE INSERT ON desktop_fulltext_blocks BEGIN SELECT RAISE(ABORT,'failure'); END")
        with self.assertRaises(sqlite3.Error):self.parse(0,'Replacement current text')
        self.assertEqual(self.service.current_document_ir(self.docs[0]['id'])['id'],revision['id'])
        self.assertEqual(self.service.search_fulltext('Original')['total'],1)
        self.assertEqual(self.service.search_fulltext('Replacement')['total'],0)
        with self.service.store.connection() as db:
            db.execute('DROP TRIGGER index_fail')
            db.execute("CREATE TRIGGER fulltext_audit_fail BEFORE INSERT ON desktop_audit WHEN NEW.point LIKE 'fulltext_index_%' BEGIN SELECT RAISE(ABORT,'failure'); END")
        with self.assertRaises(sqlite3.Error):self.service.clear_fulltext_index()
        self.assertEqual(self.service.search_fulltext('Original')['total'],1)
        with self.assertRaises(sqlite3.Error):self.service.rebuild_fulltext_index()
        self.assertEqual(self.service.search_fulltext('Original')['total'],1)

    def test_v7_backup_backfill_and_migration_failure(self):
        revision=self.parse(0,'Backfilled current text')
        self.service.save_saved_search('Saved',dict(match='all',conditions=[dict(field='title',operator='is_not_empty',value='')]))
        self.service.close();path=self.root/'data'/'library.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('DROP TABLE desktop_fulltext_fts; DROP TABLE desktop_fulltext_blocks; DROP TABLE desktop_fulltext_status; PRAGMA user_version=7;')
        with patch('polyscholar.store.index_current_ir',side_effect=RuntimeError('migration failure')):
            with self.assertRaises(RuntimeError):LocalStore(self.root/'data')
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name='desktop_fulltext_blocks'").fetchone())
        with closing(sqlite3.connect(self.root/'data'/'library-before-v8.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
        self.service=LocalService(self.root/'data')
        result=self.service.search_fulltext('Backfilled')
        self.assertEqual(result['total'],1);self.assertEqual(result['items'][0]['revisionId'],revision['id'])
        self.assertEqual(len(self.service.list_saved_searches()),1)

    def test_delete_cascades_index_and_fts_and_real_local_pdf_parser(self):
        source=self.root/'real.pdf'
        pdf=pymupdf.open();page=pdf.new_page();page.insert_text((60,90),'Local searchable PDF example');pdf.save(source);pdf.close()
        doc=self.service.import_pdf(source)
        self.service.parse_document(doc['id'])
        result=self.service.search_fulltext('searchable PDF')
        self.assertEqual(result['total'],1);self.assertEqual(result['items'][0]['page'],1)
        self.service.delete_document(doc['id'])
        self.service.purge_document(doc['id'])
        self.assertEqual(self.service.search_fulltext('searchable PDF')['total'],0)
        with self.service.store.connection() as db:
            db.execute("INSERT INTO desktop_fulltext_fts(desktop_fulltext_fts,rank) VALUES('integrity-check',1)")

    def test_sql_progress_deadline_interrupts_without_partial_and_connection_recovers(self):
        self.parse(0,*['Large matching candidate text']*2000)
        ticks = [0]
        def clock():
            ticks[0]+=1
            # Entry, metadata completion and three coverage rows finish first;
            # subsequent SQLite progress callbacks cross the 30-second bound.
            return 0 if ticks[0] <= 5 else 31
        with patch('polyscholar.fulltext.time.monotonic',side_effect=clock):
            with self.assertRaisesRegex(ValueError,'30 秒'):
                self.service.search_fulltext('matching')
        self.assertGreater(ticks[0],5)
        normal=self.service.search_fulltext('matching',limit=2)
        self.assertEqual(normal['total'],2000)
        self.assertEqual(len(normal['items']),2)
        self.assertTrue(normal['truncated'])

    def test_input_validation_and_bounded_snippet_of_large_block(self):
        self.parse(0,'ß'*100000+' long matching text '+'Z'*100000)
        result=self.service.search_fulltext('s'*3000)
        self.assertEqual(result['total'],1)
        self.assertLessEqual(len(result['items'][0]['snippet']),512)
        self.assertNotIn('text',result['items'][0])
        for text,criteria in [('a',dict(limit=True)),('a',dict(limit=0)),('a',dict(limit=1001)),('a\x00b',{}),('x'*4097,{})]:
            with self.assertRaises(ValueError):self.service.search_fulltext(text,**criteria)

if __name__=='__main__':unittest.main()
