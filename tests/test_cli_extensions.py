# SPDX-License-Identifier: AGPL-3.0-only
"""Regression tests for the 2026-10 agent-feedback CLI additions; no real network."""
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from polyscholar.cli import main
from polyscholar.arxiv import NetworkError
from polyscholar.instance import LibraryBusy, LibraryLock


def paper_report(rechecked=True, totals=(70, 78, 84)):
    dimensions = [
        dict(key='novelty', name='创新点', max=25, score=20, rationale='r'),
        dict(key='innovation_degree', name='创新程度', max=20, score=15, rationale='r'),
        dict(key='effectiveness', name='实际效果', max=40, score=30, rationale='r'),
        dict(key='rigor', name='方法严谨性', max=10, score=8, rationale='r'),
        dict(key='clarity', name='清晰度', max=5, score=5, rationale='r'),
    ]
    return dict(rubric_version='1.0.0', total=78, dimensions=dimensions,
                strengths=[dict(point='p', evidence='e')],
                weaknesses=[dict(point='q', evidence='f')],
                sub_scores=[dict(agent_id=letter, total=total) for letter, total in
                            zip('ABC', totals)],
                aggregation=dict(method='median', spread=max(totals) - min(totals),
                                 rechecked=rechecked, rounds=1,
                                 unresolved_disagreement=False))


def confidence_report():
    dimensions = [
        dict(key='evidence', name='证据强度', max=40, score=30, rationale='r'),
        dict(key='consistency', name='内部一致性', max=25, score=20, rationale='r'),
        dict(key='traceability', name='来源可溯源性', max=15, score=10, rationale='r'),
        dict(key='plausibility', name='结果合理性', max=20, score=15, rationale='r'),
    ]
    return dict(rubric_version='1.0.0', total=75, dimensions=dimensions,
                red_flags=[], verdict='较可信',
                sub_scores=[dict(agent_id=letter, total=total)
                            for letter, total in zip('ABC', (70, 75, 82))],
                aggregation=dict(method='median', spread=12, rechecked=False, rounds=0,
                                 unresolved_disagreement=False))


class CliExtensionBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root / 'data')
        self.addCleanup(self.service.close)
        self.source = self.root / 'paper.pdf'
        self.source.write_bytes(b'%PDF-1.7 cli extension sample')
        self.document = self.service.import_pdf(self.source)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv), service=self.service)
        return code, out.getvalue(), err.getvalue()

    def write(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return str(path)


class RubricCommandTests(CliExtensionBase):
    def test_list_reports_version_and_hash(self):
        code, out, _ = self.run_cli('rubric', 'list', '--json')
        self.assertEqual(code, 0)
        rows = json.loads(out)
        self.assertEqual([row['kind'] for row in rows], ['paper', 'confidence'])
        for row in rows:
            self.assertEqual(len(row['sha256']), 64)
            self.assertGreater(row['bytes'], 1000)
            self.assertTrue(row['version'])
            self.assertIn('rubrics', row['path'])

    def test_show_returns_full_text(self):
        code, out, _ = self.run_cli('rubric', 'show', 'paper', '--json')
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn('论文评分量化打分文档', payload['text'])
        self.assertIn('三子代理盲评流程', payload['text'])

    def test_show_without_kind_fails(self):
        code, _, err = self.run_cli('rubric', 'show')
        self.assertEqual(code, 1)
        self.assertIn('paper', err)


class ScoreValidateTests(CliExtensionBase):
    def test_valid_paper_report_passes(self):
        path = self.write('paper.json', paper_report())
        code, out, _ = self.run_cli('score', 'validate', '--kind', 'paper', '--detail-file', path, '--json')
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads(out)['valid'])

    def test_valid_confidence_report_passes(self):
        path = self.write('conf.json', confidence_report())
        code, out, _ = self.run_cli('score', 'validate', '--kind', 'confidence', '--detail-file', path, '--json')
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads(out)['valid'])

    def test_dimension_sum_mismatch_is_rejected(self):
        report = paper_report()
        report['total'] = 79
        code, out, _ = self.run_cli('score', 'validate', '--kind', 'paper',
                                    '--detail-file', self.write('bad.json', report), '--json')
        self.assertEqual(code, 1)
        self.assertIn('维度之和', out)

    def test_median_mismatch_and_unrechecked_spread_are_rejected(self):
        report = paper_report(rechecked=False, totals=(60, 70, 90))
        code, out, _ = self.run_cli('score', 'validate', '--kind', 'paper',
                                    '--detail-file', self.write('bad2.json', report), '--json')
        self.assertEqual(code, 1)
        joined = out
        self.assertIn('中位数', joined)
        self.assertIn('复核', joined)

    def test_bad_dimension_key_is_rejected(self):
        report = paper_report()
        report['dimensions'][0]['key'] = 'style'
        code, out, _ = self.run_cli('score', 'validate', '--kind', 'paper',
                                    '--detail-file', self.write('bad3.json', report), '--json')
        self.assertEqual(code, 1)
        self.assertIn('维度 key 无效', out)

    def test_missing_file_and_bad_json_are_rejected(self):
        code, _, _ = self.run_cli('score', 'validate', '--kind', 'paper',
                                  '--detail-file', str(self.root / 'nope.json'))
        self.assertEqual(code, 1)
        (self.root / 'broken.json').write_text('{not json', encoding='utf-8')
        code, _, _ = self.run_cli('score', 'validate', '--kind', 'paper',
                                  '--detail-file', str(self.root / 'broken.json'))
        self.assertEqual(code, 1)


