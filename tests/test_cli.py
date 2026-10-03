# SPDX-License-Identifier: AGPL-3.0-only
"""CLI contract tests: every command through main(argv, service) on a temp library."""
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from polyscholar.cli import main

class CliBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root / 'data')
        self.addCleanup(self.service.close)
        self.source = self.root / 'paper.pdf'
        self.source.write_bytes(b'%PDF-1.7 cli sample')
        self.document = self.service.import_pdf(self.source)

    def run_cli(self, *argv, data_dir=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv), service=self.service)
        return code, out.getvalue(), err.getvalue()

    def test_add_list_show_remove(self):
        code, out, _ = self.run_cli('add', str(self.source))
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli('list', '--json')
        listed = json.loads(out)
        self.assertEqual(listed[0]['title'], 'paper')
        code, out, _ = self.run_cli('show', self.document['id'][:8], '--json')
        payload = json.loads(out)
        self.assertEqual(payload['id'], self.document['id'])
        self.assertIn('scores', payload)
        code, _, _ = self.run_cli('remove', self.document['id'][:8], '--yes')
        self.assertEqual(code, 0)
        self.assertEqual(self.service.list_documents(), [])

    def test_update_fields_and_tags(self):
        code, out, _ = self.run_cli('update', self.document['id'], '--title', 'CLI 标题',
                                    '--tags', 'a,b', '--year', '2026', '--doi', '10.1/x')
        self.assertEqual(code, 0)
        stored = self.service.document(self.document['id'])
        self.assertEqual(stored['title'], 'CLI 标题')
        self.assertEqual(stored['tags'], ['a', 'b'])
        self.assertEqual(stored['doi'], '10.1/x')

    def test_update_without_fields_fails(self):
        code, _, err = self.run_cli('update', self.document['id'])
        self.assertEqual(code, 1)
        self.assertIn('字段', err)

    def test_collection_lifecycle(self):
        code, _, _ = self.run_cli('collection', 'add', '自动驾驶')
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli('collection', 'list', '--json')
        collections = json.loads(out)
        self.assertEqual(collections[0]['name'], '自动驾驶')
        code, _, _ = self.run_cli('collection', 'attach', self.document['id'][:8], '--collection', '自动驾驶')
        self.assertEqual(code, 0)
        members = self.service.document_collections(self.document['id'])
        self.assertEqual(len(members), 1)
        code, _, _ = self.run_cli('collection', 'detach', self.document['id'][:8], '--collection', '自动驾驶')
        self.assertEqual(code, 0)
        self.assertEqual(self.service.document_collections(self.document['id']), [])
        code, _, _ = self.run_cli('collection', 'remove', '自动驾驶')
        self.assertEqual(code, 0)
        self.assertEqual(self.service.list_collections(), [])

    def test_collection_name_not_unique_lists_candidates(self):
        self.service.create_collection('A', None)
        parent = self.service.create_collection('P', None)
        self.service.create_collection('A', parent['id'])
        code, _, err = self.run_cli('collection', 'attach', self.document['id'][:8], '--collection', 'A')
        self.assertEqual(code, 1)
        self.assertIn('不唯一', err)

    def test_text_requires_parse(self):
        code, out, _ = self.run_cli('text', self.document['id'])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), '')

    def test_score_set_show_validation(self):
        detail_file = self.root / 'report.json'
        detail_file.write_text(json.dumps(dict(dimensions=[], sub_scores=[]), ensure_ascii=False), encoding='utf-8')
        code, out, _ = self.run_cli('score', 'set', self.document['id'], '--kind', 'paper',
                                    '--score', '87.5', '--rationale', '创新充分', '--detail-file', str(detail_file))
        self.assertEqual(code, 0)
        # 回执精简:写入成功只报结果,不回显整个明细 JSON(此前是最大 token 浪费点)。
        receipt = json.loads(out)
        self.assertEqual(receipt['kind'], 'paper')
        self.assertEqual(receipt['score'], 87.5)
        self.assertGreater(receipt['detail_bytes'], 0)
        self.assertNotIn('dimensions', out)
        shown = json.loads(self.run_cli('score', 'show', self.document['id'], '--json')[1])
        self.assertEqual(shown['paper']['score'], 87.5)
        self.assertEqual(shown['paper']['detail']['dimensions'], [])
        for bad in (['--score', '101'], ['--score', '-1'], []):
            code, _, err = self.run_cli('score', 'set', self.document['id'], '--kind', 'paper', *bad,
                                        '--rationale', 'x')
            self.assertEqual(code, 1)
        code, _, _ = self.run_cli('score', 'set', self.document['id'], '--kind', 'summary',
                                  '--rationale', '概览文本', '--detail-file', str(detail_file))
        self.assertEqual(code, 0)
        scores = self.service.document_scores(self.document['id'])
        self.assertIsNone(scores['summary']['score'])
        self.assertIn('概览', scores['summary']['rationale'])

    def test_translate_mocked_runs_to_artifact(self):
        self.service.update_document(self.document['id'], {'url': 'https://arxiv.org/abs/2312.04567'})
        self.service.save_settings({'model': 'test-model'})
        page = '<html><body><p class="ps-zh">中文译文</p></body></html>'
        with patch('polyscholar.service.html_translate.translate_paper',
                   return_value=(page, 1, 1)):
            code, out, err = self.run_cli('translate', self.document['id'])
        self.assertEqual(code, 0, out + err)
        self.assertIn('translated.html', out)
        artifact = Path(out.strip().splitlines()[-1])
        self.assertEqual(artifact.read_text(encoding='utf-8'), page)
        code, out, _ = self.run_cli('export-translation', self.document['id'], '-o', str(self.root / 'export.html'))
        self.assertEqual(code, 0)
        self.assertEqual((self.root / 'export.html').read_text(encoding='utf-8'), page)

    def test_translate_rejects_non_arxiv(self):
        self.service.save_settings({'model': 'test-model'})
        code, _, err = self.run_cli('translate', self.document['id'])
        self.assertEqual(code, 1)
        self.assertIn('arXiv', err)

    def test_jobs_lists_states(self):
        code, out, _ = self.run_cli('jobs', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])

from polyscholar.service import LocalService
if __name__ == '__main__':
    unittest.main()
