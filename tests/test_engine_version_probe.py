# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic CLI and lifecycle checks, without importing upstream engines.

使用临时 CLI 夹具检查语义与清理，不代表真实引擎或安装包验收。
"""
import json
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

    def run_cli(self, engine, code, *, fallback_socketpair=False):
        package, module = (
            ('babeldoc', 'main') if engine == 'babeldoc' else ('pdf2zh', 'pdf2zh')
        )
        modules = self.root / engine / 'modules'
        (modules / package).mkdir(parents=True)
        (modules / package / '__init__.py').write_text('', encoding='utf-8')
        (modules / package / (module + '.py')).write_text(code, encoding='utf-8')
        home = self.root / engine / 'disposable-home'
        environment = {
            name: os.environ[name]
            for name in ('HOME', 'USERPROFILE', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')
            if name in os.environ
        }
        environment.update({
            'PATH': os.defpath,
            'SYNTHETIC_HOME_STATE': json.dumps({
                name: os.environ.get(name) for name in ('HOME', 'USERPROFILE')
            }),
            'SYNTHETIC_DISPOSABLE_HOME': str(home),
        })
        # Only this test bootstrap adds the synthetic package; production runs
        # the bundled interpreter's installed upstream module unchanged.
        # 仅测试引导加入合成模块路径，生产检查执行自带解释器的原上游模块。
        bootstrap = 'import runpy,socket,sys\n'
        if fallback_socketpair:
            # Exercise the stdlib Windows wakeup implementation on every host.
            # 所有主机都强制使用 Windows 的标准库本地 TCP 唤醒实现。
            bootstrap += (
                'pairs=[]\n'
                'def fallback(*args,**kwargs):\n'
                '    pair=socket._fallback_socketpair(*args,**kwargs)\n'
                '    pairs.append(pair)\n'
                '    return pair\n'
                'socket.socketpair=fallback\n'
            )
        bootstrap += (
            'sys.path.insert(0,sys.argv[1])\n'
            'sys.argv=sys.argv[2:]\n'
        )
        if fallback_socketpair:
            bootstrap += (
                'try:\n'
                '    runpy.run_path(sys.argv[0],run_name="__main__")\n'
                'finally:\n'
                '    assert len(pairs)==1, "Expected one native wakeup pair"\n'
                '    assert all(sock.fileno()==-1 for pair in pairs for sock in pair), '
                '"Native wakeup pair was not closed"\n'
            )
        else:
            bootstrap += 'runpy.run_path(sys.argv[0],run_name="__main__")\n'
        result = subprocess.run(
            [sys.executable, '-I', '-c', bootstrap, str(modules),
             str(HELPER), engine, str(home)],
            cwd=self.root, env=environment, capture_output=True, timeout=15,
        )
        return result, home

    def test_actual_module_cli_stdout_and_home_discovery_are_preserved(self):
        code = '''
import json
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
for name, original in json.loads(os.environ['SYNTHETIC_HOME_STATE']).items():
    assert os.environ.get(name) == original
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
        # An early parser exit never claims the prebuilt loop; it must still close.
        # 参数解析提前退出不会领取预建循环，仍必须关闭底层唤醒管道。
        result, _ = self.run_cli(
            'babeldoc', 'print("synthetic version failure")\nraise SystemExit(7)\n',
            fallback_socketpair=True,
        )
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout.splitlines(), [b'synthetic version failure'])
        self.assertEqual(result.stderr, b'')

    def test_real_asyncio_run_uses_one_native_wakeup_pair_and_keeps_network_denied(self):
        result, _ = self.run_cli('babeldoc', '''
import asyncio
import socket

async def main():
    await asyncio.sleep(0)
    try:
        asyncio.events.new_event_loop()
    except RuntimeError as error:
        assert str(error) == 'The version event loop can only be used once'
    else:
        raise AssertionError('A second event loop was accepted')
    try:
        socket.getaddrinfo('synthetic.invalid', 443)
    except RuntimeError as error:
        assert str(error) == 'Network is disabled in the version fixture'
    else:
        raise AssertionError('Unexpected DNS lookup')
    with socket.socket() as outgoing:
        try:
            outgoing.connect(('127.0.0.1', 9))
        except RuntimeError as error:
            assert str(error) == 'Network is disabled in the version fixture'
        else:
            raise AssertionError('Unexpected local connection')
    with socket.socket() as incoming:
        try:
            incoming.bind(('127.0.0.1', 0))
        except OSError as error:
            assert str(error) == 'Passive bind is disabled in the version fixture'
        else:
            raise AssertionError('Unexpected local listener')
    print('synthetic async version')

asyncio.run(main())
''', fallback_socketpair=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [b'synthetic async version'])
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
            name: os.environ[name] for name in ('HOME', 'USERPROFILE')
            if name in os.environ
        }
        environment.update({
            'PATH': 'synthetic-original-path',
            'OPENAI_API_KEY': 'synthetic-secret',
            'PYTHONPATH': 'synthetic-foreign-imports',
            'XDG_CACHE_HOME': 'synthetic-foreign-cache',
        })

        def execute(command, **kwargs):
            seen.append((command, kwargs))
            if len(seen) == 2:
                folder = Path(kwargs['cwd'])
                self.assertEqual(folder.parent, work)
                self.assertTrue(folder.is_dir())
                self.assertEqual(Path(command[-1]), folder / 'home')
                self.assertEqual(command[1:4], ['-I', str(HELPER), 'babeldoc'])
                expected_keys = {'PATH'} | ({'HOME', 'USERPROFILE'} & environment.keys())
                self.assertEqual(set(kwargs['env']), expected_keys)
                for name in ('HOME', 'USERPROFILE'):
                    self.assertEqual(kwargs['env'].get(name), os.environ.get(name))
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
