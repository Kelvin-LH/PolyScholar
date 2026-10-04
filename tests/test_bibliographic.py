# SPDX-License-Identifier: AGPL-3.0-only
"""Fileless creation and upgrade failure paths / 无文件创建及升级失败路径。"""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from polyscholar.service import LocalService
from polyscholar.store import LocalStore


class BibliographicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root/'data')
        self.store = self.service.store

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_creation_validation_membership_and_audit_rollback(self):
        for metadata in ({'title':' '}, {'title':'T','sha256':'bad'},
                         {'title':'T','date':'2020','year':'2021'},
                         {'title':'T','creators':[{'literal':'\ud800'}]}):
            with self.assertRaises(ValueError):
                self.service.create_bibliographic_item(metadata)
        with self.assertRaises(ValueError):
            self.service.create_bibliographic_item({'title':'T'},'missing')
        self.assertEqual(self.store.list_documents(),[])
        collection = self.service.create_collection('C')
        with self.store.connection() as db:
            db.execute("CREATE TRIGGER fail_creation BEFORE INSERT ON desktop_audit WHEN NEW.point='bibliographic_created' BEGIN SELECT RAISE(ABORT,'fail');END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.create_bibliographic_item({'title':'T'},collection['id'])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_memberships').fetchone()[0],0)
            db.execute('DROP TRIGGER fail_creation')
        for kind in ('article-journal','paper-conference','book','thesis'):
            item = self.service.create_bibliographic_item({'title':kind,'itemType':kind},collection['id'])
            self.assertFalse(item['hasPdf'])
            self.assertEqual(self.service.document_collections(item['id']),[collection['id']])
            for operation in (lambda:self.service.summarize_document(item['id'],[]),
                              lambda:self.store.clear_fulltext_index(item['id']),
                              lambda:self.store.replace_document_ir(item['id'],'test',[])):
                with self.assertRaises(ValueError):operation()
        self.assertEqual(self.service.search_fulltext('')['coverage'],[])
        self.assertEqual(self.store.rebuild_fulltext_index()['documentCount'],0)
        self.assertEqual(self.store.list_jobs(),[])

    def legacy_database(self, corrupt=False):
        source = self.root/'old.pdf'
        source.write_bytes(b'%PDF-1.7 old fixture')
        parent = self.service.import_pdf(source)
        second = self.root/'child.pdf'
        second.write_bytes(b'%PDF-1.7 child fixture')
        child = self.service.import_attachment(parent['id'],second)
        self.store.replace_document_ir(child['id'],'fixture',[{'number':1,'width':100,'height':100,
            'blocks':[{'text':'Persisted child','bbox':[0,0,1,1],'order':0}]}])
        self.service.close()
        path = self.root/'data'/'library.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA foreign_keys=OFF')
            db.execute('PRAGMA legacy_alter_table=ON')
            db.executescript('''BEGIN;
                CREATE TABLE old_documents(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,data TEXT NOT NULL);
                INSERT INTO old_documents SELECT * FROM desktop_documents;
                DROP TABLE desktop_documents;
                ALTER TABLE old_documents RENAME TO desktop_documents;
                PRAGMA user_version=10;COMMIT;''')
            preserved = [row[0] for row in db.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name='desktop_attachment_links' AND type IN ('trigger','index') AND sql IS NOT NULL")]
            db.executescript('''BEGIN;
                CREATE TABLE old_links(
                    parent_document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
                    child_document_id TEXT PRIMARY KEY REFERENCES desktop_documents(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('translation','supplement')),
                    label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 1024),
                    CHECK(parent_document_id != child_document_id));
                INSERT INTO old_links SELECT * FROM desktop_attachment_links;
                DROP TABLE desktop_attachment_links;
                ALTER TABLE old_links RENAME TO desktop_attachment_links;
                COMMIT;''')
            for statement in preserved:db.execute(statement)
            db.commit()
            if corrupt:
                db.execute("INSERT INTO desktop_memberships VALUES('missing','missing')")
                db.commit()
        return path,parent,child

    def test_nullable_upgrade_keeps_existing_pdf_and_foreign_keys(self):
        path,parent,child = self.legacy_database()
        self.service = LocalService(self.root/'data')
        self.store = self.service.store
        self.assertEqual(self.store.read_pdf(parent['id']),b'%PDF-1.7 old fixture')
        self.assertEqual(self.service.document_blocks(child['id'])[0]['text'],'Persisted child')
        item = self.service.create_bibliographic_item({'title':'New'})
        with self.store.connection() as db:
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],15)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertIsNone(db.execute('SELECT sha256 FROM desktop_documents WHERE id=?',(item['id'],)).fetchone()[0])
        preview = self.service.merge_preview([parent['id'],item['id']],parent['id'])
        self.service.merge_documents([parent['id'],item['id']],parent['id'],{},preview['revision'])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT role FROM desktop_attachment_links WHERE child_document_id=?',(item['id'],)).fetchone()[0],'merged_record')
        with closing(sqlite3.connect(self.root/'data'/'library-before-v11.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
            self.assertEqual(db.execute('SELECT sha256 FROM desktop_documents WHERE id=?',(parent['id'],)).fetchone()[0],parent['sha256'])
            self.assertEqual(next(row[3] for row in db.execute('PRAGMA table_info(desktop_documents)') if row[1]=='sha256'),1)

    def test_schema_failure_and_foreign_key_failure_leave_original_version(self):
        path,parent,child = self.legacy_database(corrupt=True)
        with self.assertRaisesRegex(ValueError,'关系校验'):
            LocalStore(self.root/'data')
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
            self.assertEqual(next(row[3] for row in db.execute('PRAGMA table_info(desktop_documents)') if row[1]=='sha256'),1)
            self.assertEqual(db.execute('SELECT child_document_id FROM desktop_attachment_links').fetchone()[0],child['id'])
            db.execute("DELETE FROM desktop_memberships WHERE document_id='missing'")
            db.commit()
        with patch('polyscholar.store.ATTACHMENT_SCHEMA','CREATE TABLE broken('):
            with self.assertRaises(sqlite3.OperationalError):LocalStore(self.root/'data')
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
            self.assertEqual(next(row[3] for row in db.execute('PRAGMA table_info(desktop_documents)') if row[1]=='sha256'),1)
        self.service = LocalService(self.root/'data')
        self.store = self.service.store
        self.assertEqual(len(self.service.list_attachments(parent['id'])),2)

    def test_metadata_edit_preserves_hidden_selection_and_export_guard(self):
        item = self.service.create_bibliographic_item({'title':'T'})
        source = self.root/'real.pdf'
        source.write_bytes(b'%PDF-1.7 primary')
        file = self.service.import_attachment(item['id'],source)
        self.service.trash_document(file['id'])
        self.service.update_document(item['id'],{'notes':'Edited while primary hidden'})
        self.assertIsNone(self.service.primary_pdf_id(item['id']))
        self.service.restore_document(file['id'])
        self.assertEqual(self.service.primary_pdf_id(item['id']),file['id'])
        with self.assertRaises(ValueError):
            self.store.write_export(source,b'citation')
        self.assertEqual(source.read_bytes(),b'%PDF-1.7 primary')

    def test_primary_change_audit_failure_and_merge_revision_drift(self):
        first = self.service.create_bibliographic_item({'title':'First'})
        second = self.service.create_bibliographic_item({'title':'Second'})
        source = self.root/'selected.pdf'
        source.write_bytes(b'%PDF-1.7 selected')
        pdf = self.service.import_attachment(second['id'],source)
        preview = self.service.merge_preview([first['id'],second['id']],first['id'])
        self.assertEqual(preview['primaryPdfId'],pdf['id'])
        self.service.trash_document(pdf['id'])
        with self.assertRaisesRegex(ValueError,'变化'):
            self.service.merge_documents([first['id'],second['id']],first['id'],{},preview['revision'])
        self.service.restore_document(pdf['id'])
        alternate = self.root/'alternate.pdf'
        alternate.write_bytes(b'%PDF-1.7 alternate')
        alternate_pdf = self.service.import_attachment(second['id'],alternate)
        with self.store.connection() as db:
            db.execute("CREATE TRIGGER fail_primary BEFORE INSERT ON desktop_audit WHEN NEW.point='primary_pdf_changed' BEGIN SELECT RAISE(ABORT,'fail');END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.set_primary_pdf(second['id'],alternate_pdf['id'])
        self.assertEqual(self.service.primary_pdf_id(second['id']),pdf['id'])
        with self.store.connection() as db:db.execute('DROP TRIGGER fail_primary')
        with self.assertRaisesRegex(ValueError,'属于'):
            self.service.set_primary_pdf(first['id'],pdf['id'])
