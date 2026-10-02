# SPDX-License-Identifier: AGPL-3.0-only
"""Independent literal/CJK vectors and current-revision index lifecycle."""
from pathlib import Path
import tempfile
import unittest
from polyscholar.service import LocalService


def pages(text):
    return [dict(number=1, width=600, height=800, rotation=0, cropBox=[0, 0, 600, 800],
                 blocks=[dict(text=text, bbox=[.1, .2, .8, .3], kind='text', order=0)])]


class FulltextVectorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = LocalService(self.root / 'data'); self.addCleanup(self.service.close)
        source = self.root / 'root.pdf'; source.write_bytes(b'%PDF-1.7 fulltext vectors root')
        self.parent = self.service.import_pdf(source)
        source = self.root / 'child.pdf'; source.write_bytes(b'%PDF-1.7 fulltext vectors child')
        self.child = self.service.import_attachment(self.parent['id'], source)

    def replace(self, doc, text):
        return self.service.store.replace_document_ir(doc['id'], 'Independent vectors', pages(text))

    def test_short_cjk_casefold_expansion_and_literal_expression_characters(self):
        text = '研究证据 Straße 100% _ "quoted" alpha OR beta'
        self.replace(self.child, text)
        for needle in ('研', '研究', '研究证据', 'STRASSE', '%', '_', '"quoted"', 'alpha OR beta'):
            with self.subTest(needle=needle):
                result = self.service.search_fulltext(needle)
                self.assertEqual(result['total'], 1)
                hit = result['items'][0]
                self.assertEqual((hit['documentId'], hit['parentDocumentId']), (self.child['id'], self.parent['id']))
                self.assertNotIn('text', hit)
                self.assertIn('研究证据 Straße', hit['snippet'])
                self.assertEqual((hit['pageNumber'], hit['bbox']), (1, [.1, .2, .8, .3]))
        self.assertEqual(self.service.search_fulltext('alpha OR missing')['total'], 0)

    def test_reparse_clear_rebuild_and_restart_never_search_old_revisions(self):
        old = self.replace(self.parent, 'obsolete text')
        self.service.store.save_claim(self.parent['id'], 'Retained note', [])
        latest = self.replace(self.parent, 'current text')
        self.assertNotEqual(old['id'], latest['id'])
        self.assertEqual(self.service.search_fulltext('obsolete')['total'], 0)
        self.service.clear_fulltext_index(self.parent['id'])
        self.assertEqual(self.service.search_fulltext('current')['total'], 0)
        self.assertEqual(len(self.service.store.list_claims(self.parent['id'])), 1)
        self.assertEqual(self.service.store.current_document_ir(self.parent['id'])['id'], latest['id'])
        self.service.close(); self.service = LocalService(self.root / 'data'); self.addCleanup(self.service.close)
        self.assertEqual(self.service.search_fulltext('current')['total'], 0)
        self.service.rebuild_fulltext_index(self.parent['id'])
        hits = self.service.search_fulltext('current')['items']
        self.assertEqual([hit['revisionId'] for hit in hits], [latest['id']])
        self.service.delete_attachment(self.parent['id'], self.child['id'])
        self.service.delete_document(self.parent['id'])
        self.assertEqual(self.service.search_fulltext('current')['total'], 0)

    def test_large_block_returns_bounded_original_excerpt(self):
        self.replace(self.parent, 'prefix ' + 'x' * 100000 + ' Straße end ' + 'y' * 100000)
        hit = self.service.search_fulltext('STRASSE')['items'][0]
        self.assertNotIn('text', hit)
        self.assertLessEqual(len(hit['snippet']), 512)
        self.assertIn('Straße', hit['snippet'])
        self.assertNotIn('STRASSE', hit['snippet'])
