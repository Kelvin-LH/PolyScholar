# SPDX-License-Identifier: AGPL-3.0-only
"""Probe target/origin checks without upstream execution.

包验证夹具必须检查指定组件，不允许静默回退源码；不执行翻译/API。
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import smoke_engines

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/engine_memory_probe.py'
COMPONENTS = (
    '__init__.py', 'engine_entry.py', 'engines.py', 'job_worker.py',
    'managed_process.py', 'process_gate.py', 'windows_job.py',
)


class EngineProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='polyscholar-probe-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.integrations = self.root / '迁移 检查' / 'integrations'
        self.integrations.mkdir(parents=True)
        for filename in COMPONENTS:
            shutil.copy2(ROOT / 'integrations' / filename, self.integrations / filename)

    def helper(self, directory=None):
        result = subprocess.run(
            [sys.executable, '-I', str(HELPER), 'babeldoc', str(self.root),
             str(directory if directory is not None else self.integrations)],
            cwd=self.root, capture_output=True, timeout=15,
        )
        self.assertEqual(result.stdout + result.stderr, b'')
        return result

    def test_copied_components_with_spaces_and_chinese_are_loaded_from_target(self):
        # A distinguishable copy proves that repository modules were not used.
        # 修改副本标记，确保结果不能由仓库模块冒充。
        with (self.integrations / 'engine_entry.py').open('a', encoding='utf-8') as stream:
            stream.write('\nSYNTHETIC_PACKAGED_MARKER = "selected-copy"\n')
        result = subprocess.run(
            [sys.executable, '-I', '-c',
             'import runpy,sys\n'
             'probe=runpy.run_path(sys.argv[1])\n'
             'entry,engines=probe["load_integrations"](sys.argv[2])\n'
             'assert entry.SYNTHETIC_PACKAGED_MARKER=="selected-copy"\n'
             'probe["assert_component_origins"](sys.argv[2])\n',
             str(HELPER), str(self.integrations)],
            cwd=self.root, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, b'')

    def test_missing_directory_never_falls_back_to_repository(self):
        self.assertNotEqual(self.helper(self.root / 'missing').returncode, 0)

    def test_target_directory_cannot_link_to_repository(self):
        redirected = self.root / 'redirected-integrations'
        try:
            redirected.symlink_to(ROOT / 'integrations', target_is_directory=True)
        except OSError:
            self.skipTest('Directory symlinks are unavailable on this host')
        self.assertNotEqual(self.helper(redirected).returncode, 0)

    def test_each_missing_component_is_rejected_before_upstream_import(self):
        for filename in COMPONENTS:
            with self.subTest(component=filename):
                target = self.integrations / filename
                target.unlink()
                try:
                    self.assertNotEqual(self.helper().returncode, 0)
                finally:
                    shutil.copy2(ROOT / 'integrations' / filename, target)

    def test_forged_component_file_origin_is_rejected(self):
        with (self.integrations / 'engines.py').open('a', encoding='utf-8') as stream:
            stream.write('\n__file__ = ' + repr(str(ROOT / 'integrations/engines.py')) + '\n')
        self.assertNotEqual(self.helper().returncode, 0)

    def test_package_search_path_cannot_redirect_to_repository(self):
        (self.integrations / '__init__.py').write_text(
            '__path__ = [' + repr(str(ROOT / 'integrations')) + ']\n', encoding='utf-8')
        self.assertNotEqual(self.helper().returncode, 0)

    def test_import_diagnostics_are_suppressed_at_python_and_fd_boundaries(self):
        (self.integrations / '__init__.py').write_text(
            'import os,sys\n'
            'print("synthetic import diagnostic")\n'
            'sys.stderr.write("synthetic import diagnostic")\n'
            'os.write(1,b"synthetic raw stdout")\n'
            'os.write(2,b"synthetic raw stderr")\n'
            'raise RuntimeError("synthetic raw import failure")\n', encoding='utf-8')
        self.assertNotEqual(self.helper().returncode, 0)

    def test_public_wrapper_passes_explicit_target_and_reports_fixed_failure(self):
        (self.integrations / 'engine_entry.py').unlink()
        calls = []
        real_run = subprocess.run

        def fixture_runtime(command, **kwargs):
            calls.append(command)
            # Use the test interpreter solely to exercise the public subprocess
            # and missing-component rejection, not a pinned upstream engine.
            # 只替换解释器以检查传参和组件拒绝，不声称检查真实上游。
            return real_run([sys.executable, *command[1:]], **kwargs)

        with patch.object(smoke_engines.subprocess, 'run', side_effect=fixture_runtime):
            with self.assertRaisesRegex(RuntimeError, '^' + smoke_engines.FAILURE + '$'):
                smoke_engines.verify_engines(self.root / 'runtime', self.integrations)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], str(self.integrations))
        suffix = 'babeldoc/python.exe' if sys.platform == 'win32' else 'babeldoc/bin/python3'
        self.assertEqual(Path(calls[0][0]), self.root / 'runtime' / suffix)


if __name__ == '__main__':
    unittest.main()
