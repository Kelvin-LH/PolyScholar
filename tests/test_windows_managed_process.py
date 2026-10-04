# SPDX-License-Identifier: AGPL-3.0-only
"""Real Windows factory/gate checks with synthetic subprocesses only.

使用真实 Windows 工厂和门闩，仅执行合成子进程；不调用模型或厂商引擎。
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from integrations.engines import limited_environment
from integrations.managed_process import WindowsProcess, start_process
from tests import test_windows_job as fixtures


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Requires real Windows process containment')
class WindowsManagedProcessTests(unittest.TestCase):
    # Reuse the real HANDLE-based fixture instead of duplicating PID checks.
    # 复用真实进程句柄夹具，不复制只依靠 PID 的退出判断。
    setUp = fixtures.WindowsJobTests.setUp
    start = fixtures.WindowsJobTests.start
    stop = staticmethod(fixtures.WindowsJobTests.stop)
    release = staticmethod(fixtures.WindowsJobTests.release)
    wait_file = fixtures.WindowsJobTests.wait_file
    hold_process = fixtures.WindowsJobTests.hold_process
    cleanup_handle = fixtures.WindowsJobTests.cleanup_handle

    def managed(self, code, *arguments):
        process = start_process(
            [sys.executable, '-c', code, *map(str, arguments)],
            env=limited_environment(), stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.assertIsInstance(process, WindowsProcess)
        self.addCleanup(self.stop_managed, process)
        return process

    @staticmethod
    def stop_managed(process):
        try:
            process.stop()
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()

    def test_factory_preserves_all_business_stdin_bytes_and_unicode_arguments(self):
        destination = self.root / '中文 输入 bytes.bin'
        payload = b'synthetic-token\x00\r\n\x1a\xffsecond-line\n'
        code = (
            'import pathlib,sys\n'
            'pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())\n'
        )
        process = self.managed(code, destination)
        process.stdin.write(payload)
        process.stdin.close()
        self.assertEqual(process.wait(timeout=20), 0)
        process.stop()
        self.assertEqual(destination.read_bytes(), payload)

    def test_assignment_failure_never_executes_requested_command(self):
        marker = self.root / 'must-not-run'
        code = 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("ran")'
        with patch('integrations.managed_process.WindowsJob.assign',
                   side_effect=OSError('Synthetic assignment rejection')):
            with self.assertRaises(OSError):
                self.managed(code, marker)
        self.assertFalse(marker.exists())

    def test_explicit_gate_path_works_without_adjacent_frozen_module_script(self):
        marker = self.root / 'explicit-gate-result'
        code = (
            'import pathlib,sys\n'
            'pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())\n'
        )
        # The frozen module location need not contain a standalone gate script.
        # 冻结模块所在位置不必存在独立门闩脚本，使用实际资源路径。
        with patch('integrations.managed_process.__file__',
                   str(self.root / 'missing-frozen-module' / 'managed_process.py')):
            process = start_process(
                [sys.executable, '-c', code, str(marker)],
                env=limited_environment(), stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                gate_path=ROOT / 'integrations/process_gate.py',
            )
        self.assertIsInstance(process, WindowsProcess)
        self.addCleanup(self.stop_managed, process)
        payload = b'explicit-resource-gate-synthetic\r\n'
        process.stdin.write(payload)
        process.stdin.close()
        self.assertEqual(process.wait(timeout=20), 0)
        process.stop()
        self.assertEqual(marker.read_bytes(), payload)

    def test_factory_owner_hard_exit_kills_descendant_after_gate_exits(self):
        marker = self.root / 'leaf.pid'
        ready = self.root / 'owner.ready'
        code = (
            'import os,pathlib,subprocess,sys\n'
            'sys.path.insert(0,sys.argv[1])\n'
            'from integrations.managed_process import start_process\n'
            'from integrations.engines import limited_environment\n'
            'sys.stdin.readline()\n'
            'child=start_process([sys.executable,"-c",sys.argv[4],'
            'sys.argv[2],sys.argv[5]],env=limited_environment(),'
            'stdin=subprocess.PIPE)\n'
            'child.stdin.write(b"start\\n");child.stdin.flush()\n'
            'child.wait(timeout=20)\n'
            'pathlib.Path(sys.argv[3]).write_text("ready",encoding="ascii")\n'
            'sys.stdin.readline()\n'
            'os._exit(51)\n'
        )
        owner = self.start(code, ROOT, marker, ready, fixtures.PARENT, fixtures.LEAF)
        self.release(owner)
        self.wait_file(ready)
        leaf = self.hold_process(self.wait_file(marker))
        self.release(owner, b'exit\n')
        self.assertEqual(owner.wait(timeout=20), 51)
        self.assertEqual(self.api.WaitForSingleObject(leaf, 5000), 0)

    def test_sidecar_hard_exit_kills_nested_engine_descendants(self):
        marker = self.root / 'nested-leaf.pid'
        ready = self.root / 'sidecar.ready'
        code = (
            'import os,pathlib,subprocess,sys\n'
            'sys.path.insert(0,sys.argv[1])\n'
            'from integrations.managed_process import start_process\n'
            'from integrations.engines import limited_environment\n'
            'sys.stdin.readline()\n'
            'engine=start_process([sys.executable,"-c",sys.argv[4],'
            'sys.argv[2],sys.argv[5]],env=limited_environment(),'
            'stdin=subprocess.PIPE)\n'
            'engine.stdin.write(b"start\\n");engine.stdin.flush()\n'
            'engine.wait(timeout=20)\n'
            'pathlib.Path(sys.argv[3]).write_text("ready",encoding="ascii")\n'
            'sys.stdin.readline()\n'
            'os._exit(52)\n'
        )
        sidecar = self.managed(
            code, ROOT, marker, ready, fixtures.PARENT, fixtures.LEAF)
        self.release(sidecar)
        self.wait_file(ready)
        leaf = self.hold_process(self.wait_file(marker))
        self.release(sidecar, b'exit\n')
        self.assertEqual(sidecar.wait(timeout=20), 52)
        self.assertEqual(self.api.WaitForSingleObject(leaf, 5000), 0)
        sidecar.stop()


if __name__ == '__main__':
    unittest.main()
