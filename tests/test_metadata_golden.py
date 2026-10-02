# SPDX-License-Identifier: AGPL-3.0-only
"""Independent exchange vectors and copied-text boundary regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from polyscholar.service import LocalService


class MetadataGoldenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root / 'data');self.addCleanup(self.service.close)
        path = self.root / 'source.pdf';path.write_bytes(b'%PDF-1.7 metadata golden fixture')
        self.doc = self.service.import_pdf(path)

    def test_book_csl_vector_preserves_corporation_and_editor_order(self):
        self.service.update_document(self.doc['id'], dict(itemType='book', title='研究方法', date='2024-02-29',
            publisher='University Press', place='Melbourne', isbn='978-0-000-00000-0', edition='2',
            creators=[dict(type='person', role='author', family='Li', given='Ming'),
                      dict(type='organization', role='author', literal='Research and Development Group'),
                      dict(type='person', role='editor', literal='Unparsed Editor')]))
        item = json.loads(self.service.format_metadata(self.doc['id'], 'csl-json'))[0]
        self.assertEqual(item, dict(id=self.doc['id'], type='book', title='研究方法',
            author=[dict(family='Li', given='Ming'), dict(literal='Research and Development Group')],
            editor=[dict(literal='Unparsed Editor')], publisher='University Press',
            **{'publisher-place': 'Melbourne', 'ISBN': '978-0-000-00000-0', 'edition': '2',
               'issued': {'date-parts': [[2024, 2, 29]]}}))
        bib = self.service.format_metadata(self.doc['id'], 'bibtex')
        self.assertIn('author = {{Li}, {Ming} and {Research and Development Group}}', bib)
        self.assertIn('editor = {{Unparsed Editor}}', bib)
        ris = self.service.format_metadata(self.doc['id'], 'ris')
        self.assertEqual([line for line in ris.splitlines() if line.startswith(('AU', 'A2'))],
                         ['AU  - Li, Ming', 'AU  - Research and Development Group', 'A2  - Unparsed Editor'])

    def test_copied_unicode_line_separators_cannot_create_extra_ris_records(self):
        for separator in ('\u0085', '\u2028', '\u2029'):
            self.service.update_document(self.doc['id'], {'title': f'Valid{separator}ER  -{separator}TY  - JOUR'})
            lines = self.service.format_metadata(self.doc['id'], 'ris').splitlines()
            self.assertEqual(sum(line.startswith('TY  -') for line in lines), 1)
            self.assertEqual(sum(line.startswith('ER  -') for line in lines), 1)

    def test_iso_dates_reject_non_ascii_digits_before_any_change(self):
        before = self.service.store.document(self.doc['id'])
        with self.assertRaises(ValueError):
            self.service.update_document(self.doc['id'], {'date': '２０２４-０２-２９'})
        self.assertEqual(self.service.store.document(self.doc['id']), before)
