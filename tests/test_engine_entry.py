# SPDX-License-Identifier: AGPL-3.0-only
"""Trusted entry protocol and output isolation; synthetic upstream only.

真实入口进程的协议/原始输出边界；替换上游，不声称实际翻译。
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from integrations import engines


ROOT = Path(__file__).resolve().parents[1]


class EngineEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        source = self.root / 'source.pdf'
        source.write_bytes(b'%PDF-1.4 synthetic entry fixture')
        self.request = engines.Request(
            'babeldoc', Path(sys.executable), source, self.root / 'output',
            'https://api.example.test', 'synthetic-model',
            allow_document_upload=True, allow_asset_download=True,
        )
        self.data = engines.request_input(self.request, 'synthetic-secret-never-echo')
        self.request.output.mkdir()

    def entry(self, mode, data=None, extra_arguments=()):
        script = self.root / ('entry-' + mode + '.py')
        script.write_text(
            'import os,sys\n'
            f'sys.path.insert(0,{str(ROOT)!r})\n'
            'from integrations import engine_entry\n'
            'engine_entry.engines.check_version=lambda *_:None\n'
            'def synthetic_upstream(req,key):\n'
            '    print(key)\n'
            '    sys.stderr.write(key)\n'
            '    os.write(1,key.encode())\n'
            '    os.write(2,key.encode())\n'
            '    assert req.output.is_dir()\n'
            '    assert req.model=="synthetic-model"\n'
            '    (req.output/"called").write_text("synthetic-entry-reached")\n'
            + ('    raise ValueError(key)\n' if mode == 'error'
               else '    return ' + ('7' if mode == 'nonzero' else '0') + '\n')
            + 'engine_entry.run=synthetic_upstream\n'
            'raise SystemExit(engine_entry.main())\n',
            encoding='utf-8',
        )
        result = subprocess.run(
            [sys.executable, '-I', str(script), *extra_arguments],
            input=self.data if data is None else data,
            capture_output=True, timeout=15, cwd=self.root,
            env=engines.limited_environment(),
        )
        self.assertEqual(result.stdout + result.stderr, b'')
        return result

    def test_actual_entry_delivers_request_and_suppresses_upstream_prints(self):
        self.assertEqual(self.entry('success').returncode, 0)
        self.assertEqual((self.request.output / 'called').read_text(), 'synthetic-entry-reached')

    def test_upstream_failure_is_nonzero_without_raw_exception(self):
        for mode, code in [('error', 1), ('nonzero', 7)]:
            with self.subTest(mode=mode):
                self.assertEqual(self.entry(mode).returncode, code)

    def test_untrusted_options_and_invalid_input_never_reach_upstream(self):
        for data, arguments in [
            (self.data, ('--mcp',)), (b'{}', ()), (b'X' * (1024 * 1024 + 1), ()),
        ]:
            with self.subTest(bytes=len(data), extra=bool(arguments)):
                self.assertNotEqual(self.entry('rejected', data, arguments).returncode, 0)
                self.assertFalse((self.request.output / 'called').exists())


if __name__ == '__main__':
    unittest.main()
