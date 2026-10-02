# SPDX-License-Identifier: AGPL-3.0-only
"""Real locally generated PDF parsing, including rotated/cropped geometry."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from polyscholar.service import LocalService

try:
    import pymupdf
except ImportError:
    pymupdf = None


@unittest.skipIf(pymupdf is None, 'Install project PyMuPDF dependency to test real PDF extraction')
class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.service=LocalService(self.root/'data');self.addCleanup(self.service.close)

    def pdf(self, name, text='Original evidence text.', rotation=0, crop=False):
        doc=pymupdf.open();page=doc.new_page(width=400,height=500)
        if text:page.insert_text((80,120),text)
        if crop:page.set_cropbox(pymupdf.Rect(50,50,350,450))
        page.set_rotation(rotation);path=self.root/name;doc.save(path);doc.close()
        return self.service.import_pdf(path)

    def test_real_text_and_rotated_crop_geometry_saved_without_network(self):
        item=self.pdf('rotated.pdf',rotation=90,crop=True)
        source=self.service.store.object_path(item);before=hashlib.sha256(source.read_bytes()).hexdigest()
        with patch('polyscholar.service.build_opener',side_effect=AssertionError('No API call allowed')):
            revision=self.service.parse_document(item['id'])
        block=self.service.document_blocks(item['id'])[0]
        self.assertEqual(revision['status'],'ready')
        self.assertIn('Original evidence text.',block['text'])
        self.assertEqual(block['pageTransform']['rotation'],90)
        self.assertEqual(block['pageTransform']['cropBox'],[50,50,350,450])
        self.assertEqual((block['pageTransform']['width'],block['pageTransform']['height']),(400,300))
        with pymupdf.open(source) as doc:
            expected=doc[0].search_for('Original evidence text.')[0]*doc[0].rotation_matrix
            actual=pymupdf.Rect(block['bbox'][0]*400,block['bbox'][1]*300,block['bbox'][2]*400,block['bbox'][3]*300)
            self.assertLess(abs(actual.x0-expected.x0)+abs(actual.y0-expected.y0)+abs(actual.x1-expected.x1)+abs(actual.y1-expected.y1),0.01)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)

    def test_blank_pdf_is_no_text_and_reparse_marks_old_evidence_stale(self):
        blank=self.pdf('blank.pdf',text='')
        self.assertEqual(self.service.parse_document(blank['id'])['status'],'no_text')
        self.assertEqual(self.service.document_blocks(blank['id']),[])
        item=self.pdf('text.pdf');self.service.parse_document(item['id'])
        block=self.service.document_blocks(item['id'])[0]
        claim=self.service.save_claim(item['id'],'Local note',[{'blockId':block['id'],'quote':'Original evidence text.'}])
        self.assertFalse(claim['stale'])
        self.service.parse_document(item['id'])
        self.assertTrue(self.service.list_claims(item['id'])[0]['stale'])

    def test_invalid_pdf_and_modified_source_do_not_create_ir(self):
        invalid=self.root/'bad.pdf';invalid.write_bytes(b'%PDF-1.7 invalid')
        item=self.service.import_pdf(invalid)
        with self.assertRaises(ValueError):self.service.parse_document(item['id'])
        self.assertIsNone(self.service.current_document_ir(item['id']))
        item=self.pdf('valid.pdf');source=self.service.store.object_path(item);source.chmod(0o600);source.write_bytes(b'%PDF-1.7 changed')
        with self.assertRaisesRegex(ValueError,'变化'):self.service.parse_document(item['id'])
        self.assertIsNone(self.service.current_document_ir(item['id']))


if __name__=='__main__':unittest.main()
