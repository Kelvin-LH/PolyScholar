# SPDX-License-Identifier: AGPL-3.0-only
"""Real generated PDFs: attachment identity through extraction and loopback summary."""
from pathlib import Path
import tempfile
import unittest
import pymupdf
from polyscholar.service import LocalService
from test_summary_http import provider


class AttachmentFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root / 'data');self.addCleanup(self.service.close)
        self.parent_source = self.make_pdf('paper.pdf', 'Original main paper contains 20 samples.')
        self.child_source = self.make_pdf('supplement.pdf', 'Study included 12 samples.')
        self.parent = self.service.import_pdf(self.parent_source)

    def make_pdf(self, name, text):
        path = self.root / name
        with pymupdf.open() as pdf:
            page = pdf.new_page();page.insert_text((72, 72), text);pdf.save(path)
        return path

    def test_child_pdf_identity_is_preserved_through_real_parse_and_summary(self):
        attachment = self.service.import_attachment(self.parent['id'], self.child_source, 'supplement')
        child_id = attachment.get('documentId', attachment['id'])
        original = self.parent_source.read_bytes();supplement = self.child_source.read_bytes()
        self.assertNotEqual(child_id, self.parent['id'])
        self.assertEqual([d['id'] for d in self.service.list_documents()], [self.parent['id']])
        revision = self.service.parse_document(child_id)
        blocks = self.service.document_blocks(child_id)
        self.assertIn('12 samples', blocks[0]['text'])
        self.assertNotIn('20 samples', blocks[0]['text'])
        self.assertEqual(revision['sha256'], self.service.store.document(child_id)['sha256'])
        self.assertIsNone(self.service.current_document_ir(self.parent['id']))
        with provider() as (address, seen):
            self.service.save_settings({'endpoint': address, 'model': 'synthetic-model', 'timeoutSeconds': 3})
            self.service.set_session_key('dummy-attachment-key')
            saved = self.service.summarize_document(child_id, [blocks[0]['id']], revision['id'])
        self.assertEqual(len(seen), 1)
        self.assertEqual(saved[0]['documentId'], child_id)
        self.assertEqual(saved[0]['evidence'][0]['revisionId'], revision['id'])
        self.assertEqual(self.service.list_claims(self.parent['id']), [])
        self.assertEqual(self.parent_source.read_bytes(), original)
        self.assertEqual(self.child_source.read_bytes(), supplement)
        self.service.close()
        self.service = LocalService(self.root / 'data');self.addCleanup(self.service.close)
        self.assertEqual(len(self.service.list_attachments(self.parent['id'])), 2)
        self.assertEqual(self.service.list_claims(child_id)[0]['id'], saved[0]['id'])
