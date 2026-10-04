# SPDX-License-Identifier: AGPL-3.0-only
"""Actual local HTTP and supervised worker failure paths; no public requests."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import Request, build_opener

from integrations.github_snapshot import GitHubClient, NoRedirect
from polyscholar.service import LocalService
from verification_fixtures import FixtureOpener, github_report, REPOSITORY


class VerificationWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.resources = self.root / 'resources'
        (self.resources / 'integrations').mkdir(parents=True)
        self.service = LocalService(self.root / 'data', resources_dir=self.resources)
        self.addCleanup(self.service.close)
        source = self.root / 'target.pdf'
        source.write_bytes(b'%PDF-1.7 PRIVATE local content never sent to verification')
        self.document = self.service.import_pdf(source)

    def worker(self, body):
        path = self.resources / 'integrations/github_snapshot.py'
        path.write_text('import json,sys,os,time\nrequest=json.loads(sys.stdin.buffer.readline())\n' + body, encoding='utf-8')

    def test_actual_worker_receives_only_repository_and_no_model_credentials(self):
        receipt = self.root / 'received.json'
        payload = github_report()
        self.worker(f"open({str(receipt)!r},'w').write(json.dumps({{'request':request,'env_names':list(os.environ)}}))\n"
                    f"sys.stdout.buffer.write({json.dumps(payload).encode()!r})\n")
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-secret', 'GITHUB_TOKEN': 'synthetic-token',
                                     'POLYSCHOLAR_DATA_DIR': 'private-library-path'}):
            result = self.service.verify_github(self.document['id'], REPOSITORY, authorized=True)
        self.assertEqual(result['status'], 'checked')
        received = json.loads(receipt.read_text())
        self.assertEqual(received['request'], {'repository_url': REPOSITORY})
        self.assertFalse({'OPENAI_API_KEY', 'GITHUB_TOKEN', 'POLYSCHOLAR_DATA_DIR'} & set(received['env_names']))
        self.assertFalse(self.service._children)

    def test_invalid_worker_target_and_payload_do_not_publish_reports(self):
        payload = github_report()
        payload['repository_url'] = 'https://github.com/another/repo'
        self.worker(f"sys.stdout.buffer.write({json.dumps(payload).encode()!r})\n")
        with self.assertRaisesRegex(ValueError, '授权范围'):
            self.service.verify_github(self.document['id'], REPOSITORY, authorized=True)
        self.assertFalse(self.service.list_verifications(self.document['id']))
        self.assertFalse(self.service._children)

    def slow_server(self):
        started = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                started.set()
                try:
                    for byte in b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n':
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.04)
                except (OSError, ConnectionError):
                    pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: thread.join(timeout=2))
        self.addCleanup(server.shutdown)
        self.worker('import urllib.request\n'
                    f"urllib.request.urlopen('http://127.0.0.1:{server.server_port}/slow',timeout=10).read()\n")
        return started

    def test_wall_clock_timeout_stops_slow_headers_and_archives_unknown(self):
        started = self.slow_server()
        begin = time.monotonic()
        with patch('polyscholar.service.VERIFICATION_TIMEOUT', 1.5):
            result = self.service.verify_github(self.document['id'], REPOSITORY, authorized=True)
        self.assertTrue(started.is_set())
        self.assertLess(time.monotonic() - begin, 3)
        report = self.service.verification_report(self.document['id'], result['id'])['report']
        self.assertEqual(report['status'], 'unavailable')
        self.assertEqual(report['error_code'], 'timeout')
        self.assertFalse(self.service._children)

    def test_inflight_service_close_stops_worker_without_saving(self):
        started = self.slow_server()
        failures = []

        def check():
            try:
                self.service.verify_github(self.document['id'], REPOSITORY, authorized=True)
            except ValueError as error:
                failures.append(error)

        thread = threading.Thread(target=check)
        thread.start()
        self.assertTrue(started.wait(5))
        with self.assertRaises(ValueError):
            self.service.delete_document(self.document['id'])
        self.assertTrue(self.service.store.is_active(self.document['id']))
        self.service.close()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(failures)
        self.assertFalse(self.service._children)
        reopened = LocalService(self.root / 'data')
        try:
            self.assertFalse(reopened.list_verifications(self.document['id']))
        finally:
            reopened.close()

    def test_actual_http_snapshot_requests_do_not_follow_readme_commands(self):
        fixture = FixtureOpener()
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append((self.path, dict(self.headers)))
                raw = json.dumps(fixture.responses[self.path]).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            class LocalOpener:
                def open(self, request, timeout):
                    path = request.full_url.removeprefix('https://api.github.com')
                    local = Request(f'http://127.0.0.1:{server.server_port}' + path, headers=request.headers)
                    return build_opener(NoRedirect()).open(local, timeout=timeout)

            report = GitHubClient(LocalOpener()).collect(REPOSITORY)
            self.assertEqual(report['status'], 'checked')
            self.assertEqual(len(requests), 4)
            self.assertTrue(all('Authorization' not in headers for _, headers in requests))
            self.assertNotIn('PRIVATE', json.dumps(requests))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
