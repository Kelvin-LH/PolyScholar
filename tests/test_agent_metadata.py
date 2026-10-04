# SPDX-License-Identifier: AGPL-3.0-only
"""Agent metadata exchange retains validated local boundaries.
代理元数据交换保持校验及本地数据边界。
"""
from pathlib import Path
import tempfile
import unittest

from polyscholar.metadata import exchange_metadata, ITEM_TYPES
from polyscholar.citation_import import CitationImporter
from polyscholar.searches import matches_query, validate_query
from polyscholar.store import LocalStore


class AgentMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = LocalStore(Path(self.temporary.name) / 'library')
        self.addCleanup(self.store.close)
        self.document = self.store.create_bibliographic_item({
            'title': 'Explicit arXiv preprint', 'itemType': 'arxiv-preprint',
            'url': 'https://arxiv.org/abs/2401.12345',
            'abstract': 'Research abstract with 中文 evidence.',
            'date': '2024-01-20',
        })

    def test_preprint_is_fileless_and_can_change_type_without_losing_common_fields(self):
        self.assertIn('arxiv-preprint', ITEM_TYPES)
        self.assertIsNone(self.document['sha256'])
        changed = self.store.update_document(self.document['id'], {'itemType': 'article-journal'})
        self.assertEqual(changed['url'], self.document['url'])
        self.assertEqual(changed['abstract'], self.document['abstract'])

    def test_three_formats_preserve_preprint_and_web_metadata(self):
        importer = CitationImporter()
        for format in ('bibtex', 'ris', 'csl-json'):
            with self.subTest(format=format):
                body = exchange_metadata(self.document, format)
                items, warnings = importer._items(body, format)
                self.assertFalse(warnings)
                self.assertTrue(items[0]['valid'], items[0]['errors'])
                data = items[0]['metadata']
                self.assertEqual(data['itemType'], 'arxiv-preprint')
                self.assertEqual(data['url'], self.document['url'])
                self.assertEqual(data['abstract'], self.document['abstract'])
                self.assertEqual(data['date'], '2024-01-20')

    def test_url_and_abstract_are_shared_search_fields(self):
        for field, text in (('url', 'arxiv.org'), ('abstract', '中文')):
            query = validate_query(dict(match='all', conditions=[dict(field=field, operator='contains', value=text)]))
            self.assertTrue(matches_query(self.document, query))

    def test_active_private_and_credential_urls_rejected_atomically(self):
        for url in ('javascript:alert(1)', 'file:///private/source.pdf',
                    'https://secret:token@example.org', 'https://arxiv.org/\nprivate'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.store.update_document(self.document['id'], {'url': url, 'abstract': 'changed'})
        self.assertEqual(self.store.document(self.document['id'])['abstract'], self.document['abstract'])

    def test_invalid_import_url_is_ignored_with_warning_and_never_fetched(self):
        items, _ = CitationImporter()._items('[{"type":"article-journal","title":"safe","URL":"file:///private/source.pdf"}]', 'csl-json')
        self.assertTrue(items[0]['valid'])
        self.assertFalse(items[0]['metadata'].get('url'))
        self.assertTrue(items[0]['warnings'])


if __name__ == '__main__':
    unittest.main()
