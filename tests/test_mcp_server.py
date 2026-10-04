# SPDX-License-Identifier: AGPL-3.0-only
"""MCP server unit checks: docs exposure, whitelist rejection, real execution."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from polyscholar import mcp_server
from polyscholar.service import LocalService

class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.source = self.root / 'paper.pdf'
        self.source.write_bytes(b'%PDF-1.7 mcp sample')

    def seed(self):
        """Create a library with one document, then CLOSE it (single-instance lock)."""
        service = LocalService(self.data)
        try:
            document = service.import_pdf(self.source)
            return document['id']
        finally:
            service.close()

    def test_cli_docs_returns_contract(self):
        with patch.object(mcp_server, 'CLI_MD', Path(mcp_server.__file__).parents[1] / 'cli.md'):
            docs = mcp_server.cli_docs()
        self.assertIn('cli_run', docs)
        self.assertIn('translate', docs)

    def test_cli_md_env_override_wins(self):
        override = self.root / 'custom-cli.md'
        override.write_text('# 自定义契约', encoding='utf-8')
        with patch.dict(os.environ, {'POLYSCHOLAR_CLI_MD': str(override)}):
            self.assertEqual(mcp_server.cli_docs(), '# 自定义契约')

    def test_cli_run_rejects_non_whitelisted(self):
        result = mcp_server.cli_run('rm -rf /')
        self.assertIn('拒绝执行', result)
        result = mcp_server.cli_run('')
        self.assertIn('拒绝执行', result)

    def test_cli_run_allows_rubric_and_status(self):
        with patch.dict(os.environ, {'POLYSCHOLAR_DATA_DIR': str(self.data)}):
            self.assertIn('exit=0', mcp_server.cli_run('rubric list --json'))
            self.assertIn('exit=0', mcp_server.cli_run('status --json'))

    def test_cli_run_surfaces_single_instance_error(self):
        self.seed()
        service = LocalService(self.data)  # holds the lock
        try:
            with patch.dict(os.environ, {'POLYSCHOLAR_DATA_DIR': str(self.data)}):
                result = mcp_server.cli_run('jobs --json')
        finally:
            service.close()
        self.assertIn('exit=2', result)  # 2026-10 contract: lock conflict is exit code 2
        self.assertIn('已在运行', result)

    def test_cli_run_executes_against_data_dir(self):
        document_id = self.seed()
        with patch.dict(os.environ, {'POLYSCHOLAR_DATA_DIR': str(self.data)}):
            result = mcp_server.cli_run('show %s --json' % document_id)
        self.assertIn('exit=0', result)
        self.assertIn('"paper"', result)  # scores block present

if __name__ == '__main__':
    unittest.main()


class ManagedCommandTests(unittest.TestCase):
    def test_invalid_quotes_are_rejected(self):
        self.assertIn('拒绝执行', mcp_server.cli_run('list "'))

    def test_output_is_bounded_while_child_runs(self):
        import sys
        with patch.object(mcp_server, 'MAX_OUTPUT', 512):
            result = mcp_server._run_bounded([sys.executable, '-c', 'import sys; sys.stdout.write("x" * 2000000); sys.stderr.write("y" * 2000000)'])
        self.assertIn('exit=0', result)
        self.assertIn('输出截断', result)
        self.assertLess(len(result), 1500)

    def test_timeout_stops_managed_child(self):
        import sys
        import time
        begin = time.monotonic()
        with patch.object(mcp_server, 'RUN_TIMEOUT', 0.1):
            result = mcp_server._run_bounded([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.assertIn('进程树已终止', result)
        self.assertLess(time.monotonic() - begin, 5)