class ScoreSetInputTests(CliExtensionBase):
    def test_rationale_file_is_stored(self):
        rationale = self.root / 'rationale.md'
        rationale.write_text('优势:方法闭合\n劣势:样本单一', encoding='utf-8')
        detail = self.write('paper.json', paper_report())
        code, _, _ = self.run_cli('score', 'set', self.document['id'], '--kind', 'paper',
                                  '--score', '78', '--rationale-file', str(rationale),
                                  '--detail-file', detail)
        self.assertEqual(code, 0)
        stored = self.service.document_scores(self.document['id'])['paper']
        self.assertIn('样本单一', stored['rationale'])
        self.assertEqual(stored['detail']['total'], 78)

    def test_oversized_rationale_file_fails(self):
        rationale = self.root / 'big.md'
        rationale.write_text('x' * (64 * 1024 + 1), encoding='utf-8')
        code, _, err = self.run_cli('score', 'set', self.document['id'], '--kind', 'paper',
                                    '--score', '1', '--rationale-file', str(rationale))
        self.assertEqual(code, 1)
        self.assertIn('64 KiB', err)

    def test_oversized_detail_file_fails(self):
        detail = self.root / 'big.json'
        detail.write_text('{"pad":"' + 'x' * (256 * 1024) + '"}', encoding='utf-8')
        code, _, err = self.run_cli('score', 'set', self.document['id'], '--kind', 'paper',
                                    '--score', '1', '--detail-file', str(detail))
        self.assertEqual(code, 1)
        self.assertIn('256 KiB', err)

    def test_detail_and_detail_file_are_mutually_exclusive(self):
        code, _, err = self.run_cli('score', 'set', self.document['id'], '--kind', 'paper',
                                    '--score', '1', '--detail', '{}',
                                    '--detail-file', self.write('p.json', paper_report()))
        self.assertEqual(code, 1)
        self.assertIn('只能同时提供一个', err)


class AddMetaQuietTests(CliExtensionBase):
    def test_meta_patches_imported_document(self):
        meta = self.write('meta.json', dict(title='元数据标题', year='2024',
                                            tags=['audit'], doi='10.1/x'))
        code, _, _ = self.run_cli('add', str(self.source), '--meta', meta)
        self.assertEqual(code, 0)
        stored = self.service.document(self.document['id'])
        self.assertEqual(stored['title'], '元数据标题')
        self.assertEqual(stored['year'], '2024')
        self.assertEqual(stored['tags'], ['audit'])

    def test_meta_with_unknown_field_fails(self):
        meta = self.write('bad-meta.json', dict(claim='not allowed'))
        code, _, err = self.run_cli('add', str(self.source), '--meta', meta)
        self.assertEqual(code, 1)
        self.assertIn('meta', err)

    def test_quiet_add_returns_id_and_title_only(self):
        meta = self.write('meta2.json', dict(title='安静导入'))
        code, out, _ = self.run_cli('add', str(self.source), '--meta', meta, '--quiet', '--json')
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(sorted(payload), ['id', 'title'])

    def test_quiet_list_trims_records(self):
        code, out, _ = self.run_cli('list', '--json', '--quiet')
        self.assertEqual(code, 0)
        listed = json.loads(out)
        self.assertEqual(sorted(listed[0]), ['id', 'scores', 'title', 'year'])


