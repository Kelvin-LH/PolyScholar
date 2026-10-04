# SPDX-License-Identifier: AGPL-3.0-only
"""stdio handshake smoke: initialize → tools/list → tools/call against a temp library."""
import json
import os
import queue
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from polyscholar.service import LocalService

class StdioHandshakeTests(unittest.TestCase):
    def open_session(self, root, read_only=False):
        env = {**os.environ, 'POLYSCHOLAR_DATA_DIR': str(root / 'data'),
               'POLYSCHOLAR_MCP_READ_ONLY': '1' if read_only else '0'}
        server = subprocess.Popen(
            [sys.executable, '-m', 'polyscholar.mcp_server'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, cwd=str(Path(__file__).resolve().parents[1]))
        def cleanup():
            if server.poll() is None:
                server.kill()
            server.wait(timeout=5)
            for stream in (server.stdin, server.stdout):
                if stream and not stream.closed:
                    stream.close()
        self.addCleanup(cleanup)

        def send(payload):
            server.stdin.write((json.dumps(payload) + '\n').encode())
            server.stdin.flush()

        def receive():
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: lines.put(server.stdout.readline()), daemon=True)
            reader.start()
            try:
                line = lines.get(timeout=10)
            except queue.Empty:
                self.fail('MCP stdio response exceeded 10 seconds')
            return json.loads(line) if line else {}

        return server, send, receive

    def test_initialize_list_and_call(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        service = LocalService(root / 'data')
        source = root / 's.pdf'
        source.write_bytes(b'%PDF-1.7 mcp stdio smoke')
        service.import_pdf(source)
        service.close()  # release the lock before spawning the MCP server subprocess

        server, send, receive = self.open_session(root)

        send(dict(jsonrpc='2.0', id=1, method='initialize',
                  params=dict(protocolVersion='2025-06-18', capabilities={},
                              clientInfo=dict(name='smoke', version='0'))))
        init = receive()
        self.assertEqual(init['result']['serverInfo']['name'], 'PolyScholar')
        send(dict(jsonrpc='2.0', method='notifications/initialized'))
        send(dict(jsonrpc='2.0', id=2, method='tools/list'))
        tools = receive()['result']['tools']
        self.assertEqual(sorted(t['name'] for t in tools), ['cli_docs', 'cli_execute', 'cli_run', 'library_status'])
        send(dict(jsonrpc='2.0', id=3, method='tools/call',
                  params=dict(name='cli_run',
                              arguments=dict(command='list --json'))))
        call = receive()['result']
        text = call['content'][0]['text']
        self.assertIn('exit=0', text)
        self.assertIn('"s.pdf"', text)  # the seeded document is visible via MCP
        execute_tool = next(tool for tool in tools if tool['name'] == 'cli_execute')
        self.assertFalse(execute_tool['annotations']['readOnlyHint'])
        self.assertTrue(execute_tool['annotations']['openWorldHint'])
        self.assertTrue(execute_tool['annotations']['destructiveHint'])
        send(dict(jsonrpc='2.0', id=4, method='tools/call', params=dict(
            name='cli_execute', arguments=dict(argv=['list', '--json']))))
        executed = receive()['result']['structuredContent']
        self.assertTrue(executed['success'])
        self.assertEqual(executed['operation'], 'read')
        self.assertIn('s.pdf', executed['stdout'])
        send(dict(jsonrpc='2.0', id=5, method='tools/call', params=dict(
            name='cli_execute', arguments=dict(argv=['list', '--data-dir=/other']))))
        rejected = receive()['result']['structuredContent']
        self.assertEqual(rejected['error_code'], 'library_override')
        send(dict(jsonrpc='2.0', id=6, method='resources/list'))
        resources = receive()['result']['resources']
        self.assertEqual({item['uri'] for item in resources}, {
            'polyscholar://docs/cli', 'polyscholar://rubrics/paper', 'polyscholar://rubrics/confidence'})
        for request_id, uri in enumerate(('polyscholar://docs/cli', 'polyscholar://rubrics/paper',
                                          'polyscholar://rubrics/confidence'), start=7):
            send(dict(jsonrpc='2.0', id=request_id, method='resources/read', params=dict(uri=uri)))
            self.assertTrue(receive()['result']['contents'][0]['text'])
        holder = LocalService(root / 'data')
        try:
            send(dict(jsonrpc='2.0', id=10, method='tools/call', params=dict(
                name='library_status', arguments={})))
            status = receive()['result']['structuredContent']
            self.assertTrue(status['success'])
            self.assertEqual(status['status']['lock'], 'busy')
            self.assertIn('关闭', status['next_action'])
        finally:
            holder.close()
        server.stdin.close()
        server.wait(timeout=10)

    def test_read_only_annotations_and_rejection_over_stdio(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        service = LocalService(root / 'data')
        service.close()
        server, send, receive = self.open_session(root, read_only=True)
        send(dict(jsonrpc='2.0', id=1, method='initialize', params=dict(
            protocolVersion='2025-06-18', capabilities={}, clientInfo=dict(name='readonly-test', version='0'))))
        self.assertIn('result', receive())
        send(dict(jsonrpc='2.0', method='notifications/initialized'))
        send(dict(jsonrpc='2.0', id=2, method='tools/list'))
        tools = receive()['result']['tools']
        execute_tool = next(tool for tool in tools if tool['name'] == 'cli_execute')
        self.assertFalse(execute_tool['annotations']['readOnlyHint'])
        self.assertFalse(execute_tool['annotations']['destructiveHint'])
        self.assertFalse(execute_tool['annotations']['openWorldHint'])
        send(dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(
            name='cli_execute', arguments=dict(argv=['translate', 'doc']))))
        result = receive()['result']['structuredContent']
        self.assertEqual(result['error_code'], 'read_only')
        self.assertIsNone(result['exit_code'])
        send(dict(jsonrpc='2.0', id=4, method='tools/call', params=dict(
            name='library_status', arguments={})))
        self.assertTrue(receive()['result']['structuredContent']['read_only'])
        server.stdin.close()
        server.wait(timeout=10)

if __name__ == '__main__':
    unittest.main()
