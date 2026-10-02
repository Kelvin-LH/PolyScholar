# SPDX-License-Identifier: AGPL-3.0-only
"""Launch protocol and failure contracts, separate from actual Windows Job tests."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from integrations import engines
from integrations.managed_process import ProcessCleanupError, WindowsProcess


GATE = Path(__file__).resolve().parents[1] / 'integrations/process_gate.py'


class ProcessGateTests(unittest.TestCase):
    def test_release_byte_does_not_consume_worker_protocol_bytes(self):
        command = [sys.executable, '-I', '-c',
                   'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())']
        payload = b'\x00\x1a\r\n' + '中文 JSON 原文'.encode() + bytes(range(256))
        result = subprocess.run(
            [sys.executable, '-I', str(GATE), *command],
            input=b'\x01' + payload,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, payload)
        self.assertEqual(result.stderr, b'')

    def test_gate_fails_closed_without_a_valid_release_byte(self):
        command = [sys.executable, '-I', '-c', 'print("must not run")']
        for data in (b'', b'\n', b'\x00'):
            with self.subTest(size=len(data)):
                result = subprocess.run(
                    [sys.executable, '-I', str(GATE), *command], input=data,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                )
                self.assertEqual(result.returncode, 125)
                self.assertEqual(result.stdout + result.stderr, b'')

    def test_release_without_a_command_is_rejected(self):
        result = subprocess.run(
            [sys.executable, '-I', str(GATE)], input=b'\x01',
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        self.assertEqual(result.returncode, 125)
        self.assertEqual(result.stdout + result.stderr, b'')

    def test_assign_failure_never_releases_actual_bootstrap(self):
        # 此处注入绑定失败，验证真实门闩；不作为 Windows API 验收。
        # Inject assignment failure but execute the real blocked bootstrap.
        class RejectedJob:
            process = None
            terminated = False
            closed = False

            def assign(self, process):
                self.process = process
                raise OSError('Synthetic assignment rejection')

            def terminate(self, timeout=5):
                self.terminated = True

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / 'must-not-exist'
            command = [sys.executable, '-I', '-c',
                       f'from pathlib import Path; Path({str(marker)!r}).touch()']
            job = RejectedJob()
            with patch('integrations.managed_process.WindowsJob', return_value=job):
                with self.assertRaises(OSError):
                    WindowsProcess(command, env=engines.limited_environment())
            self.assertFalse(marker.exists())
            self.assertIsNotNone(job.process.poll())
            self.assertTrue(job.terminated and job.closed)


class EngineCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source.pdf'
        self.source.write_bytes(b'%PDF-1.4 synthetic')
        self.config_dir = self.root / 'private'
        self.config_dir.mkdir(mode=0o700)
        self.req = engines.Request(
            'babeldoc', Path(sys.executable), self.source, self.root / 'output',
            'https://api.example.test', 'synthetic-model',
            allow_document_upload=True, allow_asset_download=True,
        )
        self.enterContext(patch.object(engines, 'check_version'))
        self.enterContext(patch.object(engines.tempfile, 'mkdtemp',
                                       return_value=str(self.config_dir)))

    def test_unconfirmed_tree_cleanup_preserves_config_and_rejects_success(self):
        with patch.object(engines, 'start_process',
                          side_effect=ProcessCleanupError('Synthetic cleanup failure')):
            with self.assertRaises(engines.EngineError) as caught:
                engines.run(self.req, 'synthetic-token')
        self.assertEqual(caught.exception.code, 'io_error')
        self.assertTrue((self.config_dir / 'config.toml').is_file())
        self.assertFalse((self.req.output / 'polyscholar-export.json').exists())

    def test_output_directory_race_does_not_delete_an_unowned_directory(self):
        original = engines.private_config

        def create_competing_output(*args):
            config = original(*args)
            self.req.output.mkdir()
            (self.req.output / 'unrelated.txt').write_text('preserve')
            return config

        with patch.object(engines, 'private_config', side_effect=create_competing_output):
            with self.assertRaises(engines.EngineError):
                engines.run(self.req, 'synthetic-token')
        self.assertEqual((self.req.output / 'unrelated.txt').read_text(), 'preserve')
        self.assertFalse(self.config_dir.exists())


if __name__ == '__main__':
    unittest.main()
