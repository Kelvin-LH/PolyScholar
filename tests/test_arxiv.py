# SPDX-License-Identifier: AGPL-3.0-only
"""arXiv import checks against a loopback server; no real network in tests."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from polyscholar import arxiv
from polyscholar.metadata import exchange_metadata
from polyscholar.service import LocalService

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2312.04567v2</id>
    <title>  A Study of
        Synthetic Fonts  </title>
    <summary>  This paper studies
    synthetic fonts with care.  </summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan M. Turing</name></author>
    <published>2023-12-07T18:59:00Z</published>
    <arxiv:doi>10.1000/synthetic.1</arxiv:doi>
    <link href="http://127.0.0.1:{port}/pdf/2312.04567" type="application/pdf" title="pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/1994.00001v1</id>
    <title>An Earlier Synthetic Work</title>
    <summary>Earlier summary text.</summary>
    <author><name>Grace Hopper</name></author>
    <published>1994-03-01T00:00:00Z</published>
    <link href="http://127.0.0.1:{port}/pdf/missing" type="application/pdf" title="pdf"/>
  </entry>
</feed>
"""

PDF_BYTES = b'%PDF-1.4\n' + b'synthetic body ' * 64

@contextmanager
def arxiv_server():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            seen.append(self.path)
            body = b''
            if self.path.startswith('/api/query/badxml'):
                body = b'<definitely-not-xml'
                self.send_response(200)
            elif self.path.startswith('/api/query'):
                body = FEED.format(port=self.server.server_address[1]).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/atom+xml')
            elif self.path.startswith('/pdf/2312.04567'):
                body = PDF_BYTES
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
            else:
                self.send_response(404)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%d/api/query' % server.server_address[1], seen
    finally:
        server.shutdown()
        server.server_close()

class ParseTests(unittest.TestCase):
    def test_links_and_bare_identifiers_normalize(self):
        cases = {'https://arxiv.org/abs/2312.04567': '2312.04567',
                 'http://arxiv.org/pdf/2312.04567v3.pdf': '2312.04567',
                 'https://export.arxiv.org/abs/2312.04567v2': '2312.04567',
                 'arxiv.org/abs/cs/0301012v1': 'cs/0301012',
                 '2312.04567': '2312.04567',
                 'math.GT/0309136': 'math.GT/0309136',
                 'https://arxiv.org/abs/2312.04567?context=cs': '2312.04567'}
        for text, expected in cases.items():
            self.assertEqual(arxiv.parse_identifier(text), expected, text)

    def test_rejects_non_arxiv_and_invalid_identifiers(self):
        for text in ['https://example.org/abs/2312.04567', '1234.567', 'README/x1234567',
                     'not-a-paper', '', None, 'x' * 301]:
            self.assertIsNone(arxiv.parse_identifier(text), text)

    def test_parse_identifiers_deduplicates_and_keeps_order(self):
        text = 'https://arxiv.org/abs/2312.04567\n2312.04567v2，https://arxiv.org/abs/cs/0301012'
        self.assertEqual(arxiv.parse_identifiers(text), ['2312.04567', 'cs/0301012'])

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = LocalService(Path(self.temp.name) / 'library')

    def tearDown(self):
        self.service.close()

    def find_document(self, document_id):
        return next(d for d in self.service.list_documents() if d['id'] == document_id)

    def test_lookup_parses_atom_fields_and_sends_only_ids(self):
        with arxiv_server() as (base, seen):
            entries = self.service.arxiv_lookup('https://arxiv.org/abs/2312.04567v2', endpoint=base)
        first = entries[0]
        self.assertEqual(first['identifier'], '2312.04567')
        self.assertEqual(first['title'], 'A Study of Synthetic Fonts')
        self.assertEqual(first['abstract'], 'This paper studies synthetic fonts with care.')
        self.assertEqual(first['authors'], ['Ada Lovelace', 'Alan M. Turing'])
        self.assertEqual(first['date'], '2023-12-07')
        self.assertEqual(first['doi'], '10.1000/synthetic.1')
        self.assertEqual(first['url'], 'https://arxiv.org/abs/2312.04567')
        self.assertIn('/pdf/2312.04567', first['pdf_url'])
        self.assertTrue(seen[0].startswith('/api/query?id_list=2312.04567'))

    def test_lookup_rejects_unrecognized_text(self):
        with arxiv_server() as (base, seen):
            with self.assertRaisesRegex(ValueError, '未识别'):
                self.service.arxiv_lookup('hello world', endpoint=base)
        self.assertEqual(seen, [])

    def test_lookup_rejects_broken_metadata(self):
        with arxiv_server() as (base, seen):
            with self.assertRaisesRegex(ValueError, '解析'):
                self.service.arxiv_lookup('2312.04567', endpoint=base + '/badxml')

    def test_import_downloads_fills_metadata_and_deduplicates(self):
        with arxiv_server() as (base, seen):
            entries = self.service.arxiv_lookup('2312.04567', endpoint=base)
            collection = self.service.create_collection('arXiv')
            result = self.service.arxiv_import(entries[:1], collection['id'])
            self.assertEqual(len(result['imported']), 1)
            self.assertEqual(result['errors'], [])
            document = self.find_document(result['imported'][0]['id'])
            self.assertEqual(document['title'], 'A Study of Synthetic Fonts')
            self.assertEqual(document['abstract'], 'This paper studies synthetic fonts with care.')
            self.assertEqual(document['year'], '2023')
            self.assertEqual(document['date'], '2023-12-07')
            self.assertEqual([c['literal'] for c in document['creators']], ['Ada Lovelace', 'Alan M. Turing'])
            self.assertEqual(document['url'], 'https://arxiv.org/abs/2312.04567')
            self.assertEqual(self.service.document_collections(document['id']), [collection['id']])
            again = self.service.arxiv_import(entries[:1], collection['id'])
            self.assertEqual(again['imported'][0]['id'], document['id'])
            self.assertEqual(len(self.service.list_documents()), 1)

    def test_import_reports_per_entry_failures(self):
        with arxiv_server() as (base, seen):
            entries = self.service.arxiv_lookup('2312.04567', endpoint=base)
            result = self.service.arxiv_import(entries)
            self.assertEqual(len(result['imported']), 1)
            self.assertEqual(len(result['errors']), 1)
            self.assertIn('1994.00001', result['errors'][0])

    def test_export_includes_abstract_and_url_without_control_characters(self):
        with arxiv_server() as (base, seen):
            entries = self.service.arxiv_lookup('2312.04567', endpoint=base)
            result = self.service.arxiv_import(entries[:1])
        document = self.find_document(result['imported'][0]['id'])
        self.service.update_document(document['id'], {'abstract': 'line one\nline two'})
        csl = exchange_metadata(self.find_document(document['id']), 'csl-json')
        self.assertIn('"abstract"', csl)
        self.assertIn('"URL"', csl)
        bibtex = exchange_metadata(self.find_document(document['id']), 'bibtex')
        self.assertIn('url = {', bibtex)
        self.assertIn('abstract = {line one line two}', bibtex)
        ris = exchange_metadata(self.find_document(document['id']), 'ris')
        self.assertIn('UR  - https://arxiv.org/abs/2312.04567', ris)
        self.assertIn('AB  - line one line two', ris)
        self.assertEqual(ris.count('ER  -'), 1)

if __name__ == '__main__':
    unittest.main()
