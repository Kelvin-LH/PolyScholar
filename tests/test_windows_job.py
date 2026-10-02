# SPDX-License-Identifier: AGPL-3.0-only
"""Real synthetic Windows process trees; other hosts explicitly skip.

真实合成 Windows 进程树；其他系统明确跳过，不冒充 Windows 验收。
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from integrations.windows_job import WindowsJob


ROOT = Path(__file__).resolve().parents[1]
LEAF = 'import time; time.sleep(120)'
PARENT = (
    'import pathlib,subprocess,sys\n'
    'sys.stdin.readline()\n'
    'child=subprocess.Popen([sys.executable,"-c",sys.argv[2]], '
    'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=False)\n'
    'pathlib.Path(sys.argv[1]).write_text(str(child.pid),encoding="ascii")\n'
)


@unittest.skipUnless(os.name == 'nt', 'Requires real Windows Job Objects')
class WindowsJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='polyscholar-job-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateProcess.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL

    def start(self, code, *arguments):
        process = subprocess.Popen(
            [sys.executable, '-c', code, *map(str, arguments)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=False,
        )
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        if process.stdin:
            process.stdin.close()

    @staticmethod
    def release(process, message=b'start\n'):
        process.stdin.write(message)
        process.stdin.flush()

    def wait_file(self, path):
        deadline = time.monotonic() + 20
        content = ''
        while time.monotonic() < deadline:
            try:
                content = path.read_text(encoding='ascii')
            except FileNotFoundError:
                pass
            if content:
                return content
            time.sleep(0.02)
        self.fail('Synthetic child did not publish nonempty marker content')

    def hold_process(self, pid):
        # Retain the real process handle, so PID reuse cannot make exit look valid.
        # 保留真实进程句柄，避免 PID 重用让退出断言误判。
        handle = self.api.OpenProcess(0x100000 | 0x1, False, int(pid))
        self.assertTrue(handle, 'Could not open synthetic descendant')
        self.addCleanup(self.cleanup_handle, handle)
        self.assertEqual(self.api.WaitForSingleObject(handle, 0), 0x102)
        return handle

    def cleanup_handle(self, handle):
        if self.api.WaitForSingleObject(handle, 0) == 0x102:
            self.api.TerminateProcess(handle, 1)
            self.api.WaitForSingleObject(handle, 5000)
        self.api.CloseHandle(handle)

    def test_descendant_terminates_after_direct_parent_exits(self):
        job = WindowsJob()
        self.addCleanup(job.close)
        marker = self.root / 'child.pid'
        parent = self.start(PARENT, marker, LEAF)
        job.assign(parent)
        self.release(parent)
        child = self.hold_process(self.wait_file(marker))
        self.assertEqual(parent.wait(timeout=15), 0)
        job.terminate(timeout=5)
        self.assertEqual(self.api.WaitForSingleObject(child, 5000), 0)
        job.close()
        job.close()
        with self.assertRaises(OSError):
            job.terminate()

    def test_owner_hard_exit_closes_noninherited_job_and_kills_descendant(self):
        marker = self.root / 'child.pid'
        ready = self.root / 'owner.ready'
        code = (
            'import os,pathlib,subprocess,sys,time\n'
            'sys.path.insert(0,sys.argv[1])\n'
            'from integrations.windows_job import WindowsJob\n'
            'sys.stdin.readline()\n'
            'job=WindowsJob()\n'
            'parent=subprocess.Popen([sys.executable,"-c",sys.argv[4],'
            'sys.argv[2],sys.argv[5]],stdin=subprocess.PIPE,'
            'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=False)\n'
            'job.assign(parent)\n'
            'parent.stdin.write(b"start\\n");parent.stdin.flush()\n'
            'parent.wait(timeout=20)\n'
            'pathlib.Path(sys.argv[3]).write_text("ready",encoding="ascii")\n'
            'sys.stdin.readline()\n'
            'os._exit(41)\n'
        )
        owner = self.start(code, ROOT, marker, ready, PARENT, LEAF)
        self.release(owner)
        self.wait_file(ready)
        child = self.hold_process(self.wait_file(marker))
        self.release(owner, b'exit\n')
        self.assertEqual(owner.wait(timeout=15), 41)
        self.assertEqual(self.api.WaitForSingleObject(child, 5000), 0)

    def test_nested_job_contains_inner_descendant(self):
        outer = WindowsJob()
        self.addCleanup(outer.close)
        marker = self.root / 'nested.pid'
        code = (
            'import subprocess,sys\n'
            'sys.path.insert(0,sys.argv[1])\n'
            'from integrations.windows_job import WindowsJob\n'
            'sys.stdin.readline()\n'
            'inner=WindowsJob()\n'
            'parent=subprocess.Popen([sys.executable,"-c",sys.argv[3],'
            'sys.argv[2],sys.argv[4]],stdin=subprocess.PIPE,'
            'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
            'inner.assign(parent)\n'
            'parent.stdin.write(b"start\\n");parent.stdin.flush()\n'
            'parent.wait(timeout=20)\n'
            'sys.stdin.readline()\n'
        )
        middle = self.start(code, ROOT, marker, PARENT, LEAF)
        outer.assign(middle)
        self.release(middle)
        child = self.hold_process(self.wait_file(marker))
        outer.terminate(timeout=5)
        self.assertIsNotNone(middle.poll())
        self.assertEqual(self.api.WaitForSingleObject(child, 5000), 0)


if __name__ == '__main__':
    unittest.main()
