# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic CLI and lifecycle checks, without importing upstream engines.

使用临时 CLI 夹具检查语义与清理，不代表真实引擎或安装包验收。
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import prepare_runtime


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/engine_version_probe.py'


class VersionProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='polyscholar-version-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def run_cli(self, engine, code):
        package, module = (
            ('babeldoc', 'main') if engine == 'babeldoc' else ('pdf2zh', 'pdf2zh')
        )
        modules = self.root / engine / 'modules'
        (modules / package).mkdir(parents=True)
        (modules / package / '__init__.py').write_text('', encoding='utf-8')
        (modules / package / (module + '.py')).write_text(code, encoding='utf-8')
        home = self.root / engine / 'disposable-home'
        original_home = self.root / engine / 'original-home'
        original_home.mkdir()
        sentinel = original_home / 'sentinel'
        sentinel.write_text('original configuration', encoding='utf-8')
        environment = {
            name: os.environ[name]
            for name in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')
            if name in os.environ
        }
        environment.update({
            'PATH': os.defpath,
            'HOME': str(original_home),
            'USERPROFILE': str(original_home),
            'SYNTHETIC_ORIGINAL_HOME': str(original_home),
            'SYNTHETIC_DISPOSABLE_HOME': str(home),
        })
        # Only this test bootstrap adds the synthetic package; production runs
        # the bundled interpreter's installed upstream module unchanged.
        # 仅测试引导加入合成模块路径，生产检查执行自带解释器的原上游模块。
        bootstrap = (
            'import runpy,sys\n'
            'sys.path.insert(0,sys.argv[1])\n'
            'sys.argv=sys.argv[2:]\n'
            'runpy.run_path(sys.argv[0],run_name="__main__")\n'
        )
        result = subprocess.run(
            [sys.executable, '-I', '-c', bootstrap, str(modules),
             str(HELPER), engine, str(home)],
            cwd=self.root, env=environment, capture_output=True, timeout=15,
        )
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'original configuration')
        self.assertEqual(list(original_home.iterdir()), [sentinel])
        return result, home

    def test_actual_module_cli_stdout_and_home_discovery_are_preserved(self):
        code = '''
import os
from pathlib import Path
import sys
assert __name__ == '__main__'
assert sys.argv[1:] == ['--version']
home = Path(os.environ['SYNTHETIC_DISPOSABLE_HOME'])
assert Path.home() == home
assert os.path.expanduser('~') == str(home)
assert os.path.expanduser('~/cache') == str(home) + '/cache'
assert os.path.expanduser(b'~/cache') == os.fsencode(str(home) + '/cache')
assert os.path.expanduser('relative/path') == 'relative/path'
assert os.environ['HOME'] == os.environ['SYNTHETIC_ORIGINAL_HOME']
assert os.environ['USERPROFILE'] == os.environ['SYNTHETIC_ORIGINAL_HOME']
try:
    os.path.expanduser('~foreign/cache')
except RuntimeError:
    pass
else:
    raise AssertionError('Foreign home lookup was allowed')
(home / 'synthetic-cache').write_text('version import cache')
print('synthetic version 1.2.3')
raise SystemExit(0)
'''
        for engine in ('babeldoc', 'pdfmathtranslate'):
            with self.subTest(engine=engine):
                result, home = self.run_cli(engine, code)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [b'synthetic version 1.2.3'])
                self.assertEqual(result.stderr, b'')
                self.assertEqual((home / 'synthetic-cache').read_text(), 'version import cache')

    def test_nonzero_cli_status_is_not_changed_to_success(self):
        result, _ = self.run_cli('babeldoc',
                                 'print("synthetic version failure")\nraise SystemExit(7)\n')
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout.splitlines(), [b'synthetic version failure'])
        self.assertEqual(result.stderr, b'')

    def test_network_name_lookup_is_rejected_before_io(self):
        result, _ = self.run_cli('babeldoc', '''
import socket
try:
    socket.getaddrinfo('synthetic.invalid', 443)
except RuntimeError:
    print('synthetic network blocked')
else:
    raise AssertionError('Unexpected network lookup')
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [b'synthetic network blocked'])
        self.assertEqual(result.stderr, b'')

    def test_version_check_filters_environment_and_cleans_its_work_directory(self):
        work = self.root / 'work'
        work.mkdir()
        runtime = self.root / 'runtime'
        seen = []
        environment = {
            'HOME': 'synthetic-original-home',
            'USERPROFILE': 'synthetic-original-profile',
            'PATH': 'synthetic-original-path',
            'OPENAI_API_KEY': 'synthetic-secret',
            'PYTHONPATH': 'synthetic-foreign-imports',
            'XDG_CACHE_HOME': 'synthetic-foreign-cache',
        }

        def execute(command, **kwargs):
            seen.append((command, kwargs))
            if len(seen) == 2:
                folder = Path(kwargs['cwd'])
                self.assertEqual(folder.parent, work)
                self.assertTrue(folder.is_dir())
                self.assertEqual(Path(command[-1]), folder / 'home')
                self.assertEqual(command[1:4], ['-I', str(HELPER), 'babeldoc'])
                self.assertEqual(set(kwargs['env']), {'HOME', 'USERPROFILE', 'PATH'})
                self.assertEqual(kwargs['env']['HOME'], environment['HOME'])
                self.assertEqual(kwargs['env']['USERPROFILE'], environment['USERPROFILE'])
                (folder / 'synthetic-cache').write_text('temporary cache')
            return subprocess.CompletedProcess(command, 0)

        with patch.object(prepare_runtime, 'clean_environment', return_value=environment), \
                patch.object(prepare_runtime.subprocess, 'run', side_effect=execute):
            prepare_runtime.verify(runtime, 'babeldoc', work)

        self.assertEqual(len(seen), 3)
        self.assertEqual(Path(seen[1][0][0]), prepare_runtime.binary(runtime).absolute())
        self.assertEqual(seen[2][0][1:], ['-I', '-m', 'pip', 'check'])
        self.assertFalse(Path(seen[1][1]['cwd']).exists())
        self.assertEqual(list(work.iterdir()), [])

    def test_failed_or_timed_out_version_check_cleans_before_propagating(self):
        for failure in ('exit', 'timeout'):
            with self.subTest(failure=failure):
                work = self.root / failure
                work.mkdir()
                calls = []

                def execute(command, **kwargs):
                    calls.append((command, kwargs))
                    if len(calls) == 2:
                        (Path(kwargs['cwd']) / 'synthetic-cache').write_text('temporary cache')
                        if failure == 'exit':
                            raise subprocess.CalledProcessError(7, command)
                        raise subprocess.TimeoutExpired(command, 90)
                    return subprocess.CompletedProcess(command, 0)

                expected = (subprocess.CalledProcessError if failure == 'exit'
                            else subprocess.TimeoutExpired)
                with patch.object(prepare_runtime.subprocess, 'run', side_effect=execute):
                    with self.assertRaises(expected):
                        prepare_runtime.verify(self.root / 'runtime', 'babeldoc', work)
                self.assertEqual(len(calls), 2)
                self.assertFalse(Path(calls[1][1]['cwd']).exists())
                self.assertEqual(list(work.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