class StatusAndExitCodeTests(CliExtensionBase):
    def test_status_with_injected_service_reports_self_lock(self):
        self.service.set_scores(self.document['id'], [dict(kind='paper', score=78.0,
                                                           rationale='r', detail='{}')])
        code, out, _ = self.run_cli('status', '--json')
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report['lock'], 'self(本进程持有)')
        self.assertGreaterEqual(report['documents'], 1)
        self.assertIn('paper', report['scores'])

    def test_busy_library_exit_code_2(self):
        held = self.root / 'held'
        held.mkdir()
        holder = LibraryLock(held)
        self.addCleanup(holder.close)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['--data-dir', str(held), 'list'])
        self.assertEqual(code, 2)

    def test_network_error_exit_code_3(self):
        with patch('polyscholar.cli.arxiv_client.fetch_metadata',
                   side_effect=NetworkError('无法连接 arXiv')):
            code, _, err = self.run_cli('add', '--arxiv', '2401.04081')
        self.assertEqual(code, 3)
        self.assertIn('arXiv', err)

    def test_integrity_error_returns_clean_message(self):
        import sqlite3
        with patch.object(self.service, 'set_scores',
                          side_effect=sqlite3.IntegrityError('CHECK constraint failed: kind')):
            code, _, err = self.run_cli('score', 'set', self.document['id'], '--kind', 'paper',
                                        '--score', '1', '--detail', '{}')
        self.assertEqual(code, 1)
        self.assertIn('约束', err)
        self.assertNotIn('Traceback', err)

    def test_library_busy_is_value_error(self):
        self.assertTrue(issubclass(LibraryBusy, ValueError))


