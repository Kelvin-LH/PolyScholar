# SPDX-License-Identifier: AGPL-3.0-only
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from integrations import engines, job_worker

ROOT = Path(__file__).resolve().parents[1]

class WorkerContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'private paper.pdf'
        self.source.write_bytes(b'%PDF-1.4\nsynthetic\n')
        self.body = dict(protocol_version=1, job_id='job-1', engine='babeldoc',
                         python=sys.executable, source=str(self.source),
                         output=str(self.base / 'output'), endpoint='https://api.deepseek.com',
                         model='synthetic-model', api_key='never-echo-this-secret',
                         allow_document_upload=True, allow_asset_download=True)

    def parse(self, body):
        return job_worker.parse_request(json.dumps(body).encode())

    def test_defaults_and_secret_only_in_pipe_payload(self):
        identity, req, key = self.parse(self.body)
        self.assertEqual((identity, req.timeout, req.pages), ('job-1', 600, ''))
        self.assertNotIn(key, ' '.join(engines.command(req)))
        _, upstream_req, upstream_key = job_worker.parse_request(engines.request_input(req, key))
        self.assertEqual(upstream_req, req)
        self.assertEqual(upstream_key, key)
        self.assertEqual(list(self.base.iterdir()), [self.source])

    def test_protocol_type_consent_and_boundaries(self):
        for changes in [dict(protocol_version=True), dict(timeout=True), dict(pages=2),
                        dict(api_key=''), dict(api_key='key\nsecret'), dict(source='relative.pdf'),
                        dict(allow_document_upload=1), dict(allow_asset_download=False),
                        dict(job_id='../../bad'), dict(extra='not-allowed'), dict(pages='4-2')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.parse({**self.body, **changes})
        for raw in [b'{"job_id":"a","job_id":"b"}', b'NaN', b'[]', b'X' * (job_worker.MAX_INPUT_BYTES + 1)]:
            with self.assertRaises(ValueError):
                job_worker.parse_request(raw)

    def test_failure_cli_has_no_sensitive_traceback(self):
        body = {**self.body, 'endpoint': 'https://name:never-echo-this-secret@example.org'}
        result = subprocess.run([sys.executable, '-I', str(ROOT/'integrations/job_worker.py')],
                                input=json.dumps(body), capture_output=True, text=True, timeout=10,
                                env={**os.environ, 'PYTHONIOENCODING': 'ascii'})
        event = json.loads(result.stdout)
        self.assertEqual(event['error_code'], 'invalid_request')
        self.assertEqual(result.returncode, 1)
        for private in [self.body['api_key'], str(self.source), 'Traceback']:
            self.assertNotIn(private, result.stdout + result.stderr)

    def test_success_ndjson_unknown_cost_and_cleanup(self):
        fake = self.base/'fake_engine.py'
        fake.write_text("import json,pathlib,sys\nbody=json.load(sys.stdin)\n"
                        "assert not list(pathlib.Path.cwd().iterdir())\n"
                        "out=pathlib.Path(body['output']);"
                        "(out/'translated.pdf').write_bytes(b'%PDF-1.4\\nsynthetic\\n')\n")
        working_dirs = []
        original = engines.start_process

        def launch(*args, **kwargs):
            working_dirs.append(Path(kwargs['cwd']))
            return original(*args, **kwargs)

        capture = io.StringIO()
        stream = io.TextIOWrapper(io.BytesIO(json.dumps(self.body).encode()))
        with patch.object(sys, 'stdin', stream), patch.object(sys, 'stdout', capture), \
             patch.object(engines, 'check_version'), \
             patch.object(engines, 'start_process', side_effect=launch), \
             patch.object(engines, 'command', return_value=[sys.executable, str(fake)]), \
             patch.object(signal, 'signal'):
            self.assertEqual(job_worker.main(), 0)
        events = [json.loads(line) for line in capture.getvalue().splitlines()]
        self.assertEqual([item['event'] for item in events], ['started', 'completed'])
        self.assertIsNone(events[-1]['cost'])
        self.assertFalse(events[-1]['manifest']['quality_verified'])
        self.assertNotIn(self.body['api_key'], capture.getvalue())
        self.assertEqual(len(working_dirs), 1)
        self.assertFalse(working_dirs[0].exists())
        output = Path(self.body['output'])
        self.assertTrue(all(self.body['api_key'].encode() not in path.read_bytes()
                            for path in output.rglob('*') if path.is_file()))

    def test_failure_and_timeout_remove_partial_output(self):
        for mode in ('failure', 'timeout', 'cancelled'):
            with self.subTest(mode=mode):
                output = self.base / mode
                _, req, key = self.parse({**self.body, 'output':str(output), 'timeout':1})
                script = self.base / (mode + '.py')
                script.write_text('import json,pathlib,sys,time\n'
                                  "body=json.load(sys.stdin)\n"
                                  "assert not list(pathlib.Path.cwd().iterdir())\n"
                                  "(pathlib.Path(body['output'])/'partial.pdf').write_bytes(b'partial')\n"
                                  + ('sys.exit(2)' if mode == 'failure' else 'time.sleep(30)'))
                directories = []
                processes = []
                original_start = engines.start_process

                def launch(*args, **kwargs):
                    self.assertEqual(list(Path(kwargs['cwd']).iterdir()), [])
                    directories.append(Path(kwargs['cwd']))
                    process = original_start(*args, **kwargs)
                    processes.append(process)
                    return process

                with patch.object(engines, 'check_version'), \
                     patch.object(engines, 'start_process', side_effect=launch), \
                     patch.object(engines, 'command', return_value=[sys.executable, str(script)]):
                    if mode == 'cancelled':
                        # The real subprocess is killed by the same KeyboardInterrupt branch used by SIGTERM.
                        original_wait = subprocess.Popen.wait
                        first = [True]
                        def wait(proc, *args, **kwargs):
                            if first[0]: first[0]=False; raise KeyboardInterrupt()
                            return original_wait(proc, *args, **kwargs)
                        with patch.object(subprocess.Popen, 'wait', wait), self.assertRaises(engines.EngineError) as caught:
                            engines.run(req, key)
                    else:
                        with self.assertRaises(engines.EngineError) as caught:
                            engines.run(req, key)
                self.assertEqual(caught.exception.code, {'failure':'engine_failed', 'timeout':'timeout', 'cancelled':'cancelled'}[mode])
                self.assertFalse(output.exists())
                self.assertEqual(len(directories), 1)
                self.assertFalse(directories[0].exists())
                self.assertIsNotNone(processes[0].poll())

    def test_large_request_to_nonreading_child_keeps_timeout_and_reaps_writer(self):
        # 长请求超出常见管道容量，仍须能超时；外部看守避免回归挂死测试。
        # Exceed common pipe capacities; a watchdog prevents a regression hanging tests.
        key = '\U0001f512' * 16384
        _, req, _ = self.parse({**self.body, 'api_key': key, 'model': '中' * 16384,
                               'timeout': 1})
        self.assertGreater(len(engines.request_input(req, key)), 256 * 1024)
        child = self.base / 'nonreading.py'
        child.write_text('import time\ntime.sleep(30)\n')
        directories = []
        processes = []
        failures = []
        original_start = engines.start_process

        def launch(*args, **kwargs):
            self.assertEqual(list(Path(kwargs['cwd']).iterdir()), [])
            directories.append(Path(kwargs['cwd']))
            process = original_start(*args, **kwargs)
            processes.append(process)
            return process

        def execute():
            try:
                engines.run(req, key)
            except BaseException as error:
                failures.append(error)

        supervisor = threading.Thread(target=execute, daemon=True)
        with patch.object(engines, 'check_version'), \
             patch.object(engines, 'start_process', side_effect=launch), \
             patch.object(engines, 'command', return_value=[sys.executable, str(child)]):
            supervisor.start()
            supervisor.join(timeout=10)
            expired = supervisor.is_alive()
            if expired:
                # 测试失败也回收自己的子进程，不留下挂起读者。
                # Reap our child even on regression, releasing any blocked write.
                for process in processes:
                    engines.stop_process(process)
                supervisor.join(timeout=5)
        self.assertFalse(expired, 'Request delivery blocked the engine timeout')
        self.assertFalse(supervisor.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], engines.EngineError)
        self.assertEqual(failures[0].code, 'timeout')
        self.assertEqual(len(directories), 1)
        self.assertFalse(directories[0].exists())
        self.assertFalse(req.output.exists())
        self.assertIsNotNone(processes[0].poll())
        self.assertFalse(any(thread.name == 'polyscholar-engine-input'
                             for thread in threading.enumerate()))

    @unittest.skipUnless(os.name == 'posix', 'POSIX cooperative cancellation contract')
    def test_sigterm_cli_kills_engine_and_removes_credential_free_cwd(self):
        child = self.base / 'sleep_engine.py'
        marker = self.base / 'child.json'
        child.write_text('import json,os,pathlib,sys,time\n'
                         'body=json.load(sys.stdin)\n'
                         "assert body['api_key'] not in ' '.join(sys.argv)\n"
                         "assert all(body['api_key'] not in v for v in os.environ.values())\n"
                         'assert not list(pathlib.Path.cwd().iterdir())\n'
                         'pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(),str(pathlib.Path.cwd())]))\n'
                         'time.sleep(30)\n')
        wrapper = self.base / 'synthetic_worker.py'
        wrapper.write_text('import sys\n'
                           'sys.path.insert(0,' + repr(str(ROOT)) + ')\n'
                           'from integrations import engines,job_worker\n'
                           'engines.check_version=lambda *args: None\n'
                           'engines.command=lambda req: [sys.executable,' + repr(str(child)) + ',' + repr(str(marker)) + ']\n'
                           'raise SystemExit(job_worker.main())\n')
        proc = subprocess.Popen([sys.executable, str(wrapper)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        proc.stdin.write(json.dumps(self.body) + '\n'); proc.stdin.close(); proc.stdin = None
        self.assertEqual(json.loads(proc.stdout.readline())['event'], 'started')
        deadline = time.monotonic() + 5
        while (not marker.exists() or not marker.stat().st_size) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(marker.exists())
        pid, directory = json.loads(marker.read_text())
        self.assertEqual(list(Path(directory).iterdir()), [])
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=10)
        self.assertEqual(json.loads(stdout)['error_code'], 'cancelled')
        self.assertEqual(stderr, '')
        self.assertFalse(Path(directory).exists())
        self.assertFalse(Path(self.body['output']).exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

if __name__ == '__main__':
    unittest.main()
