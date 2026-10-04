# SPDX-License-Identifier: AGPL-3.0-only
"""MCP policy and structured errors / MCP 策略及结构化错误。"""
from pathlib import Path
import subprocess
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
                     ['parse', 'doc'], ['export-translation', 'doc'], ['score', 'aggregate']):
            self.assertEqual(readonly.execute(argv)['error_code'], 'read_only')
        with patch('polyscholar.mcp_bridge.run_captured', return_value=(b'[]', b'', 0, False)):
            for argv in (['list', '--json'], ['collection', 'list'], ['score', 'validate'], ['rubric', 'list']):
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
