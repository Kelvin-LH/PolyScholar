# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic package-check failure contracts, not installed-package acceptance.

仅以合成目录和失败注入验证检查生命周期，不等同实际包或安装验收。
"""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
with patch.object(sys, 'path', [str(SCRIPTS), *sys.path]):
    package_desktop = importlib.import_module('package_desktop')
    relocation = importlib.import_module('check_package_relocation')


class PackageValidationFailureTests(unittest.TestCase):
    def test_failed_engine_check_invalidates_previous_success_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'polyscholar').mkdir()
            for name in ('app.py', 'summary_model.py'):
                (root / 'polyscholar' / name).write_text('', encoding='utf-8')
            for name in ('integrations', 'schemas', 'licenses'):
                (root / name).mkdir()
            for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
                (root / name).write_text('synthetic', encoding='utf-8')
            runtime = root / 'runtime'
            executable = 'python.exe' if sys.platform == 'win32' else 'bin/python3'
            for engine in ('babeldoc', 'pdfmathtranslate'):
                binary = runtime / engine / executable
                binary.parent.mkdir(parents=True)
                binary.write_text('synthetic', encoding='utf-8')
            (runtime / 'runtime-manifest.json').write_text('{}', encoding='utf-8')

            dist = root / 'dist'
            artifact = dist / ('PolyScholar.app' if sys.platform == 'darwin' else 'PolyScholar')
            artifact.mkdir(parents=True)
            marker = artifact / 'previous-bundle-marker'
            marker.write_text('preserved', encoding='utf-8')
            manifest = dist / 'package-manifest.json'
            manifest.write_text(json.dumps({
                'embedded_engine_memory_configuration_verified': True,
                'fixture': 'previous-success',
            }), encoding='utf-8')
            arguments = [
                'package_desktop.py', '--dist', str(dist), '--runtime', str(runtime),
            ]

            # Only the actual orchestration and file operations run here.
            # 只执行真实检查编排与文件操作，构建、GUI 与引擎均为测试替身。
            with patch.object(package_desktop, 'ROOT', root), \
                    patch.object(sys, 'argv', arguments), \
                    patch.object(package_desktop.subprocess, 'run'), \
                    patch.object(package_desktop, 'verify_desktop'), \
                    patch.object(package_desktop, 'verify_packaged_engines',
                                 side_effect=RuntimeError('synthetic check failure')) as engine_check:
                with self.assertRaisesRegex(RuntimeError, 'synthetic check failure'):
                    package_desktop.main()

            engine_check.assert_called_once_with(artifact)
            self.assertFalse(manifest.exists())
            self.assertEqual(marker.read_text(encoding='utf-8'), 'preserved')

    def test_relocated_engine_failure_restores_bundle_without_success_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / 'SyntheticBundle'
            artifact.mkdir()
            marker = artifact / 'marker'
            marker.write_text('synthetic bundle', encoding='utf-8')
            evidence = root / 'relocation-evidence.json'
            inspected = []

            def fail_after_move(moved):
                inspected.append(moved)
                self.assertFalse(artifact.exists())
                self.assertEqual((moved / 'marker').read_text(encoding='utf-8'),
                                 'synthetic bundle')
                raise RuntimeError('synthetic check failure')

            with patch.object(relocation, 'verify_desktop'), \
                    patch.object(relocation, 'verify_packaged_engines',
                                 side_effect=fail_after_move), \
                    patch.object(relocation, 'verify') as runtime_check:
                with self.assertRaisesRegex(RuntimeError, 'synthetic check failure'):
                    relocation.PackageRelocationCheck(artifact, evidence).run()

            self.assertEqual(len(inspected), 1)
            runtime_check.assert_not_called()
            self.assertEqual(marker.read_text(encoding='utf-8'), 'synthetic bundle')
            self.assertFalse(evidence.exists())
            self.assertEqual(list(root.iterdir()), [artifact])


if __name__ == '__main__':
    unittest.main()
