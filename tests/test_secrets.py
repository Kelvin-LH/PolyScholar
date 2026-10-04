# SPDX-License-Identifier: AGPL-3.0-only
"""Key persistence via OS credential store; plaintext fallback must never exist."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from keyring.errors import NoKeyringError

from polyscholar import secrets
from polyscholar.service import LocalService

class FakeKeyring:
    def __init__(self):
        self.store = {}
    def set_password(self, service, account, value):
        self.store[(service, account)] = value
    def get_password(self, service, account):
        return self.store.get((service, account))
    def delete_password(self, service, account):
        self.store.pop((service, account), None)

class SecretsTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKeyring()
        patcher = patch.object(secrets, 'keyring', self.fake)
        patcher.start();self.addCleanup(patcher.stop)

    def test_store_load_clear_roundtrip(self):
        self.assertIsNone(secrets.load_secret())
        secrets.store_secret('key-123')
        self.assertEqual(secrets.load_secret(), 'key-123')
        self.assertTrue(secrets.load_secret() == 'key-123')
        secrets.clear_secret()
        self.assertIsNone(secrets.load_secret())

    def test_store_rejects_empty(self):
        with self.assertRaises(ValueError):
            secrets.store_secret('')

    def test_missing_keyring_is_explicit_not_plaintext(self):
        class NoBackend:
            def set_password(self, *args): raise NoKeyringError('no backend')
            def get_password(self, *args): raise NoKeyringError('no backend')
            def delete_password(self, *args): raise NoKeyringError('no backend')
        with patch.object(secrets, 'keyring', NoBackend()):
            with self.assertRaisesRegex(ValueError, '凭据库'):
                secrets.store_secret('k')
            self.assertIsNone(secrets.load_secret())

class ServiceKeyFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = LocalService(Path(self.temp.name) / 'library')

    def tearDown(self):
        self.service.close()

    def test_effective_key_prefers_session_then_store(self):
        absent = Path(self.temp.name) / 'no-key-file'
        with patch.object(secrets, 'keyring', FakeKeyring()), patch.object(secrets, '_key_file', lambda: absent):
            self.assertEqual(self.service._effective_key(), '')
        self.service.set_session_key('session-key')
        stored = FakeKeyring();stored.set_password(secrets.SERVICE, secrets.ACCOUNT, 'stored-key')
        absent = Path(self.temp.name) / 'no-key-file'
        with patch.object(secrets, 'keyring', stored), patch.object(secrets, '_key_file', lambda: absent):
            self.assertEqual(self.service._effective_key(), 'session-key')
        self.service.set_session_key('')
        with patch.object(secrets, 'keyring', stored):
            self.assertEqual(self.service._effective_key(), 'stored-key')

    def test_store_api_key_persists_and_clear_removes(self):
        absent = Path(self.temp.name) / 'no-key-file'
        with patch.object(secrets, 'keyring', FakeKeyring()), patch.object(secrets, '_key_file', lambda: absent):
            self.service.store_api_key('persisted')
            self.assertEqual(self.service._effective_key(), 'persisted')
            self.assertTrue(self.service.api_key_stored())
            self.service.clear_stored_api_key()
            self.assertFalse(self.service.api_key_stored())

class FileSecretTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        patcher = patch.object(secrets, '_key_file', lambda: self.base / '.polyscholar' / 'api_key')
        patcher.start();self.addCleanup(patcher.stop)

    def test_file_storage_is_rejected_without_writing(self):
        with self.assertRaisesRegex(ValueError, '明文'):
            secrets.store_secret_file('file-key')
        self.assertFalse((self.base / '.polyscholar' / 'api_key').exists())

    def test_legacy_file_is_not_activated_and_can_be_cleared(self):
        path = self.base / '.polyscholar' / 'api_key'
        path.parent.mkdir()
        path.write_text('legacy-key')
        self.assertIsNone(secrets.load_secret_file())
        secrets.clear_secret_file()
        self.assertFalse(path.exists())

if __name__ == '__main__':
    unittest.main()
