# SPDX-License-Identifier: AGPL-3.0-only
import importlib.util
import json
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
    def test_private_request_uses_stdin_and_only_trusted_launcher_in_argv(self):
        for name in engines.VERSIONS:
            req = replace(self.req, engine=name)
            args = engines.command(req)
            self.assertEqual(args, [str(req.python.absolute()), '-I',
                                    str(ROOT / 'integrations/engine_entry.py')])
            payload = json.loads(engines.request_input(req, 'test-secret'))
            self.assertEqual(payload['engine'], name)
            self.assertEqual(payload['api_key'], 'test-secret')
            self.assertEqual(payload['source'], str(self.source))
            self.assertEqual(payload['pages'], '1-2')
            for private in ('test-secret', str(self.source), req.endpoint, req.model):
                self.assertNotIn(private, ' '.join(args))
            self.assertFalse(req.output.exists())
    def test_preserves_virtualenv_python_symlink(self):
        binary=self.base/"venv-python"
        try:binary.symlink_to(sys.executable)
        except OSError:self.skipTest("Symlinks unavailable")
        args=engines.command(replace(self.req,python=binary))
        self.assertEqual(args[0],str(binary.absolute()))

    def test_does_not_inherit_parent_provider_credentials(self):
        with patch.dict('os.environ',{'OPENAI_API_KEY':'private','POLYSCHOLAR_API_KEY':'private'}):
            env = engines.limited_environment()
            self.assertNotIn('OPENAI_API_KEY',env)
            self.assertNotIn('POLYSCHOLAR_API_KEY',env)
    def test_real_subprocess_contract_and_cleanup(self):
        fake = self.base/'fake.py'
        fake.write_text('''import json,os,pathlib,sys
body=json.load(sys.stdin)
key=body['api_key']
assert key and key not in ' '.join(sys.argv)
assert all(key not in value for value in os.environ.values())
assert not list(pathlib.Path.cwd().iterdir())
out=pathlib.Path(body['output'])
(out/'translated.pdf').write_bytes(b'%PDF-1.4\\nsynthetic output\\n')
''')
        working_dirs = []
        processes = []
        original = engines.start_process

        def launch(*args, **kwargs):
            working_dirs.append(Path(kwargs['cwd']))
            process = original(*args, **kwargs)
            processes.append(process)
            return process

        for name in engines.VERSIONS:
            req=replace(self.req,engine=name,output=self.base/name)
            with patch.object(engines, 'check_version'), \
                 patch.object(engines, 'command', return_value=[sys.executable, str(fake)]), \
                 patch.object(engines, 'start_process', side_effect=launch), \
                 patch.dict('os.environ', {'OPENAI_API_KEY': 'test-secret'}):
                result = engines.run(req, 'test-secret')
            self.assertIn('translated.pdf',result['outputs'])
            self.assertTrue(all(b'test-secret' not in path.read_bytes()
                                for path in req.output.rglob('*') if path.is_file()))
        self.assertEqual(len(working_dirs), len(engines.VERSIONS))
        self.assertTrue(all(not path.exists() for path in working_dirs))
        self.assertTrue(all(process.poll() == 0 for process in processes))

    def test_timeout_stops_worker_and_cleans_empty_working_directory(self):
        fake = self.base / 'sleep.py'
        fake.write_text('import json,sys,time\njson.load(sys.stdin)\ntime.sleep(30)')
        working_dirs = []
        processes = []
        original = engines.start_process

        def launch(*args, **kwargs):
            directory = Path(kwargs['cwd'])
            self.assertEqual(list(directory.iterdir()), [])
            working_dirs.append(directory)
            process = original(*args, **kwargs)
            processes.append(process)
            return process

        with patch.object(engines, 'check_version'), \
             patch.object(engines, 'command', return_value=[sys.executable, str(fake)]), \
             patch.object(engines, 'start_process', side_effect=launch):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                engines.run(replace(self.req, timeout=1), 'test-secret')
        self.assertEqual(len(working_dirs), 1)
        self.assertFalse(working_dirs[0].exists())
        self.assertFalse(self.req.output.exists())
        self.assertIsNotNone(processes[0].poll())

if __name__ == '__main__':unittest.main()
