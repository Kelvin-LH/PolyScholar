# SPDX-License-Identifier: AGPL-3.0-only
"""Real process-crash checks with synthetic credentials and no upstream/API.

使用真实进程硬退出验证适配器凭据边界；不冒充真实引擎/API验收。
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from integrations import engines, job_worker


ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'synthetic-memory-only-credential'


class CredentialDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def assert_no_secret_files(self, root):
        for path in root.rglob('*'):
            if path.is_file():
                self.assertNotIn(TOKEN.encode(), path.read_bytes(), path.name)

    def test_adapter_hard_exit_before_engine_start_creates_no_credentials(self):
        program = r'''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from integrations import engines
root = Path(sys.argv[2])
private = root / 'private'
private.mkdir(mode=0o700)
source = root / 'source.pdf'
source.write_bytes(b'%PDF-1.4 synthetic crash fixture')
engines.tempfile.mkdtemp = lambda **_: str(private)
engines.check_version = lambda *_: None
engines.start_process = lambda *_, **__: os._exit(71)
request = engines.Request(sys.argv[3], Path(sys.executable), source, root / 'output',
    'https://api.example.test', 'synthetic-model',
    allow_document_upload=True, allow_asset_download=True)
engines.run(request, sys.stdin.readline().strip())
'''
        for engine in engines.VERSIONS:
            with self.subTest(engine=engine):
                root = self.root / engine
                root.mkdir()
                result = subprocess.run(
                    [sys.executable, '-I', '-c', program, str(ROOT), str(root), engine],
                    input=TOKEN.encode(), capture_output=True, timeout=15,
                )
                self.assertEqual(result.returncode, 71)
                self.assertEqual(result.stdout + result.stderr, b'')
                self.assertTrue((root / 'private').is_dir())
                self.assert_no_secret_files(root)

    def test_adapter_hard_exit_after_an_actual_child_receives_input(self):
        # Keep the child alive at the crash boundary; never infer exit from no files.
        # 硬退出时子进程确实持有已收到的凭据；无落盘不等于进程已回收。
        child = self.root / 'child.py'
        child.write_text(
            'import hashlib,json,os,pathlib,sys,time\n'
            'body=json.loads(sys.stdin.buffer.readline())\n'
            f'assert hashlib.sha256(body["api_key"].encode()).hexdigest()=={hashlib.sha256(TOKEN.encode()).hexdigest()!r}\n'
            'root=pathlib.Path(body["output"]).parent\n'
            '(root/"received.tmp").write_text(json.dumps({"pid":os.getpid(),"received":True}))\n'
            '(root/"received.tmp").replace(root/"received.json")\n'
            'while not (root/"release").exists():time.sleep(.01)\n',
            encoding='utf-8',
        )
        program = r'''
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from integrations import engines
root = Path(sys.argv[2]); child = Path(sys.argv[3])
private = root / 'private'; private.mkdir(mode=0o700)
source = root / 'source.pdf'; source.write_bytes(b'%PDF-1.4 synthetic')
engines.tempfile.mkdtemp = lambda **_: str(private)
engines.check_version = lambda *_: None
engines.command = lambda _: [sys.executable, '-I', str(child)]
class CrashAfterDelivery:
    def __init__(self, process):
        self.stdin = process.stdin
    def wait(self, timeout):
        deadline = time.monotonic() + 10
        marker = root / 'received.json'
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        if not marker.exists():
            os._exit(73)
        # Windows Job owner remains referenced until this real hard exit.
        # 真正硬退出前保持 Windows Job 所有者对象存活。
        os._exit(72)
actual_start = engines.start_process
processes = []
def start(*args, **kwargs):
    process = actual_start(*args, **kwargs)
    processes.append(process)
    return CrashAfterDelivery(process)
engines.start_process = start
request = engines.Request('babeldoc', Path(sys.executable), source, root / 'output',
    'https://api.example.test', 'synthetic-model',
    allow_document_upload=True, allow_asset_download=True)
engines.run(request, sys.stdin.readline().strip())
'''
        root = self.root / 'after-delivery'
        root.mkdir()
        result = None
        try:
            result = subprocess.run(
                [sys.executable, '-I', '-c', program, str(ROOT), str(root), str(child)],
                input=TOKEN.encode(), capture_output=True, timeout=20,
            )
            self.assertEqual(result.returncode, 72)
            marker = json.loads((root / 'received.json').read_text())
            self.assertTrue(marker['received'])
            self.assertEqual(result.stdout + result.stderr, b'')
            self.assert_no_secret_files(root)
        finally:
            # POSIX orphan containment is a separate gate; release our owned fixture.
            # POSIX 孤儿进程约束另行验收；仅释放本测试自有夹具。
            (root / 'release').touch()
            marker_path = root / 'received.json'
            if os.name == 'posix' and marker_path.exists():
                pid = json.loads(marker_path.read_text())['pid']
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_request_validation_rejects_bad_key_before_any_output(self):
        source = self.root / 'source.pdf'
        source.write_bytes(b'%PDF-1.4 synthetic')
        req = engines.Request(
            'babeldoc', Path(sys.executable), source, self.root / 'output',
            'https://api.example.test', 'synthetic-model',
            allow_document_upload=True, allow_asset_download=True,
        )
        for invalid in ('', 'bad\nkey', 'x' * 16385):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                engines.run(req, invalid)
        self.assertFalse(req.output.exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group descendant cleanup')
    def test_exited_parent_with_stdin_holding_descendant_reaps_writer(self):
        # 使用独立看守进程，坏实现不能挂死测试；Windows Job 另行验证。
        # An external watchdog bounds regressions; Windows Job checks are separate.
        root = self.root / 'descendant-holds-input'
        root.mkdir()
        child = root / 'child.py'
        child.write_text(r'''
import json, os, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[2])
if sys.argv[1] == 'leaf':
    os.fstat(0)
    (root / 'ready.tmp').write_text(json.dumps({'pid': os.getpid()}))
    (root / 'ready.tmp').replace(root / 'ready.json')
    # Keep stdin open without reading until process-group termination.
    time.sleep(30)
else:
    subprocess.Popen([sys.executable, '-I', __file__, 'leaf', str(root)],
                     stdin=sys.stdin, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 5
    while not (root / 'ready.json').exists() and time.monotonic() < deadline:
        time.sleep(.01)
    if not (root / 'ready.json').exists():
        os._exit(74)
    (root / 'exiting.tmp').write_text('ready')
    (root / 'exiting.tmp').replace(root / 'exiting')
    os._exit(0)
''', encoding='utf-8')
        program = r'''
import json, sys, threading
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from integrations import engines, job_worker
root = Path(sys.argv[2])
private = root / 'private'
private.mkdir(mode=0o700)
engines.tempfile.mkdtemp = lambda **_: str(private)
engines.check_version = lambda *_: None
engines.command = lambda _: [sys.executable, '-I', str(root / 'child.py'),
                             'parent', str(root)]
actual_start = engines.start_process
processes = []
def start(*args, **kwargs):
    process = actual_start(*args, **kwargs)
    processes.append(process)
    (root / 'process.tmp').write_text(json.dumps({'pid': process.pid}))
    (root / 'process.tmp').replace(root / 'process.json')
    return process
engines.start_process = start
_, request, key = job_worker.parse_request(
    sys.stdin.buffer.readline(job_worker.MAX_INPUT_BYTES + 1))
try:
    engines.run(request, key)
except engines.EngineError as error:
    code = error.code
else:
    code = 'unexpected_success'
print(json.dumps({'code': code, 'parentExit': processes[0].poll(),
                  'writerThreads': sum(t.name == 'polyscholar-engine-input'
                                       for t in threading.enumerate())}))
'''
        source = root / 'source.pdf'
        source.write_bytes(b'%PDF-1.4 synthetic')
        request = engines.Request(
            'babeldoc', Path(sys.executable), source, root / 'output',
            'https://api.example.test', '\U0001f512' * 16384, timeout=2,
            allow_document_upload=True, allow_asset_download=True,
        )
        key = TOKEN + '\U0001f512' * (16384 - len(TOKEN))
        payload = engines.request_input(request, key)
        self.assertGreater(len(payload), 256 * 1024)
        watchdog = subprocess.Popen(
            [sys.executable, '-I', '-c', program, str(ROOT), str(root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            stdout, stderr = watchdog.communicate(payload, timeout=15)
            self.assertEqual(watchdog.returncode, 0)
            self.assertEqual(stderr, b'')
            self.assertEqual(json.loads(stdout), {
                'code': 'engine_failed', 'parentExit': 0, 'writerThreads': 0,
            })
            self.assertEqual((root / 'exiting').read_text(), 'ready')
            self.assertGreater(json.loads((root / 'ready.json').read_text())['pid'], 0)
            self.assertFalse(request.output.exists())
            self.assertFalse((root / 'private').exists())
            self.assert_no_secret_files(root)
        finally:
            # 不依赖受测 stop_process；失败时也终止本测试自有进程组并回收看守。
            # Independent cleanup kills our owned group even if stop_process regresses.
            marker = root / 'process.json'
            if marker.exists():
                try:
                    os.killpg(json.loads(marker.read_text())['pid'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if watchdog.poll() is None:
                watchdog.kill()
            watchdog.communicate(timeout=5)

    def test_ready_output_is_private_to_the_trusted_child_entry(self):
        source = self.root / 'source.pdf'
        source.write_bytes(b'%PDF-1.4 synthetic')
        req = engines.Request(
            'babeldoc', Path(sys.executable), source, self.root / 'output',
            'https://api.example.test', 'synthetic-model',
            allow_document_upload=True, allow_asset_download=True,
        )
        data = engines.request_input(req, TOKEN)
        with self.assertRaises(ValueError):
            job_worker.parse_request(data, output_ready=True)
        req.output.mkdir()
        with self.assertRaises(ValueError):
            job_worker.parse_request(data)
        self.assertEqual(job_worker.parse_request(data, output_ready=True)[1], req)
        body = json.loads(data)
        body['output_ready'] = True
        with self.assertRaises(ValueError):
            job_worker.parse_request(json.dumps(body).encode())


if __name__ == '__main__':
    unittest.main()
