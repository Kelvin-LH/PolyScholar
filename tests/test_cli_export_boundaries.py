# SPDX-License-Identifier: AGPL-3.0-only
"""Score exports retain protected desktop paths and atomic publication.
评分导出保留桌面受保护路径及原子发布边界。
"""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from polyscholar.cli import main
from polyscholar.service import LocalService


class CliExportBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.resources = self.root / 'resources'
        self.resources.mkdir()
        self.service = LocalService(self.root / 'library', resources_dir=self.resources)
        self.addCleanup(self.service.close)
        self.original = self.root / 'original.pdf'
        self.original.write_bytes(b'%PDF-1.7 retained original')
        self.document = self.service.import_pdf(self.original)
        self.reports = {}
        for kind, count in (('summary', 2), ('paper', 3)):
            paths = []
            for index in range(count):
                if kind == 'summary':
                    report = {key: [{'text': 'fixture report', 'evidence': 'page 1'}]
                              for key in ('problem', 'method', 'results', 'limitations')}
                else:
                    report = dict(rubric_version='1.0.0', manifest={'paper_sha256':'fixture'}, total=80,
                        dimensions=[dict(key=key, max=maximum, score=maximum * .8, rationale='fixture')
                            for key, maximum in [('novelty',25),('innovation_degree',20),
                                ('effectiveness',40),('rigor',10),('clarity',5)]],
                        strengths=[{'point':'fixture', 'evidence':'page 1'}],
                        weaknesses=[{'point':'fixture', 'evidence':'page 2'}])
                path = self.root / f'{kind}-{index}.json'
                path.write_text(json.dumps(report), encoding='utf-8')
                paths.append(str(path))
            self.reports[kind] = paths

    def run_cli(self, kind, destination):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(['score','aggregate','--kind',kind,'--reports',*self.reports[kind],
                         '--out',str(destination),'--json'], service=self.service)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_both_aggregates_reject_original_managed_sqlite_and_runtime_collisions(self):
        resource = self.resources / 'component.py'
        resource.write_text('retained runtime fixture', encoding='utf-8')
        managed = self.service.store.object_path(self.document)
        database = self.service.store.root / 'library.sqlite3'
        for kind in self.reports:
            for destination in (self.original, managed, database, resource):
                with self.subTest(kind=kind, destination=destination.name):
                    before = destination.read_bytes()
                    code, _, message = self.run_cli(kind, destination)
                    self.assertEqual(code, 1)
                    self.assertIn('不能覆盖', message)
                    self.assertEqual(destination.read_bytes(), before)
        self.assertEqual(self.service.store.document(self.document['id'])['title'], 'original')

    def test_successful_reports_use_atomic_json_publication(self):
        real_replace = os.replace
        for kind in self.reports:
            destination = self.root / f'{kind}-assembled.json'
            destination.write_text('old result', encoding='utf-8')
            with patch('polyscholar.exports.os.replace', wraps=real_replace) as replace:
                code, stdout, stderr = self.run_cli(kind, destination)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)['out'], str(destination.resolve()))
            self.assertTrue(isinstance(json.loads(destination.read_text(encoding='utf-8')), dict))
            replace.assert_called_once()
            temporary, published = replace.call_args.args
            self.assertEqual(Path(temporary).parent, destination.parent.resolve())
            self.assertEqual(Path(published), destination.resolve())
            self.assertFalse(Path(temporary).exists())

    def test_symbolic_link_destination_does_not_bypass_protected_writer(self):
        destination = self.root / 'symbolic-report.json'
        actual = self.root / 'actual-report.json'
        actual.write_text('{"retained":true}', encoding='utf-8')
        try:
            destination.symlink_to(actual)
        except OSError:
            self.skipTest('Symbolic links are unavailable on this platform')
        code, _, _ = self.run_cli('summary', destination)
        self.assertEqual(code, 1)
        self.assertEqual(actual.read_text(encoding='utf-8'), '{"retained":true}')

    def test_failed_atomic_replace_preserves_previous_json(self):
        destination = self.root / 'retained.json'
        destination.write_text('{"retained":true}', encoding='utf-8')
        with patch('polyscholar.exports.os.replace', side_effect=OSError('controlled write failure')):
            code, _, _ = self.run_cli('summary', destination)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(destination.read_text(encoding='utf-8')), {'retained':True})
        self.assertEqual(list(self.root.glob('.polyscholar-export-*')), [])


if __name__ == '__main__':
    unittest.main()
