# SPDX-License-Identifier: AGPL-3.0-only
"""stdio handshake smoke: initialize → tools/list → tools/call against a temp library."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from polyscholar.service import LocalService

class StdioHandshakeTests(unittest.TestCase):
    def test_initialize_list_and_call(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        service = LocalService(root / 'data')
        source = root / 's.pdf'
        source.write_bytes(b'%PDF-1.7 mcp stdio smoke')
        service.import_pdf(source)
        service.close()  # release the lock before spawning the MCP server subprocess

        env = {**os.environ, 'POLYSCHOLAR_DATA_DIR': str(root / 'data')}
        server = subprocess.Popen(
            [sys.executable, '-m', 'polyscholar.mcp_server'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, cwd=str(Path(__file__).resolve().parents[1]))
        self.addCleanup(server.kill)

        def send(payload):
            server.stdin.write((json.dumps(payload) + '\n').encode())
            server.stdin.flush()

        def receive():
            line = server.stdout.readline()
            return json.loads(line) if line else {}

        send(dict(jsonrpc='2.0', id=1, method='initialize',
                  params=dict(protocolVersion='2025-06-18', capabilities={},
                              clientInfo=dict(name='smoke', version='0'))))
        init = receive()
        self.assertEqual(init['result']['serverInfo']['name'], 'PolyScholar')
        send(dict(jsonrpc='2.0', method='notifications/initialized'))
        send(dict(jsonrpc='2.0', id=2, method='tools/list'))
        tools = receive()['result']['tools']
        self.assertEqual(sorted(t['name'] for t in tools), ['cli_docs', 'cli_run'])
        send(dict(jsonrpc='2.0', id=3, method='tools/call',
                  params=dict(name='cli_run',
                              arguments=dict(command='list --json'))))
        call = receive()['result']
        text = call['content'][0]['text']
        self.assertIn('exit=0', text)
        self.assertIn('"s.pdf"', text)  # the seeded document is visible via MCP
        server.stdin.close()
        server.wait(timeout=10)

if __name__ == '__main__':
    unittest.main()
