# SPDX-License-Identifier: AGPL-3.0-only
"""Shipped docs resolution / 随包文档定位回归，不代表冻结包验收。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from polyscholar import resource_paths


class ResourcePathTests(unittest.TestCase):
    def test_wheel_prefix_is_used_when_checkout_resources_are_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rubric = root / 'prefix/share/polyscholar/rubrics'
            rubric.mkdir(parents=True)
            with patch.object(resource_paths, '__file__', str(root / 'installed/polyscholar/resource_paths.py')), \
                 patch.object(resource_paths.sys, 'prefix', str(root / 'prefix')):
                self.assertEqual(resource_paths.resource_path('rubrics'), rubric)

    def test_invalid_resource_name_is_rejected(self):
        for name in ('../library.sqlite3', '/tmp/secret'):
            with self.assertRaises(ValueError):
                resource_paths.resource_path(name)

    def test_frozen_missing_resources_never_fall_back_to_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(resource_paths.sys, 'frozen', True, create=True), \
                 patch.object(resource_paths.sys, 'platform', 'linux'), \
                 patch.object(resource_paths.sys, '_MEIPASS', str(root), create=True):
                self.assertEqual(resource_paths.resource_path('rubrics'), root / 'resources/rubrics')