class ScoreAggregateTests(CliExtensionBase):
    def agent_report(self, total=78, spread_totals=(72, 78, 82), manifest=None):
        # Dimension scores scale with total so every fixture passes the sum check.
        dimensions = [
            dict(key='novelty', max=25, score=total * 0.25, rationale='r'),
            dict(key='innovation_degree', max=20, score=total * 0.20, rationale='r'),
            dict(key='effectiveness', max=40, score=total * 0.40, rationale='r'),
            dict(key='rigor', max=10, score=total * 0.10, rationale='r'),
            dict(key='clarity', max=5, score=total * 0.05, rationale='r'),
        ]
        return dict(rubric_version='1.0.0', manifest=manifest or dict(paper_sha256='x'),
                    total=total, dimensions=dimensions,
                    strengths=[dict(point='p', evidence='e')],
                    weaknesses=[dict(point='q', evidence='f')])

    def write_reports(self, totals=(72, 78, 82), manifest=None):
        paths = []
        for slot, total in zip('ABC', totals):
            paths.append(self.write('%s.json' % slot, self.agent_report(total=total, manifest=manifest)))
        return paths

    def test_aggregate_assembles_median_report(self):
        paths = self.write_reports()
        out = str(self.root / 'assembled.json')
        code, text, _ = self.run_cli('score', 'aggregate', '--kind', 'paper',
                                     '--reports', *paths, '--out', out, '--json')
        self.assertEqual(code, 0, text)
        payload = json.loads(text)
        self.assertEqual(payload['total'], 78)
        self.assertEqual(payload['selected_agent_id'], 'B')
        assembled = json.loads(Path(out).read_text(encoding='utf-8'))
        self.assertEqual(assembled['total'], 78)
        self.assertEqual([sub['agent_id'] for sub in assembled['sub_scores']], ['A', 'B', 'C'])
        self.assertIn('original_output', assembled['sub_scores'][0])
        self.assertFalse(assembled['aggregation']['rechecked'])
        self.assertEqual(assembled['aggregation']['rounds'], 0)  # rubric: 未复核=0

    def test_aggregate_apply_writes_score(self):
        paths = self.write_reports()
        code, text, _ = self.run_cli('score', 'aggregate', '--kind', 'paper',
                                     '--reports', *paths, '--apply', self.document['id'],
                                     '--rationale', '合卷入库', '--json')
        self.assertEqual(code, 0, text)
        self.assertEqual(json.loads(text)['applied'], self.document['id'])
        stored = self.service.document_scores(self.document['id'])['paper']
        self.assertEqual(stored['score'], 78.0)
        self.assertEqual(stored['detail']['total'], 78)

    def test_aggregate_demands_recheck_when_spread_exceeds(self):
        paths = self.write_reports(totals=(50, 78, 84))
        code, text, _ = self.run_cli('score', 'aggregate', '--kind', 'paper',
                                     '--reports', *paths, '--json')
        self.assertEqual(code, 0)
        payload = json.loads(text)
        self.assertTrue(payload['needs_recheck'])
        self.assertEqual(payload['threshold'], 10)
        self.assertIn('directive', payload)

    def test_aggregate_recheck_round_resolves(self):
        first = self.write_reports(totals=(50, 78, 84))
        recheck = []
        for slot, total in zip('ABC', (74, 78, 80)):
            recheck.append(self.write('R%s.json' % slot, self.agent_report(total=total)))
        code, text, _ = self.run_cli('score', 'aggregate', '--kind', 'paper',
                                     '--reports', *first, '--recheck', *recheck, '--json')
        self.assertEqual(code, 0, text)
        payload = json.loads(text)
        self.assertTrue(payload['rechecked'])
        self.assertFalse(payload['unresolved_disagreement'])
        out = str(self.root / 'r.json')
        self.run_cli('score', 'aggregate', '--kind', 'paper', '--reports', *first,
                     '--recheck', *recheck, '--out', out)
        assembled = json.loads(Path(out).read_text(encoding='utf-8'))
        self.assertEqual(assembled['aggregation']['rounds'], 1)  # rubric: 复核过=1
        self.assertIn('recheck_output', assembled['sub_scores'][0])

    def test_aggregate_unresolved_after_recheck(self):
        first = self.write_reports(totals=(40, 78, 90))
        recheck = [self.write('R%s.json' % slot, self.agent_report(total=total))
                   for slot, total in zip('ABC', (40, 78, 90))]
        code, text, _ = self.run_cli('score', 'aggregate', '--kind', 'paper',
                                     '--reports', *first, '--recheck', *recheck, '--json')
        self.assertEqual(code, 0, text)
        self.assertTrue(json.loads(text)['unresolved_disagreement'])

    def test_aggregate_rejects_manifest_mismatch(self):
        paths = [self.write('A.json', self.agent_report(manifest=dict(paper_sha256='x'))),
                 self.write('B.json', self.agent_report(manifest=dict(paper_sha256='y'))),
                 self.write('C.json', self.agent_report(manifest=dict(paper_sha256='x')))]
        code, _, err = self.run_cli('score', 'aggregate', '--kind', 'paper', '--reports', *paths)
        self.assertEqual(code, 1)
        self.assertIn('manifest', err)

    def test_aggregate_confidence_carries_verdict(self):
        def conf(total):
            return dict(rubric_version='1.0.0', manifest=dict(m='x'), total=total,
                        dimensions=[dict(key='evidence', max=40, score=min(40, total * 0.4)),
                                    dict(key='consistency', max=25, score=total * 0.25),
                                    dict(key='traceability', max=15, score=total * 0.15),
                                    dict(key='plausibility', max=20, score=total * 0.2)],
                        red_flags=[], verdict='较可信')
        paths = [self.write('%s.json' % slot, conf(total))
                 for slot, total in zip('ABC', (70, 75, 80))]
        code, text, _ = self.run_cli('score', 'aggregate', '--kind', 'confidence',
                                     '--reports', *paths, '--json')
        self.assertEqual(code, 0, text)
        self.assertEqual(json.loads(text)['total'], 75)

    def test_aggregate_rejects_invalid_agent_report(self):
        broken = self.write('BAD.json', dict(total=50))
        good = self.write_reports(totals=(70, 78, 84))
        code, text, _ = self.run_cli('score', 'aggregate', '--kind', 'paper',
                                     '--reports', broken, *good[1:], '--json')
        self.assertEqual(code, 1)
        self.assertIn('A', json.loads(text)['errors'])


