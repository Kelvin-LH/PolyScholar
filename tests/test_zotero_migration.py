# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic source snapshots and failure paths / 合成来源快照及失败路径。"""
from contextlib import closing
from types import SimpleNamespace
from pathlib import Path
import sqlite3
import os
import multiprocessing
import threading
import time
import tempfile
import unittest
from unittest.mock import patch
import pymupdf as fitz
from polyscholar.service import LocalService
from polyscholar.zotero_migration import ZoteroSnapshotReader, ZoteroPdfValidator, ZoteroMigrationPolicy


def fixture(directory):
    directory.mkdir()
    db = sqlite3.connect(directory/'zotero.sqlite')
    db.executescript('''
    CREATE TABLE version(schema TEXT, version INT);
    INSERT INTO version VALUES('userdata',130);
    CREATE TABLE items(itemID INT, itemTypeID INT, key TEXT, libraryID INT);
    INSERT INTO items VALUES(1,1,'AAAAAAAA',1),(2,1,'BBBBBBBB',1),(3,2,'CCCCCCCC',1),(4,2,'DDDDDDDD',1),(5,3,'EEEEEEEE',1),(6,4,'FFFFFFFF',1);
    CREATE TABLE itemTypesCombined(itemTypeID INT,typeName TEXT);
    INSERT INTO itemTypesCombined VALUES(1,'journalArticle'),(2,'attachment'),(3,'webpage'),(4,'note');
    CREATE TABLE fieldsCombined(fieldID INT,fieldName TEXT);
    INSERT INTO fieldsCombined VALUES(1,'title'),(2,'extra');
    CREATE TABLE itemDataValues(valueID INT,value TEXT);
    INSERT INTO itemDataValues VALUES(1,'First'),(2,'Second'),(3,'Unsupported'),(4,'Raw extra');
    CREATE TABLE itemData(itemID INT,fieldID INT,valueID INT);
    INSERT INTO itemData VALUES(1,1,1),(2,1,2),(5,1,3),(5,2,4);
    CREATE TABLE itemAttachments(itemID INT,parentItemID INT,linkMode INT,contentType TEXT,path TEXT);
    INSERT INTO itemAttachments VALUES(3,1,0,'application/pdf','storage:paper.pdf'),(4,2,0,'application/pdf','storage:paper.pdf');
    CREATE TABLE itemNotes(itemID INT,parentItemID INT,note TEXT,title TEXT);
    INSERT INTO itemNotes VALUES(6,1,'<p>Raw<script>never execute</script><img src="https://example.org/x"></p>','Note');
    CREATE TABLE deletedItems(itemID INT);
    INSERT INTO deletedItems VALUES(4);
    CREATE TABLE collections(collectionID INT,collectionName TEXT,parentCollectionID INT);
    INSERT INTO collections VALUES(1,'Same',NULL),(2,'Same',NULL),(3,'Child',1);
    CREATE TABLE collectionItems(collectionID INT,itemID INT);
    INSERT INTO collectionItems VALUES(1,1),(3,1),(2,2);
    ''')
    db.commit(); db.close()
    pdf = fitz.open(); pdf.new_page(); data = pdf.tobytes(); pdf.close()
    for key in ('CCCCCCCC','DDDDDDDD'):
        target=directory/'storage'/key; target.mkdir(parents=True); (target/'paper.pdf').write_bytes(data)


def hanging_pdf_validator(path, connection):
    Path(path+'.started').write_text('started')
    time.sleep(30)


def delayed_pdf_validator(path, connection):
    # 模拟尚未进入校验目标的启动阶段；由真实父进程预算终止。
    # Simulate bootstrap before validation; the real parent deadline terminates it.
    time.sleep(30)
    hanging_pdf_validator(path, connection)


def fifo_reader_probe(path, replace_before_open, connection):
    path=Path(path)
    reader=ZoteroSnapshotReader(path.parent,threading.Event(),ZoteroMigrationPolicy())
    actual_open=os.open
    def replaced_open(candidate,flags,*args,**kwargs):
        if Path(candidate)==path:
            path.unlink()
            os.mkfifo(path)
            Path(str(path)+'.replaced').write_text('regular-to-fifo race reached open')
        return actual_open(candidate,flags,*args,**kwargs)
    try:
        with patch('os.open',replaced_open if replace_before_open else actual_open):
            reader._read(path)
        connection.send_bytes(b'accepted')
    except ValueError:
        connection.send_bytes(b'rejected')
    finally:
        connection.close()


