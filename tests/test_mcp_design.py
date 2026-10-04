# SPDX-License-Identifier: AGPL-3.0-only
"""MCP policy and structured errors / MCP 策略及结构化错误。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from polyscholar.mcp_bridge import BridgeConfiguration, CliBridge


class McpDesignTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bridge = CliBridge(BridgeConfiguration(self.root / 'chosen'))

    def test_array_preserves_spaces_and_windows_paths(self):
        path = r'C:\研究 资料\paper one.pdf'
        with patch('polyscholar.mcp_bridge.run_captured', return_value=(b'{}', b'', 0, False)) as run:
            result = self.bridge.execute(['add', path, '--json'])
        self.assertTrue(result['success'])
        command = run.call_args.args[0]
        self.assertEqual(command[-3:], ['add', path, '--json'])
        self.assertEqual(command[3:5], ['--data-dir', str(self.root / 'chosen')])

    def test_library_override_abbreviations_and_invalid_args_never_launch(self):
        cases = [(['list', '--data-dir=/private'], 'library_override'),
                 (['list', '--data', '/private'], 'library_override'),
                 (['list', '--d=/private'], 'library_override'),
                 (['rm', '-rf', '/'], 'unsupported_command'),
                 (['list', '--bogus-flag'], 'invalid_arguments'),
                 (['list', '--help'], 'invalid_arguments'),
                 (['show', 'private\x00value'], 'invalid_arguments'),
                 (['show', 'x' * 8193], 'invalid_arguments'),
                 (['list'] * 129, 'invalid_arguments'),
                 (['show', '\ud800'], 'invalid_arguments'),
                 (None, 'invalid_arguments')]
        with patch('polyscholar.mcp_bridge.run_captured') as run:
            for argv, expected in cases:
                with self.subTest(argv=repr(argv)[:80]):
                    self.assertEqual(self.bridge.execute(argv)['error_code'], expected)
            run.assert_not_called()

    def test_read_only_rejects_network_writes_exports_and_aggregation(self):
        readonly = CliBridge(BridgeConfiguration(self.root, read_only=True))
        for argv in (['add', '--arxiv', '2312.04567'], ['translate', 'doc'], ['remove', 'doc', '--yes'],
                     ['parse', 'doc'], ['export-translation', 'doc'], ['score', 'aggregate'],
                     ['verify', 'github', 'doc', '--repo', 'https://github.com/example/research', '--yes'],
                     ['verify', 'import-research', 'doc', '--file', 'report.json'],
                     ['verify', 'export', 'doc', '--report', 'report', '--out', 'report.json']):
            self.assertEqual(readonly.execute(argv)['error_code'], 'read_only')
        with patch('polyscholar.mcp_bridge.run_captured', return_value=(b'[]', b'', 0, False)):
            for argv in (['list', '--json'], ['collection', 'list'], ['score', 'validate'], ['rubric', 'list'],
                         ['verify', 'list', 'doc'], ['verify', 'show', 'doc', '--report', 'report']):
                self.assertTrue(readonly.execute(argv)['success'])

    def test_error_contract_does_not_echo_provider_or_os_detail(self):
        raw = '此文献库已在运行，请切换到已打开的 PolyScholar。'.encode()
        with patch('polyscholar.mcp_bridge.run_captured', return_value=(b'', raw, 2, False)):
            result = self.bridge.execute(['jobs'])
        self.assertEqual(result['error_code'], 'library_busy')
        self.assertEqual(result['exit_code'], 2)
        self.assertIn('关闭', result['stderr'])
        self.assertNotIn('synthetic-secret', str(result))
        with patch('polyscholar.mcp_bridge.run_captured', side_effect=subprocess.TimeoutExpired('private', 1)):
            result = self.bridge.execute(['list'])
        self.assertEqual(result['error_code'], 'timeout')
        self.assertNotIn('private', str(result))

    def test_real_child_utf8_is_independent_of_host_encoding(self):
        from integrations.engines import limited_environment
        from polyscholar.service import LocalService
        service = LocalService(self.root / 'chosen')
        try:
            source = self.root / 'paper.pdf'
            source.write_bytes(b'%PDF-1.7 synthetic encoding regression')
            document = service.import_pdf(source)
            service.update_document(document['id'], {'title': '研究文献 编码检查'})
        finally:
            service.close()
        # Emulate Windows non-UTF8 pipes / 模拟 Windows 非 UTF8 输出管道。
        environment = dict(limited_environment(), PYTHONIOENCODING='cp1252:backslashreplace',
                           PYTHONUTF8='0')
        with patch('polyscholar.mcp_bridge.limited_environment', return_value=environment.copy()):
            result = self.bridge.execute(['list', '--json'])
        self.assertTrue(result['success'])
        self.assertIn('研究文献', result['stdout'])
        self.assertEqual(result['data'][0]['title'], '研究文献 编码检查')
        standalone = subprocess.run([sys.executable, '-m', 'polyscholar.cli',
            '--data-dir', str(self.root / 'chosen'), 'list', '--json'],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(standalone.returncode, 0)
        self.assertIn('研究文献', standalone.stdout.decode('utf-8'))
        holder = LocalService(self.root / 'chosen')
        try:
            with patch('polyscholar.mcp_bridge.limited_environment', return_value=environment.copy()):
                result = self.bridge.execute(['jobs', '--json'])
            standalone = subprocess.run([sys.executable, '-m', 'polyscholar.cli',
                '--data-dir', str(self.root / 'chosen'), 'jobs', '--json'],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            self.assertEqual(standalone.returncode, 2)
            self.assertIn('已在运行', standalone.stderr.decode('utf-8'))
        finally:
            holder.close()
        self.assertEqual(result['error_code'], 'library_busy')
        self.assertEqual(result['exit_code'], 2)
        self.assertIn('关闭', result['stderr'])

    def test_frozen_desktop_cannot_be_misreported_as_stdio_cli(self):
        with patch('polyscholar.mcp_bridge.sys.frozen', True, create=True), patch('polyscholar.mcp_bridge.run_captured') as run:
            self.assertEqual(self.bridge.execute(['list'])['error_code'], 'unsupported_runtime')
            run.assert_not_called()

    def test_busy_status_is_actionable_without_library_mutation(self):
        with patch('polyscholar.mcp_bridge.run_captured', return_value=(b'{"lock":"busy"}', b'', 0, False)):
            result = self.bridge.library_status()
        self.assertTrue(result['success'])
        self.assertEqual(result['status']['lock'], 'busy')
        self.assertIn('关闭', result['next_action'])


if __name__ == '__main__':
    unittest.main()
