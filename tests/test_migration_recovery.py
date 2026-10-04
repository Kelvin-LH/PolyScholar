# SPDX-License-Identifier: AGPL-3.0-only
"""Real process crashes at publication boundaries / 发布边界的真实进程崩溃。"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from polyscholar.instance import LibraryLock
from polyscholar.store import LocalStore
from polyscholar.migration_recovery import MigrationRecovery

CRASH_SCRIPT = '''
import hashlib,json,os,sys,tempfile,uuid
from pathlib import Path
from polyscholar.store import LocalStore
from polyscholar.migration_recovery import MigrationRecovery
root=Path(sys.argv[1]);phase=sys.argv[2]
store=LocalStore(root)
manager=MigrationRecovery(root)
stage=Path(tempfile.mkdtemp(prefix='zotero-',dir=root))
session=manager.begin_stage(stage)
source=stage/(uuid.uuid4().hex+'.bin')
with source.open('xb') as output:
    session.register_stage_file(source,os.fstat(output.fileno()))
    if phase=='before-bytes':os._exit(71)
    output.write(b'%PDF-1.7 synthetic bytes')
    output.flush();os.fsync(output.fileno())
    if phase=='partial-stage':os._exit(72)
data=source.read_bytes();digest=hashlib.sha256(data).hexdigest()
receipt=str(uuid.uuid4());archive=root/'zotero-archive'/receipt
archive.mkdir(parents=True)
session.set_receipt(receipt,archive)
target=store.objects/(digest+'.pdf')
if phase=='shared':target.write_bytes(data)
if phase=='before-publish':
    real=os.link
    os.link=lambda *args,**kwargs:os._exit(73)
session.publish(source,target,digest,len(data),lambda:None)
if phase in ('after-publish','shared'):os._exit(74)
with store.connection() as db:
    db.execute('BEGIN IMMEDIATE')
    doc=dict(id=str(uuid.uuid4()),sha256=digest,title='Fixture',authors='',doi='',year='',tags=[],notes='',filename='fixture.pdf',sourcePaths=[],createdAt='fixture')
    db.execute('INSERT INTO desktop_documents VALUES(?,?,?)',(doc['id'],digest,json.dumps(doc)))
    db.execute('INSERT INTO desktop_zotero_migrations VALUES(?,?,?,?,?)',(receipt,digest,'fixture',json.dumps({'id':receipt,'sourceDirectory':str(root/'synthetic-source')}),'{}'))
    if phase=='before-commit':os._exit(75)
    db.commit()
    if phase=='after-commit':os._exit(76)
'''


class MigrationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def crash(self,phase):
        local=self.root/phase
        result=subprocess.run([sys.executable,'-c',CRASH_SCRIPT,str(local),phase],
                              capture_output=True,timeout=10)
        self.assertIn(result.returncode,(71,72,73,74,75,76),result.stderr.decode())
        self.assertEqual(len(list((local/'migration-recovery').glob('*.json'))),1)
        return local

    def test_crashes_before_after_publication_and_sql_commit(self):
        for phase in ('before-bytes','partial-stage','before-publish','after-publish','before-commit','after-commit','shared'):
            with self.subTest(phase=phase):
                local=self.crash(phase)
                store=LocalStore(local)
                try:
                    self.assertEqual(store.migration_recovery_report['pending'],0)
                    self.assertEqual(list((local/'migration-recovery').glob('*.json')),[])
                    self.assertEqual(list(local.glob('zotero-????????')),[])
                    objects=list(store.objects.glob('*.pdf'))
                    self.assertEqual(len(objects),1 if phase in ('after-commit','shared') else 0)
                    if objects:
                        self.assertEqual(objects[0].stat().st_nlink,1)
                        self.assertEqual(objects[0].read_bytes(),b'%PDF-1.7 synthetic bytes')
                    with store.connection() as db:
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_zotero_migrations').fetchone()[0],int(phase=='after-commit'))
                finally:store.close()
                retry=LocalStore(local)
                self.assertEqual(retry.migration_recovery_report['pending'],0)
                retry.close()

    def test_replaced_file_hardlink_parent_and_malformed_journal_are_preserved(self):
        for mode in ('replacement','hardlink','parent','malformed'):
            with self.subTest(mode=mode):
                local=self.crash('after-publish')
                target=next((local/'objects').glob('*.pdf'))
                if mode=='replacement':
                    target.unlink();target.write_bytes(b'unrelated replacement')
                elif mode=='hardlink':os.link(target,local/'unrelated-hardlink.pdf')
                elif mode=='parent':
                    (local/'objects').rename(local/'original-objects')
                    (local/'objects').mkdir()
                else:
                    next((local/'migration-recovery').glob('*.json')).write_text('{bad')
                store=LocalStore(local)
                self.assertEqual(store.migration_recovery_report['pending'],1)
                if mode=='replacement':self.assertEqual(target.read_bytes(),b'unrelated replacement')
                if mode=='hardlink':self.assertTrue(target.exists())
                self.assertTrue(list((local/'migration-recovery').glob('*.json')))
                store.close()
                # Keep each changed root separate for the next vector.
                (local).rename(self.root/mode)

    def test_sql_reference_retains_object(self):
        local=self.crash('after-publish')
        target=next((local/'objects').glob('*.pdf'))
        digest=target.stem
        with closing(sqlite3.connect(local/'library.sqlite3')) as db:
            db.execute('INSERT INTO desktop_documents VALUES(?,?,?)',('existing-reference',digest,json.dumps({'id':'existing-reference','sha256':digest})))
            db.commit()
        store=LocalStore(local)
        self.assertEqual(store.migration_recovery_report['pending'],0)
        self.assertTrue(target.exists())
        self.assertEqual(target.stat().st_nlink,1)
        store.close()

    def test_uncommitted_receipt_does_not_authorize_stage_cleanup(self):
        local=self.crash('after-publish')
        keeper=LibraryLock(local)
        manager=MigrationRecovery(local)
        session=manager.read(next((local/'migration-recovery').glob('*.json')))
        target=next((local/'objects').glob('*.pdf'))
        try:
            with closing(sqlite3.connect(local/'library.sqlite3')) as db:
                db.execute('INSERT INTO desktop_zotero_migrations VALUES(?,?,?,?,?)',
                           (session.state['receiptId'],'transient','fixture','{}','{}'))
                self.assertTrue(db.in_transaction)
                self.assertFalse(session.finish(db))
                self.assertTrue(target.exists())
                self.assertTrue((local/session.state['stage']['path']).exists())
                db.rollback()
        finally:keeper.close()
        store=LocalStore(local)
        self.assertEqual(store.migration_recovery_report['pending'],0)
        self.assertFalse(target.exists())
        store.close()

    @unittest.skipUnless(os.name=='posix','POSIX symlink/FIFO vectors')
    def test_symlink_fifo_and_outside_archive_plan_never_delete_unrelated_data(self):
        for mode in ('symlink','fifo','outside-plan'):
            with self.subTest(mode=mode):
                local=self.crash('after-publish')
                target=next((local/'objects').glob('*.pdf'))
                external=self.root/('external-'+mode)
                external.write_bytes(b'unrelated bytes')
                if mode=='symlink':
                    target.unlink();target.symlink_to(external)
                elif mode=='fifo':
                    target.unlink();os.mkfifo(target)
                else:
                    path=next((local/'migration-recovery').glob('*.json'))
                    value=json.loads(path.read_text())
                    value['archive']['path']='../outside-directory'
                    path.write_text(json.dumps(value))
                result=subprocess.run([sys.executable,'-c',
                    'import sys,json;from polyscholar.store import LocalStore;s=LocalStore(sys.argv[1]);print(json.dumps(s.migration_recovery_report));s.close()',str(local)],
                    capture_output=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr.decode())
                self.assertEqual(json.loads(result.stdout)['pending'],1)
                self.assertEqual(external.read_bytes(),b'unrelated bytes')
                local.rename(self.root/('unsafe-'+mode))

    def test_forged_archive_with_true_external_inode_is_rejected_before_cleanup(self):
        local=self.crash('after-publish')
        external=self.root/'real-external-empty-directory';external.mkdir()
        info=external.stat();parent=external.parent.stat()
        journal=next((local/'migration-recovery').glob('*.json'))
        state=json.loads(journal.read_text())
        state['archive']={'path':'../real-external-empty-directory',
                          'identity':{'dev':info.st_dev,'ino':info.st_ino},
                          'parent':{'dev':parent.st_dev,'ino':parent.st_ino}}
        journal.write_text(json.dumps(state))
        target=next((local/'objects').glob('*.pdf'))
        store=LocalStore(local)
        self.assertEqual(store.migration_recovery_report['pending'],1)
        self.assertTrue(external.is_dir())
        self.assertTrue(target.exists())
        self.assertTrue(journal.exists())
        store.close()

    def test_actual_zotero_import_process_crash_recovers_published_pdf(self):
        local=self.root/'actual-import'
        script = r'''
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_zotero_migration import fixture
from polyscholar.service import LocalService
root=Path(sys.argv[1]);fixture(root/'source')
service=LocalService(root/'local')
preview=service.preview_zotero_migration(root/'source')
original=os.link
def crashed(source,target,*args,**kwargs):
    result=original(source,target,*args,**kwargs)
    if Path(target).parent.name=='objects':os._exit(89)
    return result
os.link=crashed
service.import_zotero_preview(preview)
'''
        local.mkdir()
        result=subprocess.run([sys.executable,'-c',script,str(local)],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,89,result.stderr.decode())
        original=(local/'source'/'zotero.sqlite').read_bytes()
        store=LocalStore(local/'local')
        self.assertEqual(store.migration_recovery_report['pending'],0)
        self.assertEqual(list(store.objects.glob('*.pdf')),[])
        self.assertEqual(store.list_documents(),[])
        self.assertEqual(list((local/'local'/'migration-recovery').glob('*.json')),[])
        self.assertEqual((local/'source'/'zotero.sqlite').read_bytes(),original)
        store.close()

    def test_schema_failure_and_lifecycle_lock_precede_cleanup(self):
        local=self.crash('after-publish')
        journal=next((local/'migration-recovery').glob('*.json'))
        before=journal.read_bytes()
        keeper=LibraryLock(local)
        try:
            with self.assertRaises(ValueError):LocalStore(local)
            self.assertEqual(journal.read_bytes(),before)
        finally:keeper.close()
        with closing(sqlite3.connect(local/'library.sqlite3')) as db:
            db.execute('PRAGMA user_version=999');db.commit()
        with self.assertRaises(ValueError):LocalStore(local)
        self.assertEqual(journal.read_bytes(),before)
        self.assertTrue(list((local/'objects').glob('*.pdf')))