class ScoreReportArchiveTests(CliExtensionBase):
    def test_import_and_read_back(self):
        path = self.write('A.json', dict(total=78, dimensions=[]))
        code, out, _ = self.run_cli('score', 'import-report', self.document['id'], '--kind', 'paper',
                                    '--agent', 'A', '--detail-file', path, '--json')
        self.assertEqual(code, 0, out)
        listing = json.loads(self.run_cli('score', 'reports', self.document['id'],
                                          '--kind', 'paper', '--json')[1])
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]['agentId'], 'A')
        self.assertEqual(len(listing[0]['sha256']), 64)
        self.assertNotIn('data', listing)
        detail = json.loads(self.run_cli('score', 'reports', self.document['id'], '--kind', 'paper',
                                         '--agent', 'A', '--json')[1])[0]
        self.assertEqual(detail['data']['total'], 78)

    def test_import_requires_agent(self):
        path = self.write('X.json', dict(total=1))
        code, _, err = self.run_cli('score', 'import-report', self.document['id'],
                                    '--kind', 'paper', '--detail-file', path)
        self.assertEqual(code, 1)
        self.assertIn('--agent', err)


class SearchAndPagesTests(CliExtensionBase):
    def _parsed_document(self, text):
        import pymupdf
        pdf_path = self.root / 'searchable.pdf'
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((60, 90), text)
        pdf.save(str(pdf_path))
        pdf.close()
        document = self.service.import_pdf(pdf_path)
        self.service.parse_document(document['id'])
        return document

    def test_search_scoped_to_document(self):
        document = self._parsed_document('Distillation teacher student framework')
        code, out, _ = self.run_cli('search', 'distillation', '--doc', document['id'][:8], '--json')
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertGreaterEqual(payload['total'], 1)
        self.assertEqual(payload['items'][0]['documentId'], document['id'])
        self.assertIn('snippet', payload['items'][0])

    def test_search_hint_when_unparsed(self):
        code, out, _ = self.run_cli('search', 'anything', '--doc', self.document['id'][:8], '--json')
        self.assertEqual(code, 0)
        self.assertIn('hint', json.loads(out))

    def test_show_pages_filters_blocks(self):
        document = self._parsed_document('Page one content')
        code, out, _ = self.run_cli('show', document['id'][:8], '--text', '--pages', '1-1', '--json')
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn('Page one content', payload['text'])
        code, _, err = self.run_cli('show', document['id'][:8], '--text', '--pages', '9-1')
        self.assertEqual(code, 1)

    def test_score_show_summary_drops_originals(self):
        detail = dict(total=78, sub_scores=[dict(agent_id='A', total=70, original_output=dict(big='payload'))],
                      aggregation=dict(method='median', spread=8, rechecked=False, rounds=1))
        self.service.set_scores(self.document['id'], [dict(kind='paper', score=78.0,
                                                           rationale='r', detail=json.dumps(detail))])
        full = json.loads(self.run_cli('score', 'show', self.document['id'], '--json')[1])
        self.assertIn('original_output', full['paper']['detail']['sub_scores'][0])
        slim = json.loads(self.run_cli('score', 'show', self.document['id'], '--summary', '--json')[1])
        self.assertNotIn('original_output', slim['paper']['detail']['sub_scores'][0])


class BatchArxivTests(CliExtensionBase):
    def test_batch_import_returns_compact_summary(self):
        first = self.service.import_pdf(self.source)
        second = self.service.import_pdf(self.source)
        entries = [dict(identifier='2401.00001'), dict(identifier='2401.00002')]
        imported = [dict(id=first['id'], title='第一篇'), dict(id=second['id'], title='第二篇')]
        with patch.object(self.service, 'arxiv_lookup', return_value=entries), \
             patch.object(self.service, 'arxiv_import', return_value=dict(imported=imported, errors=[])):
            code, out, _ = self.run_cli('add', '--arxiv', '2401.00001,2401.00002', '--quiet', '--json')
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload['imported'], [first['id'], second['id']])

    def test_single_import_keeps_full_document_output(self):
        document = self.service.import_pdf(self.source)
        entries = [dict(identifier='2401.00001')]
        with patch.object(self.service, 'arxiv_lookup', return_value=entries), \
             patch.object(self.service, 'arxiv_import',
                          return_value=dict(imported=[dict(id=document['id'], title=document['title'])], errors=[])):
            code, out, _ = self.run_cli('add', '--arxiv', '2401.00001', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['id'], document['id'])

    def test_meta_rejected_for_batch(self):
        meta = self.write('meta.json', dict(title='t'))
        entries = [dict(identifier='2401.00001'), dict(identifier='2401.00002')]
        with patch.object(self.service, 'arxiv_lookup', return_value=entries), \
             patch.object(self.service, 'arxiv_import', return_value=dict(imported=[dict(id='a', title='t')], errors=[])):
            code, _, err = self.run_cli('add', '--arxiv', 'x', '--meta', meta)
        self.assertEqual(code, 1)
        self.assertIn('--meta', err)


