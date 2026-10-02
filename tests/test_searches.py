# SPDX-License-Identifier: AGPL-3.0-only
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from polyscholar.service import LocalService
from polyscholar.store import LocalStore
from polyscholar.searches import validate_query

def rule(field, operator, value=''):
    return dict(field=field, operator=operator, value=value)
def query(*conditions, match='all'):
    return dict(match=match, conditions=list(conditions))

class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root/'data', self.root/'resources')
        self.docs=[]
        for i, fields in enumerate([
            dict(title='Straße 学术', year='2022', tags=['Methods','中文'], creators=[dict(type='person',family='Doe',given='Jane'),dict(role='editor',type='organization',literal='Committee')]),
            dict(title='Research note',year='2024',authors='Unparsed Author',tags=['中文'],itemType='book',publisher='Book Press',isbn='978111'),
            dict(title='Undated',year='unknown',tags=[]),
        ]):
            source=self.root/f'{i}.pdf';source.write_bytes(f'%PDF-1.7 search {i}'.encode())
            doc=self.service.import_pdf(source)
            self.docs.append(self.service.update_document(doc['id'],fields))
    def tearDown(self):
        self.service.close();self.temp.cleanup()
    def ids(self, **criteria):
        return {d['id'] for d in self.service.search_documents(**criteria)}

    def test_all_any_unicode_and_multivalue_negatives(self):
        self.assertEqual(self.ids(query=query(rule('title','contains','STRASSE'))),{self.docs[0]['id']})
        self.assertEqual(self.ids(query=query(rule('creator','is','committee'))),{self.docs[0]['id']})
        self.assertEqual(self.ids(query=query(rule('creator','is','Unparsed Author'))),{self.docs[1]['id']})
        self.assertEqual(self.ids(query=query(rule('tag','is_not','中文'))),{self.docs[2]['id']})
        self.assertEqual(self.ids(query=query(rule('creator','not_contains','doe'))),{self.docs[1]['id'],self.docs[2]['id']})
        self.assertEqual(self.ids(query=query(rule('tag','is','中文'),rule('year','before','2023'))),{self.docs[0]['id']})
        self.assertEqual(self.ids(query=query(rule('title','is','Undated'),rule('itemType','is','book'),match='any')),{self.docs[1]['id'],self.docs[2]['id']})
        self.assertEqual(self.ids(query=query(rule('publisher','is_empty'))),{self.docs[0]['id'],self.docs[2]['id']})
        self.assertEqual(self.ids(query=query(rule('isbn','is_not_empty'))),{self.docs[1]['id']})
        self.assertEqual(self.ids(query=query(rule('year','after','2021'))),{self.docs[0]['id'],self.docs[1]['id']})

    def test_advanced_intersects_existing_scopes_and_excludes_attachments(self):
        parent=self.service.create_collection('Parent');child=self.service.create_collection('Child',parent['id'])
        self.service.set_membership(self.docs[0]['id'],child['id'])
        criteria=query(rule('tag','is','中文'))
        self.assertEqual(self.ids(collection_id=parent['id'],include_descendants=True,tags=['Methods'],text='学术',query=criteria),{self.docs[0]['id']})
        self.assertEqual(self.ids(unfiled=True,query=criteria),{self.docs[1]['id']})
        source=self.root/'supplement.pdf';source.write_bytes(b'%PDF-1.7 supplement-search')
        attachment=self.service.import_attachment(self.docs[0]['id'],source)
        self.service.update_document(attachment['id'],dict(title='Hidden unique attachment',tags=['中文']))
        self.assertEqual(self.ids(query=query(rule('title','contains','Hidden unique'))),set())
        self.assertEqual(self.ids(query=criteria),{self.docs[0]['id'],self.docs[1]['id']})

    def test_invalid_rules_reject_before_write_and_existing_update_is_atomic(self):
        valid=query(rule('title','contains','学术'))
        saved=self.service.save_saved_search('Academic',valid)
        badqueries=[dict(match='all',conditions=[]),query(*([rule('title','is','x')]*21)),query(rule('unknown','is','x')),
                    query(rule('title','before','2020')),query(rule('year','before','２０２０')),query(rule('year','after',True)),
                    query(rule('year','before','0')),query(rule('title','contains','  ')),query(rule('title','is_empty','x')),
                    query(rule('notes','contains','中'*1400)),dict(**valid,collectionId='secret'),query(dict(field='title',operator='is'))]
        for invalid in badqueries:
            with self.subTest(invalid=repr(invalid)[:80]):
                with self.assertRaises(ValueError):
                    self.service.save_saved_search('Modified',invalid,saved['id'])
                with self.assertRaises(ValueError):
                    self.service.search_documents(query=invalid)
                self.assertEqual(self.service.list_saved_searches(),[saved])
        with self.assertRaises(ValueError):self.service.save_saved_search('ACADEMIC',valid)
        with self.assertRaises(ValueError):self.service.save_saved_search('line\u2028other',valid)
        with self.assertRaises(ValueError):self.service.save_saved_search('Missing',valid,'missing')
        self.assertEqual(self.service.list_saved_searches(),[saved])

    def test_saved_rules_dynamic_restart_update_delete_and_no_ids_or_scope(self):
        saved=self.service.save_saved_search('Recent',query(rule('year','after','2023')))
        self.assertEqual(self.ids(query=saved['query']),{self.docs[1]['id']})
        self.service.update_document(self.docs[0]['id'],dict(year='2025'))
        self.assertEqual(self.ids(query=saved['query']),{self.docs[0]['id'],self.docs[1]['id']})
        self.service.close();self.service=LocalService(self.root/'data',self.root/'resources')
        self.assertEqual(self.service.list_saved_searches(),[saved])
        updated=self.service.save_saved_search('Older',query(rule('year','before','2024')),saved['id'])
        self.assertEqual(updated['createdAt'],saved['createdAt'])
        with self.service.store.connection() as db:
            raw=db.execute('SELECT query FROM desktop_saved_searches').fetchone()[0]
        self.assertEqual(json.loads(raw),updated['query'])
        for doc in self.docs:self.assertNotIn(doc['id'],raw)
        self.assertNotIn(str(self.root),raw)
        self.service.delete_saved_search(saved['id'])
        self.assertEqual(self.service.list_saved_searches(),[])
        with self.assertRaises(ValueError):self.service.delete_saved_search(saved['id'])
        self.assertEqual(len(self.service.search_documents()),3)

    def test_audit_failure_rolls_back_create_update_and_delete(self):
        saved=self.service.save_saved_search('Original',query(rule('title','contains','学术')))
        with self.service.store.connection() as db:
            db.execute("CREATE TRIGGER fail_saved_audit BEFORE INSERT ON desktop_audit WHEN NEW.point LIKE 'saved_search_%' BEGIN SELECT RAISE(ABORT,'failure'); END")
        for operation in (lambda:self.service.save_saved_search('New',query(rule('title','is','Undated'))),
                          lambda:self.service.save_saved_search('Changed',query(rule('year','is','2022')),saved['id']),
                          lambda:self.service.delete_saved_search(saved['id'])):
            with self.assertRaises(sqlite3.Error):operation()
            self.assertEqual(self.service.list_saved_searches(),[saved])
        with self.service.store.connection() as db:
            db.execute('DROP TRIGGER fail_saved_audit')
        self.service.save_saved_search('Updated',saved['query'],saved['id'])
        self.service.delete_saved_search(saved['id'])
        with self.service.store.connection() as db:
            points=[r[0] for r in db.execute("SELECT point FROM desktop_audit WHERE point LIKE 'saved_search_%' ORDER BY sequence")]
        self.assertEqual(points,['saved_search_created','saved_search_updated','saved_search_deleted'])

    def test_v6_upgrade_backup_and_failed_schema_keep_version_documents(self):
        self.service.close()
        path=self.root/'data'/'library.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('DROP TABLE desktop_saved_searches; PRAGMA user_version=6;')
        with patch('polyscholar.store.SEARCH_SCHEMA','CREATE TABLE broken('):
            with self.assertRaises(sqlite3.Error):LocalStore(self.root/'data')
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_documents').fetchone()[0],3)
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name='desktop_saved_searches'").fetchone())
        # Audit-trigger replacement belongs to the same schema transaction:
        # an injected trigger syntax failure must not publish version 7 either.
        with patch('polyscholar.store.AUDIT_POINTS', {"invalid'point"}):
            with self.assertRaises(sqlite3.Error):LocalStore(self.root/'data')
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name='desktop_saved_searches'").fetchone())
        with closing(sqlite3.connect(self.root/'data'/'library-before-v7.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_documents').fetchone()[0],3)
        self.service=LocalService(self.root/'data',self.root/'resources')
        with self.service.store.connection() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
        self.assertEqual(self.service.list_saved_searches(),[])
        self.assertEqual(len(self.service.search_documents()),3)

if __name__=='__main__':unittest.main()
