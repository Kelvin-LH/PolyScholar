# SPDX-License-Identifier: AGPL-3.0-only
"""Regression evidence for the supplied review; no provider calls or private PDFs."""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
from integrations import engines
from polyscholar.service import LocalService, environment
from polyscholar.store import LocalStore
from scripts.prepare_runtime import unpack


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root/'data', self.root/'resources')
        self.addCleanup(self.service.close)
        self.store = self.service.store
        self.source = self.root/'original.pdf'
        self.source.write_bytes(b'%PDF-1.7 original')
        self.doc = self.store.import_pdf(self.source)

    def test_second_process_cannot_recover_live_job_and_release_allows_restart(self):
        job = self.store.new_job(self.doc['id'], 'babeldoc')
        job['state'] = 'running'; self.store.put_job(job)
        code = "from polyscholar.store import LocalStore; import sys; s=LocalStore(sys.argv[1]); s.close()"
        result = subprocess.run([sys.executable,'-c',code,str(self.store.root)], capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.store.list_jobs()[0]['state'],'running')
        with self.assertRaises(ValueError):
            LocalStore(self.store.root)
        with self.assertRaises(ValueError):
            self.store.new_job(self.doc['id'],'pdfmathtranslate')
        self.service.close()
        with self.assertRaisesRegex(ValueError,'已关闭'):
            self.store.new_job(self.doc['id'],'babeldoc')
        result = subprocess.run([sys.executable,'-c',code,str(self.store.root)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode,0, result.stderr)
        reopened = LocalStore(self.store.root)
        try:
            self.assertEqual(reopened.list_jobs()[0]['state'],'failed')
        finally:
            reopened.close()

    @unittest.skipUnless(os.name == 'posix', 'POSIX modes; Windows ACL remains separate acceptance')
    def test_cache_owned_subtree_private_without_chmod_shared_parent(self):
        shared = self.root/'shared'; shared.mkdir(mode=0o755); shared.chmod(0o755)
        old = os.umask(0)
        try:
            path = self.store.prepare_cache(str(shared))
        finally:
            os.umask(old)
        self.assertEqual(path.stat().st_mode & 0o777,0o755)
        self.assertEqual((path/'jobs').stat().st_mode & 0o777,0o700)
        unsafe = self.root/'unsafe';unsafe.mkdir();unsafe.chmod(0o777)
        with self.assertRaisesRegex(ValueError,'替换'):
            self.store.prepare_cache(str(unsafe))
        self.assertFalse((unsafe/'jobs').exists())
        link = self.root/'linked';link.mkdir();(link/'jobs').symlink_to(shared/'jobs')
        with self.assertRaises(ValueError):self.store.prepare_cache(str(link))
        with self.assertRaises(ValueError):self.store.prepare_cache(str(self.store.objects))

    def test_config_private_at_creation_and_existing_file_not_overwritten(self):
        req = engines.Request('babeldoc',Path(sys.executable),self.source,self.root/'out','https://provider.test','model')
        original = os.open
        calls = []
        def track(path, flags, mode=0o777, **kw):
            calls.append((flags,mode));return original(path, flags, mode, **kw)
        with patch('integrations.engines.os.open', side_effect=track):
            config = engines.private_config(req,self.root/'config','secret')
        self.assertEqual(calls[0][1],0o600)
        self.assertTrue(calls[0][0] & os.O_EXCL)
        before = config.read_bytes()
        with self.assertRaises(FileExistsError):engines.private_config(req,self.root/'config','other')
        self.assertEqual(config.read_bytes(),before)

    def test_bad_ports_os_environment_and_metadata_limits(self):
        req = engines.Request('babeldoc',Path(sys.executable),self.source,self.root/'out','https://provider.test','model',allow_document_upload=True,allow_asset_download=True)
        for endpoint in ('https://provider.test:99999','https://provider.test:abc'):
            with self.assertRaises(ValueError):engines.validate(replace(req,endpoint=endpoint))
        variables = {k:'os-value' for k in ('APPDATA','LOCALAPPDATA','SYSTEMDRIVE','COMSPEC','PATHEXT')}
        with patch.dict(os.environ,{**variables,'OPENAI_API_KEY':'private','CUSTOM_SECRET':'private'}):
            self.assertEqual(environment(),engines.limited_environment())
            self.assertTrue(all(environment()[key]==value for key,value in variables.items()))
            self.assertNotIn('CUSTOM_SECRET',environment())
        self.store.update_document(self.doc['id'],{'notes':'n'*65536})
        with self.assertRaises(ValueError):self.store.update_document(self.doc['id'],{'notes':'中'*65536})

    def test_export_original_database_and_aliases_protected_atomic_failure_preserves_file(self):
        duplicate = self.root/'duplicate.pdf';duplicate.write_bytes(self.source.read_bytes());self.store.import_pdf(duplicate)
        original_bytes = self.source.read_bytes()
        alias = self.root/'alias.pdf';os.link(self.store.object_path(self.doc),alias)
        database = self.store.root/'library.sqlite3'
        db_alias = self.root/'db-alias';os.link(database,db_alias)
        resource = self.service.resources/'runtime.txt';resource.parent.mkdir();resource.write_text('resource')
        for target in (self.source,duplicate,alias,database,db_alias,resource):
            with self.assertRaises(ValueError):self.service.export_metadata(self.doc['id'],'csl-json',target)
        self.assertEqual(self.source.read_bytes(),original_bytes)
        self.source.write_bytes(b'%PDF- changed since import')
        with self.assertRaises(ValueError):self.service.export_metadata(self.doc['id'],'ris',self.source)
        target = self.root/'references.ris';target.write_text('previous')
        with patch('polyscholar.exports.os.fsync',side_effect=OSError('private filesystem path')):
            with self.assertRaises(ValueError) as error:self.service.export_metadata(self.doc['id'],'ris',target)
        self.assertNotIn('private filesystem path',str(error.exception))
        self.assertEqual(target.read_text(),'previous')
        self.assertEqual(list(self.root.glob('.polyscholar-export-*')),[])
        self.service.export_metadata(self.doc['id'],'ris',target)
        self.assertEqual(target.read_text(),self.service.format_metadata([self.doc['id']],'ris'))

    def test_missing_artifact_timeout_audit_and_diagnostics(self):
        self.store.save_settings({'timeoutSeconds':3600})
        for value in (0,86401,True,'600'):
            with self.assertRaises(ValueError):self.store.save_settings({'timeoutSeconds':value})
        job = self.store.new_job(self.doc['id'],'babeldoc')
        self.assertEqual(job['timeoutSeconds'],3600)
        job.update(state='completed',artifacts=['missing.pdf']);self.store.put_job(job)
        with self.assertRaisesRegex(ValueError,'已被清理'):self.store.artifact_path(job['id'],0)
        with self.assertRaises(ValueError):self.store.audit('typo')
        with self.store.connection() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO desktop_audit(point,outcome,created_at) VALUES('typo','succeeded','now')")
        self.service.set_session_key('secret-marker')
        report = self.service.diagnostic_report()
        for private in ('secret-marker',str(self.root),self.doc['id'],self.doc['title'],'api.deepseek'):
            self.assertNotIn(private,report)
        self.assertEqual(json.loads(report)['jobs'][0]['timeoutSeconds'],3600)

    def test_v2_upgrade_preserves_legacy_audit_and_enforces_new_writes(self):
        with self.store.connection() as db:
            db.execute('DROP TRIGGER desktop_audit_validate_insert')
            db.execute('DROP TRIGGER desktop_audit_validate_update')
            db.execute("INSERT INTO desktop_audit(point,outcome,created_at) VALUES('legacy_event','old','now')")
            db.execute('PRAGMA user_version=2')
        self.service.close()
        reopened=LocalStore(self.store.root)
        try:
            self.assertTrue((reopened.root/'library-before-v3.sqlite3').exists())
            with reopened.connection() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM desktop_audit WHERE point='legacy_event'").fetchone()[0],1)
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE desktop_audit SET outcome='typo' WHERE point='document_imported'")
            reopened.audit('artifact_exported')
        finally:reopened.close()

    def test_metrics_only_accept_protocol_fields_and_callbacks_can_unsubscribe(self):
        self.assertEqual(self.service._event_metrics({'progress':float('nan'),'usage':{'total_tokens':True,'private':'secret'}}),{})
        metrics = self.service._event_metrics({'progress':20,'usage':{'total_tokens':42,'private':'secret'}})
        self.assertEqual(metrics,{'progress':20,'usage':{'total_tokens':42}})
        received=[]; unsubscribe=self.service.subscribe_jobs(received.append)
        self.service._notify_job({'id':'j','state':'running','private':'secret',**metrics})
        self.assertNotIn('private',received[0]);unsubscribe()
        self.service._notify_job({'id':'j','state':'completed'})
        self.assertEqual(len(received),1)

    def test_worker_events_persist_metrics_and_finish_before_lock_release(self):
        worker=self.service.resources/'integrations/job_worker.py';worker.parent.mkdir(parents=True)
        worker.write_text("import sys,json,pathlib\nr=json.loads(sys.stdin.readline())\nassert r['timeout']==3600\np=pathlib.Path(r['output']);p.mkdir()\n(p/'output.pdf').write_bytes(b'%PDF-1.7 synthetic')\nfor event in ['progress','completed']:\n print(json.dumps(dict(protocol_version=1,job_id=r['job_id'],event=event,progress=50,usage={'total_tokens':42,'secret':r['api_key']})),flush=True)\n")
        self.store.save_settings({'timeoutSeconds':3600,'model':'synthetic'})
        self.service.set_session_key('secret-marker')
        received=[];self.service.subscribe_jobs(received.append)
        with patch.object(self.service,'discover_engine',return_value={'available':True,'pythonPath':sys.executable}):
            self.service.start_translation(self.doc['id'])
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not any(e['state']=='completed' for e in received):
            time.sleep(0.01)
        stored=self.service.list_jobs()[0]
        self.assertEqual(stored['state'],'completed')
        self.assertEqual(stored['usage'],{'total_tokens':42})
        self.assertEqual(stored['progress'],50)
        self.assertTrue(any(e['state']=='completed' for e in received))
        self.assertNotIn('secret-marker',json.dumps(stored))

    def test_tar_filter_preserves_safe_links_rejects_escape(self):
        archive=self.root/'runtime.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            file=tarfile.TarInfo('python/bin/python3');file.size=1;tar.addfile(file,io.BytesIO(b'x'))
            link=tarfile.TarInfo('python/bin/python');link.type=tarfile.SYMTYPE;link.linkname='python3';tar.addfile(link)
        output=self.root/'unpacked';unpack(archive,output)
        self.assertEqual((output/'python/bin/python').read_bytes(),b'x')
        with tarfile.open(archive,'w:gz') as tar:
            bad=tarfile.TarInfo('python/escape');bad.type=tarfile.SYMTYPE;bad.linkname='../../outside';tar.addfile(bad)
        with self.assertRaises(ValueError):unpack(archive,self.root/'bad')


if __name__ == '__main__':unittest.main()