class SummaryAggregateTests(CliExtensionBase):
    def summary_report(self, items_per_section=1, overlap_text='Transformer backbone for planning'):
        def section(extra=None):
            items = [dict(text=overlap_text, evidence='§2')]
            if extra:
                items.append(dict(text=extra))
            return items
        return dict(problem=section(), method=section('Closed-loop evaluation on nuPlan'),
                    results=section('Improves L2 by 3 points'), limitations=section('No variance reported'))

    def test_two_agent_merge_applies_extraction(self):
        a = self.write('SA.json', self.summary_report())
        b = self.write('SB.json', self.summary_report())
        code, out, _ = self.run_cli('score', 'aggregate', '--kind', 'summary',
                                    '--reports', a, b, '--apply', self.document['id'], '--json')
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertIsNone(payload['total'])
        stored = self.service.document_scores(self.document['id'])['summary']
        self.assertIsNone(stored['score'])
        detail = stored['detail']
        self.assertEqual(detail['structure'], 'summary-1')
        self.assertEqual(detail['meta']['agents'], ['A', 'B'])
        # Every section held the same item from both agents: one merged entry, both tags.
        self.assertEqual(len(detail['problem']), 1)
        self.assertEqual(detail['problem'][0]['agents'], ['A', 'B'])
        self.assertEqual([len(detail[key]) for key in ('problem', 'method', 'results', 'limitations')],
                         [1, 2, 2, 2])
        self.assertEqual(detail['meta']['duplicates_dropped'], 7)
        self.assertIn('双代理提炼', stored['rationale'])

    def test_merge_keeps_different_wording_without_dedup(self):
        report_b = self.summary_report()
        report_b['problem'][0]['text'] = 'Transformer-based motion forecasting'  # same idea, other wording
        a = self.write('SA2.json', self.summary_report())
        b = self.write('SB2.json', report_b)
        code, out, _ = self.run_cli('score', 'aggregate', '--kind', 'summary',
                                    '--reports', a, b, '--out', str(self.root / 'sum.json'), '--json')
        self.assertEqual(code, 0, out)
        assembled = json.loads((self.root / 'sum.json').read_text(encoding='utf-8'))
        self.assertEqual(len(assembled['problem']), 2)
        self.assertEqual(assembled['meta']['duplicates_dropped'], 6)

    def test_rejects_three_reports_and_recheck(self):
        paths = [self.write('x.json', self.summary_report()) for _ in range(3)]
        code, _, err = self.run_cli('score', 'aggregate', '--kind', 'summary', '--reports', *paths)
        self.assertEqual(code, 1)
        self.assertIn('恰好两份', err)
        two = paths[:2]
        code, _, err = self.run_cli('score', 'aggregate', '--kind', 'summary',
                                    '--reports', *two, '--recheck', *two)
        self.assertEqual(code, 1)
        self.assertIn('复核', err)

    def test_rejects_contract_violations(self):
        bad = self.write('BAD.json', dict(problem=[], method=[dict(text='x')],
                                          results=[dict(text='x')], limitations=[dict(text='x')],
                                          extra='nope'))
        good = self.write('G.json', self.summary_report())
        code, out, _ = self.run_cli('score', 'aggregate', '--kind', 'summary',
                                    '--reports', bad, good, '--json')
        self.assertEqual(code, 1)
        errors = json.loads(out)['errors']['A']
        self.assertTrue(any('未知字段' in item for item in errors))
        self.assertTrue(any('解决的问题 必须是' in item for item in errors))

    def test_validate_rejects_summary_kind(self):
        path = self.write('s.json', dict(problem=[]))
        code, _, err = self.run_cli('score', 'validate', '--kind', 'summary', '--detail-file', path)
        self.assertEqual(code, 1)
        self.assertIn('aggregate', err)


from polyscholar.service import LocalService
if __name__ == '__main__':
    unittest.main()
