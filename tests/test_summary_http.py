# SPDX-License-Identifier: AGPL-3.0-only
"""Actual loopback protocol checks, with generated source and dummy credentials only."""
from contextlib import contextmanager, closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
from pathlib import Path
import tempfile
import shutil
import sys
import threading
import time
import unittest
from polyscholar.service import LocalService
from polyscholar.summary_model import request_summary


@contextmanager
def provider(mode='ok'):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append((self.path, self.headers.get('Authorization'), body))
            if mode == 'slow_headers':
                try:
                    self.wfile.write(b'HTTP/1.1 200 OK\r\n');self.wfile.flush()
                    for number in range(20):
                        self.wfile.write(f'X-Slow-{number}: value\r\n'.encode());self.wfile.flush();time.sleep(.2)
                    self.wfile.write(b'Content-Length: 0\r\n\r\n');self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if mode == 'redirect':
                self.send_response(307)
                self.send_header('Location', '/must-not-receive-key')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            block = json.loads(body['messages'][1]['content'])['sourceBlocks'][0]
            claim = dict(text='The authors report twelve samples.', category='data', attribution='author_report',
                         evidence=[dict(blockId=block['blockId'], quote='12 samples' if mode == 'ok' else '900 fabricated samples')])
            response = json.dumps(dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(dict(claims=[claim]))))],
                                       usage=dict(prompt_tokens=30, completion_tokens=10, total_tokens=40))).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            try:
                if mode == 'slow':
                    for byte in response:
                        self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.05)
                else:
                    self.wfile.write(response)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


class SummaryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root / 'data')
        self.addCleanup(self.service.close)
        source = self.root / 'private-marker.pdf'
        source.write_bytes(b'%PDF-1.7 synthetic HTTP scope fixture')
        self.doc = self.service.import_pdf(source)
        self.revision = self.service.store.replace_document_ir(self.doc['id'], 'http-fixture', [
            dict(number=1, width=600, height=800, blocks=[
                dict(text='Study included 12 samples.', bbox=[0, 0, .5, .2], kind='text', order=0),
                dict(text='UNSELECTED_PRIVATE_MARKER', bbox=[0, .3, .5, .4], kind='text', order=1)])])
        self.block = self.service.document_blocks(self.doc['id'])[0]
        self.service.set_session_key('dummy-summary-http-key')

    def configure(self, address):
        settings = self.service.get_settings()
        settings.update(endpoint=address, model='synthetic-json-model', timeoutSeconds=2)
        self.service.save_settings(settings)

    def test_real_post_only_sends_selected_source_and_persists_safe_provenance(self):
        with provider() as (address, seen):
            self.configure(address)
            claims = self.service.summarize_document(self.doc['id'], [self.block['id']], self.revision['id'])
        self.assertEqual(len(seen), 1)
        path, auth, payload = seen[0]
        self.assertEqual(path, '/v1/chat/completions')
        self.assertEqual(auth, 'Bearer dummy-summary-http-key')
        request = json.dumps(payload)
        for excluded in ('UNSELECTED_PRIVATE_MARKER', self.doc['id'], 'private-marker.pdf', str(self.root), 'dummy-summary-http-key'):
            self.assertNotIn(excluded, request)
        self.assertEqual(claims[0]['evidence'][0]['pageNumber'], 1)
        self.assertEqual(claims[0]['provenance']['source'], 'model')
        self.assertEqual(claims[0]['provenance']['usage']['total_tokens'], 40)
        for file in (self.root / 'data').rglob('*'):
            if file.is_file():
                self.assertNotIn(b'dummy-summary-http-key', file.read_bytes())

    def test_actual_redirect_is_refused_and_key_is_not_forwarded(self):
        with provider('redirect') as (address, seen):
            self.configure(address)
            with self.assertRaises(ValueError):
                self.service.summarize_document(self.doc['id'], [self.block['id']], self.revision['id'])
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.service.list_claims(self.doc['id']), [])

    def test_actual_fabricated_quote_response_does_not_persist(self):
        with provider('fabricated') as (address, seen):
            self.configure(address)
            with self.assertRaises(ValueError):
                self.service.summarize_document(self.doc['id'], [self.block['id']], self.revision['id'])
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.service.list_claims(self.doc['id']), [])

    def test_slow_response_body_cannot_reset_request_budget(self):
        with provider('slow') as (address, seen):
            self.configure(address)
            settings = self.service.get_settings();settings['timeoutSeconds'] = 1
            self.service.save_settings(settings)
            started = time.monotonic()
            with self.assertRaises(ValueError):
                self.service.summarize_document(self.doc['id'], [self.block['id']], self.revision['id'])
            self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.service.list_claims(self.doc['id']), [])

    def test_slow_headers_obey_whole_request_deadline(self):
        with provider('slow_headers') as (address, seen):
            self.configure(address)
            settings = self.service.get_settings();settings['timeoutSeconds'] = 1
            self.service.save_settings(settings)
            started = time.monotonic()
            with self.assertRaises(ValueError):
                self.service.summarize_document(self.doc['id'], [self.block['id']], self.revision['id'])
            self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.service.list_claims(self.doc['id']), [])

    def test_isolated_worker_loads_staged_helper_without_installed_app_package(self):
        staged = self.root / 'staged' / 'integrations';staged.mkdir(parents=True)
        repository = Path(__file__).resolve().parents[1]
        shutil.copy2(repository / 'integrations/summary_worker.py', staged / 'summary_worker.py')
        shutil.copy2(repository / 'polyscholar/summary_model.py', staged / 'summary_model.py')
        with provider() as (address, seen):
            claims, usage = request_summary(address, 'dummy-staged-key', 'synthetic-json-model', 'zh',
                [self.block], 2, python_path=sys.executable, worker_path=staged / 'summary_worker.py')
        self.assertEqual(len(seen), 1)
        self.assertEqual(claims[0]['evidence'][0]['quote'], '12 samples')
        self.assertEqual(usage['total_tokens'], 40)

    def test_close_terminates_actual_inflight_summary_worker_without_saving(self):
        failures = []
        def summarize():
            try:
                self.service.summarize_document(self.doc['id'], [self.block['id']], self.revision['id'])
            except ValueError as error:
                failures.append(str(error))
        with provider('slow_headers') as (address, seen):
            self.configure(address)
            settings = self.service.get_settings();settings['timeoutSeconds'] = 30
            self.service.save_settings(settings)
            thread = threading.Thread(target=summarize)
            thread.start()
            try:
                deadline = time.monotonic() + 4
                while not seen and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(seen)
                self.assertTrue(self.service._children)
                self.service.close()
            finally:
                self.service.close();thread.join(7)
            self.assertFalse(thread.is_alive())
        self.assertTrue(failures)
        self.assertEqual(self.service._children, {})
        with closing(sqlite3.connect(self.root / 'data' / 'library.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_model_summaries').fetchone()[0], 0)
