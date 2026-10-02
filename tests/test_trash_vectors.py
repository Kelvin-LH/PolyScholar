# SPDX-License-Identifier: AGPL-3.0-only
"""Independent recovery and cleanup vectors. 独立恢复及清理回归向量。"""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from polyscholar.service import LocalService


class TrashVectorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = LocalService(self.root / 'data')
        self.addCleanup(self.service.close)
        self.source = self.root / 'original.pdf'
        self.source.write_bytes(b'%PDF-1.7 independent trash source')
        self.parent = self.service.import_pdf(self.source)
        source = self.root / 'supplement.pdf'
        source.write_bytes(b'%PDF-1.7 independent trash supplement')
        self.child = self.service.import_attachment(self.parent['id'], source)

    def test_nested_visibility_restoration_and_original_identity(self):
        collection = self.service.create_collection('Retained collection')
        self.service.set_membership(self.parent['id'], collection['id'])
        self.service.update_document(self.parent['id'], dict(tags=['retained'], notes='Private retained note'))
        before = self.service.store.document(self.parent['id'])
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.service.trash_document(self.child['id'])
        self.service.trash_document(self.parent['id'])
        self.assertEqual(self.service.list_documents(), [])
        self.assertEqual(self.service.list_tags(), [])
        with self.assertRaises(ValueError):
            self.service.restore_document(self.child['id'])
        self.service.restore_document(self.parent['id'])
        self.assertEqual([d['id'] for d in self.service.list_attachments(self.parent['id'])], [self.parent['id']])
        self.service.restore_document(self.child['id'])
        self.assertEqual(self.service.store.document(self.parent['id']), before)
        self.assertEqual(self.service.document_collections(self.parent['id']), [collection['id']])
        self.assertEqual({d['id'] for d in self.service.list_attachments(self.parent['id'])}, {self.parent['id'], self.child['id']})
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_hash)

    def test_inactive_import_and_edit_do_not_implicitly_restore(self):
        self.service.trash_document(self.parent['id'])
        with self.assertRaisesRegex(ValueError, '回收站'):
            self.service.import_pdf(self.source)
        with self.assertRaisesRegex(ValueError, '回收站'):
            self.service.update_document(self.child['id'], dict(title='Unexpected edit'))
        self.assertEqual(self.service.list_documents(), [])
        self.assertEqual(len(self.service.list_trash()), 1)
        self.service.delete_collection(self.service.create_collection('Temporary')['id'])
        self.service.restore_document(self.parent['id'])
        with self.assertRaises(ValueError):
            self.service.purge_document(self.parent['id'])

    def test_cleanup_failure_is_durable_and_source_files_survive_retry(self):
        self.service.trash_document(self.parent['id'])
        owned = self.service.store.object_path(self.parent)
        unlink = Path.unlink
        def denied(path, *args, **kwargs):
            if path == owned:
                raise PermissionError('synthetic cleanup failure')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', denied):
            result = self.service.purge_document(self.parent['id'])
        self.assertFalse(result['cleanupComplete'])
        self.assertTrue(self.service.list_pending_cleanup())
        self.assertTrue(self.source.exists())
        self.service.close()
        self.service = LocalService(self.root / 'data')
        self.addCleanup(self.service.close)
        self.assertTrue(self.service.list_pending_cleanup())
        self.service.retry_cleanup()
        self.assertEqual(self.service.list_pending_cleanup(), [])
        self.assertFalse(owned.exists())
        self.assertTrue(self.source.exists())

    def test_cleanup_retry_refuses_replaced_path_identity(self):
        self.service.trash_document(self.parent['id'])
        owned = self.service.store.object_path(self.parent)
        unlink = Path.unlink
        def denied(path, *args, **kwargs):
            if path == owned:
                raise PermissionError('synthetic cleanup failure')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', denied):
            self.service.purge_document(self.parent['id'])
        retained = owned.with_suffix('.retained')
        owned.rename(retained)
        replacement = b'%PDF-1.7 different inode and user-owned replacement'
        owned.write_bytes(replacement)
        self.service.retry_cleanup()
        self.assertEqual(owned.read_bytes(), replacement)
        self.assertTrue(retained.exists())
        self.assertTrue(self.service.list_pending_cleanup())