class ZoteroPdfValidatorTests(unittest.TestCase):
    @staticmethod
    def track_stopped_workers(validator):
        stopped=[]
        real_stop=validator._stop
        def stop(worker):
            real_stop(worker)
            stopped.append(worker)
        validator._stop=stop
        return stopped

    def assert_workers_exited(self, workers):
        self.assertTrue(workers)
        for worker in workers:
            self.assertFalse(worker.is_alive())
            self.assertIsNotNone(worker.exitcode)

    def test_actual_spawn_timeout_and_cancel_reclaim_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'input.pdf'
            path.write_bytes(b'not parsed by synthetic hanging validator')
            validator=ZoteroPdfValidator()
            stopped=self.track_stopped_workers(validator)
            validator._worker_target=hanging_pdf_validator
            validator.timeout_seconds=0.5
            start=time.monotonic()
            startup_deadline=start+15
            marker=Path(str(path)+'.started')
            # 先确认真实子进程已进入挂起路径，再用受控时钟耗尽校验预算。
            # Exhaust the controlled deadline only after the real child reaches its hanging target.
            def validation_clock():
                return 1.0 if marker.exists() or time.monotonic() >= startup_deadline else 0.0
            with patch('polyscholar.zotero_migration.time', SimpleNamespace(monotonic=validation_clock)):
                valid,code=validator.validate(path,threading.Event(),1.0)
            self.assertFalse(valid)
            self.assertEqual(code,'pdf-validation-timeout')
            self.assertLess(time.monotonic()-start,18)
            self.assertTrue(Path(str(path)+'.started').exists())
            self.assertEqual(validator._workers,set())
            self.assert_workers_exited(stopped)
            Path(str(path)+'.started').unlink()
            validator.timeout_seconds=20
            cancelled=threading.Event()
            errors=[]
            def run():
                try: validator.validate(path,cancelled,time.monotonic()+20)
                except ValueError as error: errors.append(str(error))
            thread=threading.Thread(target=run)
            thread.start()
            deadline=time.monotonic()+15
            try:
                while not Path(str(path)+'.started').exists() and time.monotonic()<deadline:
                    time.sleep(0.02)
                self.assertTrue(Path(str(path)+'.started').exists())
            finally:
                cancelled.set()
                thread.join(3)
                validator.close()
            self.assertFalse(thread.is_alive())
            self.assertTrue(errors)
            self.assertEqual(validator._workers,set())
            self.assert_workers_exited(stopped)
            validator.close()

    def test_real_deadline_includes_process_startup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'input.pdf'
            path.write_bytes(b'synthetic cold-start input')
            validator=ZoteroPdfValidator()
            stopped=self.track_stopped_workers(validator)
            validator._worker_target=delayed_pdf_validator
            validator.timeout_seconds=0.1
            valid,code=validator.validate(path,threading.Event(),time.monotonic()+5)
            self.assertFalse(valid)
            self.assertEqual(code,'pdf-validation-timeout')
            self.assertFalse(Path(str(path)+'.started').exists())
            self.assertEqual(validator._workers,set())
            self.assert_workers_exited(stopped)

    @unittest.skipUnless(hasattr(os,'mkfifo'),'POSIX FIFO vector')
    def test_unattended_fifo_and_regular_to_fifo_race_do_not_block_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            for replace_before_open in (False,True):
                path=Path(temporary).resolve()/('raced' if replace_before_open else 'fifo')
                if replace_before_open: path.write_bytes(b'ordinary before open')
                else: os.mkfifo(path)
                context=multiprocessing.get_context('spawn')
                receiver,sender=context.Pipe(duplex=False)
                child=context.Process(target=fifo_reader_probe,args=(str(path),replace_before_open,sender))
                child.start(); sender.close()
                try:
                    child.join(timeout=3)
                    self.assertFalse(child.is_alive(),'unattended FIFO open blocked')
                    self.assertTrue(receiver.poll(0.1))
                    self.assertEqual(receiver.recv_bytes(maxlength=64),b'rejected')
                    self.assertEqual(child.exitcode,0)
                    if replace_before_open:
                        self.assertTrue(Path(str(path)+'.replaced').exists())
                finally:
                    if child.is_alive(): child.terminate(); child.join(1)
                    receiver.close()

    def test_validator_close_terminates_running_spawn(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'input.pdf'; path.write_bytes(b'fixture')
            validator=ZoteroPdfValidator(); validator._worker_target=hanging_pdf_validator
            stopped=self.track_stopped_workers(validator)
            validator.timeout_seconds=20
            errors=[]
            def run():
                try: validator.validate(path,threading.Event(),time.monotonic()+20)
                except ValueError as error: errors.append(str(error))
            thread=threading.Thread(target=run); thread.start()
            deadline=time.monotonic()+15
            try:
                while not Path(str(path)+'.started').exists() and time.monotonic()<deadline:
                    time.sleep(0.02)
                self.assertTrue(Path(str(path)+'.started').exists())
            finally:
                validator.close()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertTrue(errors)
            self.assertEqual(validator._workers,set())
            self.assert_workers_exited(stopped)


class ZoteroMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        self.source=self.root/'source'; fixture(self.source)
        self.service=LocalService(self.root/'local')
    def tearDown(self):
        self.service.close(); self.temp.cleanup()

    def test_complete_archive_independent_pdf_owners_and_restart_receipt(self):
        original=(self.source/'zotero.sqlite').read_bytes()
        preview=self.service.preview_zotero_migration(self.source)
        self.assertEqual(preview['counts']['pdfs'],2)
        self.assertFalse(next(i for i in preview['items'] if i['sourceId']=='3')['selectable'])
        self.assertEqual(next(i for i in preview['items'] if i['sourceId']=='5')['status'],'archive')
        receipt=self.service.import_zotero_preview(preview)
        mapping=receipt['mappings']
        self.assertNotEqual(mapping['3'],mapping['4'])
        self.assertEqual(self.service.store.document(mapping['3'])['sha256'],self.service.store.document(mapping['4'])['sha256'])
        self.assertEqual(len(list(self.service.store.objects.glob('*.pdf'))),1)
        self.assertEqual(self.service.primary_pdf_id(mapping['1']),mapping['3'])
        self.assertIsNone(self.service.primary_pdf_id(mapping['2']))
        archive=self.service.read_zotero_migration(receipt['id'])['archive']
        self.assertIn('<script>',archive['tables']['itemNotes'][0]['note'])
        self.assertEqual(archive['tables']['itemDataValues'][-1]['value'],'Raw extra')
        self.assertEqual(len(self.service.document_collections(mapping['1'])),2)
        self.assertEqual(len(self.service.list_collections()),3)
        self.assertEqual((self.source/'zotero.sqlite').read_bytes(),original)
        self.assertFalse((self.source/'zotero.sqlite-shm').exists())
        self.service.close(); self.service=LocalService(self.root/'local')
        self.assertEqual(self.service.list_zotero_migrations()[0]['id'],receipt['id'])
        fresh=self.service.preview_zotero_migration(self.source)
        with self.assertRaises(ValueError): self.service.import_zotero_preview(fresh)

    def test_wal_only_committed_row_is_read_without_source_sqlite(self):
        writer=sqlite3.connect(self.source/'zotero.sqlite')
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        writer.execute("INSERT INTO itemDataValues VALUES(7,'Only WAL')"); writer.commit()
        before={p.name:p.read_bytes() for p in self.source.glob('zotero.sqlite*')}
        try:
            preview=self.service.preview_zotero_migration(self.source)
            receipt=self.service.import_zotero_preview(preview)
            archive=self.service.read_zotero_migration(receipt['id'])['archive']
            self.assertEqual(archive['tables']['itemDataValues'][-1]['value'],'Only WAL')
            self.assertTrue(receipt['publicationCleanupComplete'])
            self.assertEqual(list(self.service.store.root.glob('zotero-????????')), [])
            self.assertEqual(list((self.service.store.root/'migration-recovery').glob('*.json')), [])
            self.assertEqual(before,{p.name:p.read_bytes() for p in self.source.glob('zotero.sqlite*')})
        finally: writer.close()

    def test_drift_forgery_cancel_unknown_schema_and_audit_rollback(self):
        preview=self.service.preview_zotero_migration(self.source)
        changed=dict(preview,title='forged')
        with self.assertRaises(ValueError): self.service.import_zotero_preview(changed)
        with self.service.store.connection() as db:
            db.execute("CREATE TRIGGER fail_zotero BEFORE INSERT ON desktop_audit WHEN NEW.point='zotero_migrated' BEGIN SELECT RAISE(ABORT,'fail');END")
        with self.assertRaises(sqlite3.IntegrityError): self.service.import_zotero_preview(preview)
        self.assertEqual(self.service.list_documents(),[])
        self.assertEqual(self.service.list_collections(),[])
        self.assertEqual(list(self.service.store.objects.glob('*.pdf')),[])
        self.service.cancel_zotero_migration()
        with self.assertRaises(ValueError): self.service.import_zotero_preview(preview)
        db=sqlite3.connect(self.source/'zotero.sqlite'); db.execute("UPDATE version SET version=999"); db.commit(); db.close()
        with self.assertRaises(ValueError): self.service.preview_zotero_migration(self.source)

    def test_subset_archive_resources_source_protection_and_shared_hash_ambiguity(self):
        # SQLite 上下文只提交/回滚；closing 在清理前释放 Windows 文件句柄。
        # SQLite's context only commits/rolls back; closing releases Windows handles before cleanup.
        with closing(sqlite3.connect(self.source/'zotero.sqlite')) as db, db:
            db.execute("UPDATE itemAttachments SET contentType='text/html',path='storage:page.html' WHERE itemID=3")
        html=self.source/'storage'/'CCCCCCCC'/'page.html'
        html.write_text('<script>not executed</script><img src="https://example.com/x">')
        preview=self.service.preview_zotero_migration(self.source)
        self.assertEqual(preview['resources'][0]['status'],'archive-ready')
        receipt=self.service.import_zotero_preview(preview,['1'])
        archive=self.service.read_zotero_migration(receipt['id'])['archive']
        self.assertEqual({r['itemID'] for r in archive['tables']['items']},{1,3,6})
        self.assertEqual(receipt['counts']['native'],1)
        self.assertEqual(receipt['counts']['archivedResources'],2)
        companion = next(r for r in archive['resources'] if r.get('kind') == 'snapshot-companion')
        self.assertEqual((self.service.store.root/companion['archivedPath']).read_bytes(), (html.parent/'paper.pdf').read_bytes())
        resource=archive['resources'][0]
        self.assertEqual((self.service.store.root/resource['archivedPath']).read_bytes(),html.read_bytes())
        exported=self.root/'recovered.html'
        self.service.export_zotero_resource(receipt['id'],'3',exported)
        self.assertEqual(exported.read_bytes(),html.read_bytes())
        with self.assertRaises(ValueError):
            self.service.export_zotero_resource(receipt['id'],'4',self.root/'missing.bin')
        for destination in (html,self.source/'zotero.sqlite',self.source/'new-export.pdf'):
            with self.assertRaises(ValueError): self.service.store.write_export(destination,b'replacement')
        self.assertEqual(html.read_text(),'<script>not executed</script><img src="https://example.com/x">')

    def test_archive_write_failure_rolls_back_and_schema_upgrade_backup_failure(self):
        with closing(sqlite3.connect(self.source/'zotero.sqlite')) as db, db:
            db.execute("UPDATE itemAttachments SET contentType='text/html',path='storage:page.html' WHERE itemID=3")
        (self.source/'storage'/'CCCCCCCC'/'page.html').write_text('local raw bytes')
        preview=self.service.preview_zotero_migration(self.source)
        real_link=os.link
        def failed_link(source,target,*args,**kwargs):
            if 'zotero-archive' in Path(target).parts:
                raise PermissionError('synthetic disk failure')
            return real_link(source,target,*args,**kwargs)
        with patch('polyscholar.migration_recovery.os.link',failed_link):
            with self.assertRaises(PermissionError): self.service.import_zotero_preview(preview)
        self.assertEqual(self.service.list_documents(),[])
        self.assertEqual(self.service.list_zotero_migrations(),[])
        self.assertEqual(list((self.service.store.root/'zotero-archive').iterdir()),[])
        # A failed upgrade must leave both the old version and a readable backup.
        # 升级 SQL 失败后，旧版本及可读备份都保留。
        self.service.close()
        path=self.root/'local'/'library.sqlite3'
        with closing(sqlite3.connect(path)) as db, db: db.execute('PRAGMA user_version=11')
        with patch('polyscholar.store.MIGRATION_SCHEMA','SELECT missing_column;'):
            with self.assertRaises(sqlite3.OperationalError): LocalService(self.root/'local')
        with closing(sqlite3.connect(path)) as db, db: self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],11)
        with closing(sqlite3.connect(self.root/'local'/'library-before-v12.sqlite3')) as db, db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.service=LocalService(self.root/'local')
        with self.service.store.connection() as db:
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],13)

    def test_source_changes_during_copy_and_unsafe_path_are_explicit(self):
        real=ZoteroSnapshotReader.manifest
        calls=[]
        def changed(reader,directory,copy_files=False):
            result=real(reader,directory,copy_files);calls.append(1)
            if copy_files:
                with closing(sqlite3.connect(directory/'zotero.sqlite')) as db, db: db.execute("UPDATE itemDataValues SET value='Changed' WHERE valueID=1")
            return result
        with patch.object(ZoteroSnapshotReader,'manifest',changed):
            with self.assertRaises(ValueError): self.service.preview_zotero_migration(self.source)
        with closing(sqlite3.connect(self.source/'zotero.sqlite')) as db, db:
            db.execute("UPDATE itemAttachments SET path='storage:../../escape.pdf' WHERE itemID=3")
        preview=self.service.preview_zotero_migration(self.source)
        self.assertEqual(preview['resources'][0]['status'],'unsafe')
        self.assertEqual(self.service.list_documents(),[])
