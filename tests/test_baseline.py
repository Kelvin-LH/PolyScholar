# SPDX-License-Identifier: AGPL-3.0-only
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from integrations import engines

ROOT = Path(__file__).resolve().parents[1]

class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript((ROOT / 'schemas/001_initial.sql').read_text())
    def tearDown(self):
        self.db.close()
    def test_foreign_keys_reject_orphan_file(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('INSERT INTO files VALUES (?,?,?,?)', ('f','missing','a'*64,'file.pdf'))
    def test_rejects_invalid_task_state_and_json(self):
        self.db.execute("INSERT INTO documents VALUES ('d','Title',NULL,'{}','now')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO jobs VALUES ('j','d','unknown','{}')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO jobs VALUES ('j','d','queued','not-json')")
    def test_baseline_links_and_schema(self):
        subprocess.run([sys.executable, str(ROOT/'scripts/check_baseline.py')], check=True,
                       stdout=subprocess.DEVNULL)

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base/'paper with spaces.pdf'
        self.source.write_bytes(b'%PDF-1.4\nsynthetic\n')
        self.req = engines.Request('babeldoc',Path(sys.executable),self.source,self.base/'output',
                                   'https://api.deepseek.com','test-model',pages='1-2',
                                   allow_document_upload=True,allow_asset_download=True)
    def test_consent_and_existing_output_rejected(self):
        with self.assertRaises(ValueError):
            engines.validate(replace(self.req,allow_document_upload=False))
        self.req.output.mkdir()
        with self.assertRaises(ValueError):engines.validate(self.req)
    def test_bad_endpoint_and_reversed_pages_rejected(self):
        for endpoint in ['http://example.org','https://u:secret@example.org','https://example.org?token=x']:
            with self.assertRaises(ValueError):engines.validate(replace(self.req,endpoint=endpoint))
        with self.assertRaises(ValueError):engines.validate(replace(self.req,pages='3-1'))
    def test_secret_not_in_argv_and_correct_engine_flags(self):
        for name in engines.VERSIONS:
            req = replace(self.req,engine=name)
            config = engines.private_config(req,self.base/name,'test-secret')
            args = engines.command(req,config)
            self.assertNotIn('test-secret',' '.join(args))
            self.assertIn(str(self.source.resolve()),args)
            self.assertIn('--config',args)
            if sys.platform != 'win32':self.assertEqual(config.stat().st_mode & 0o777,0o600)
    def test_preserves_virtualenv_python_symlink(self):
        binary=self.base/"venv-python"
        try:binary.symlink_to(sys.executable)
        except OSError:self.skipTest("Symlinks unavailable")
        args=engines.command(replace(self.req,python=binary),self.base/"config.toml")
        self.assertEqual(args[0],str(binary.absolute()))

    def test_does_not_inherit_parent_provider_credentials(self):
        with patch.dict('os.environ',{'OPENAI_API_KEY':'private','POLYSCHOLAR_API_KEY':'private'}):
            env = engines.limited_environment()
            self.assertNotIn('OPENAI_API_KEY',env)
            self.assertNotIn('POLYSCHOLAR_API_KEY',env)
    def test_real_subprocess_contract_and_cleanup(self):
        fake = self.base/'fake.py'
        fake.write_text('''import pathlib,sys
args=sys.argv
out=pathlib.Path(args[args.index('--output')+1]);out.mkdir(exist_ok=True)
config=pathlib.Path(args[args.index('--config')+1])
assert 'test-secret' in config.read_text()
(out/'translated.pdf').write_bytes(b'%PDF-1.4\\nsynthetic output\\n')
''')
        configs=[]
        original = engines.private_config
        def track(req,directory,key):
            config=original(req,directory,key);configs.append(config);return config
        def fake_command(req,config):return [sys.executable,str(fake),'--output',str(req.output),'--config',str(config)]
        for name in engines.VERSIONS:
            req=replace(self.req,engine=name,output=self.base/name)
            with patch.object(engines,'check_version'),patch.object(engines,'command',side_effect=fake_command),patch.object(engines,'private_config',side_effect=track):
                result=engines.run(req,'test-secret')
            self.assertIn('translated.pdf',result['outputs'])
            self.assertNotIn('test-secret',(req.output/'polyscholar-export.json').read_text())
        self.assertTrue(all(not p.exists() for p in configs))
    def test_timeout_stops_worker_and_cleans_config(self):
        fake=self.base/'sleep.py';fake.write_text('import time;time.sleep(30)')
        configs=[]
        original=engines.private_config
        def track(req,directory,key):
            config=original(req,directory,key);configs.append(config);return config
        with patch.object(engines,'check_version'),patch.object(engines,'command',return_value=[sys.executable,str(fake)]),patch.object(engines,'private_config',side_effect=track):
            with self.assertRaisesRegex(RuntimeError,'timed out'):
                engines.run(replace(self.req,timeout=1),'test-secret')
        self.assertTrue(all(not p.exists() for p in configs))

if __name__ == '__main__':unittest.main()
