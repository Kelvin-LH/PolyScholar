# SPDX-License-Identifier: AGPL-3.0-only
"""Standard profile boundaries / 标准资料目录与显式资料目录边界。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from polyscholar.service import LocalService, app_data_dir


class DataDirectoryTests(unittest.TestCase):
    def test_platform_default_stays_outside_source_checkout(self):
        with patch('polyscholar.service.sys.platform', 'linux'), \
             patch.dict('os.environ', {'XDG_DATA_HOME': '/tmp/profile-data'}):
            self.assertEqual(app_data_dir(), Path('/tmp/profile-data/PolyScholar'))

    def test_explicit_directory_does_not_silently_copy_another_library(self):
        # Never copy live WAL/SHM at startup / 启动时不自动复制活动 WAL/SHM。
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'paper.pdf'
            source.write_bytes(b'%PDF-1.7 library boundary')
            with_library = LocalService(root / 'existing')
            try:
                with_library.import_pdf(source)
                other = LocalService(root / 'explicit')
                try:
                    self.assertEqual(other.list_documents(), [])
                    self.assertEqual(len(with_library.list_documents()), 1)
                finally:
                    other.close()
            finally:
                with_library.close()
