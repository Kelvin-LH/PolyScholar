# SPDX-License-Identifier: AGPL-3.0-only
"""Review gates with compensating item scores / 总分掩盖条目分歧的回归。"""
import copy
import json
from pathlib import Path

from polyscholar.cli import _validate_agent_report, _validate_report
from polyscholar.scoring_review import PAPER_CRITERIA
from tests.test_cli_extensions import CliExtensionBase


def report(slot, changes=None):
    changes = changes or {}
    dimensions = []
    names = ('创新点', '创新程度', '实际效果', '方法严谨性', '清晰度')
    for (key, ids), name in zip(PAPER_CRITERIA.items(), names):
        items = [dict(id=item, score=changes.get(item, 4), rationale='1–4 条通过；第 5 条未报告。',
                      evidence=[dict(source_id='paper', locator='§3, Table 1', quote='synthetic evidence',
                                     status='observed')]) for item in ids]
        dimensions.append(dict(key=key, name=name, max=len(ids) * 5,
                               score=sum(item['score'] for item in items), rationale='synthetic', criteria=items))
    return dict(rubric_version='1.1.0', agent_id=slot, model='synthetic-test-only',
                settings=dict(temperature=None, top_p=None, seed=None),
                manifest=dict(paper_ref='synthetic-v1', rubric_sha256='a' * 64, evidence_mode='empirical',
                              effect_context='method', meaningfulness_basis=['§3: matched resource comparison'],
                              inputs=[dict(source_id='paper', label='synthetic paper', sha256='b' * 64)],
                              main_claims=['synthetic claim'], comparison_set=[], primary_metrics=['error'],
                              meaningful_thresholds=[]),
                total=sum(dim['score'] for dim in dimensions), dimensions=dimensions,
                strengths=[], weaknesses=[], innovation_type='混合类型或证据不足')


class ItemReviewTests(CliExtensionBase):
    def paths(self, changes=None, prefix=''):
        changes = changes or ({}, {}, {})
        return [self.write(prefix + slot + '.json', report(slot, change))
                for slot, change in zip('ABC', changes)]

    def aggregate(self, first, reviews=None):
        out = self.root / 'aggregate.json'
        argv = ['score', 'aggregate', '--kind', 'paper', '--reports', *first]
        if reviews:
            argv += ['--recheck', *reviews]
        code, text, err = self.run_cli(*argv, '--out', str(out), '--json')
        self.assertEqual(code, 0, text + err)
        return json.loads(text), json.loads(out.read_text(encoding='utf-8')) if out.exists() else None

    def test_equal_totals_still_require_review_and_never_write(self):
        first = self.paths((dict(E1=3, E2=5), dict(E1=5, E2=3), {}))
        out = self.root / 'blocked.json'
        code, text, err = self.run_cli('score', 'aggregate', '--kind', 'paper', '--reports', *first,
                                     '--out', str(out), '--apply', self.document['id'],
                                     '--rationale', 'synthetic', '--json')
        self.assertEqual(code, 0, err)
        payload = json.loads(text)
        self.assertEqual(payload['spread'], 0)
        self.assertTrue(payload['needs_recheck'])
        self.assertEqual(payload['critical_items'], ['E1'])
        self.assertIn('effectiveness:E1', payload['disagreement'])
        self.assertFalse(out.exists())
        self.assertIsNone(self.service.document_scores(self.document['id'])['paper'])

    def test_item_threshold_is_inclusive_and_single_point_does_not_trigger(self):
        payload, assembled = self.aggregate(self.paths((dict(D1=3, D2=5), {}, {})))
        self.assertFalse(payload['rechecked'])
        self.assertEqual(assembled['aggregation']['critical_items'], [])
        self.assertEqual(_validate_report('paper', assembled), [])
        payload, _ = self.aggregate(self.paths((dict(D1=3, D2=5), dict(D1=5, D2=3), {})))
        self.assertTrue(payload['needs_recheck'])
        self.assertEqual(payload['critical_items'], ['D1', 'D2'])

    def test_persistent_item_disagreement_is_kept_even_with_zero_total_spread(self):
        changes = (dict(E1=3, E2=5), dict(E1=5, E2=3), {})
        payload, assembled = self.aggregate(self.paths(changes), self.paths(changes, 'R'))
        self.assertTrue(payload['unresolved_disagreement'])
        self.assertEqual(payload['spread'], 0)
        self.assertEqual(assembled['aggregation']['initial_critical_items'], ['E1'])
        self.assertEqual(assembled['aggregation']['critical_items'], ['E1'])
        self.assertEqual(_validate_report('paper', assembled), [])
        self.assertEqual(assembled['sub_scores'][0]['original_output']['dimensions'][2]['criteria'][0]['score'], 3)

    def test_recheck_can_resolve_without_replacing_originals(self):
        payload, assembled = self.aggregate(self.paths((dict(E1=3), dict(E1=5), {})), self.paths(prefix='R'))
        self.assertTrue(payload['rechecked'])
        self.assertFalse(payload['unresolved_disagreement'])
        self.assertEqual(assembled['aggregation']['critical_items'], [])
        self.assertEqual(assembled['sub_scores'][0]['original_output']['total'], 79)
        self.assertEqual(assembled['sub_scores'][0]['recheck_output']['total'], 80)
        self.assertEqual(_validate_report('paper', assembled), [])

    def test_forged_review_flags_and_representative_are_rejected(self):
        _, assembled = self.aggregate(self.paths())
        for mutate in (
                lambda r: r['aggregation'].update(rechecked=True, rounds=1),
                lambda r: r['aggregation'].update(initial_spread=8),
                lambda r: r['aggregation'].update(critical_items=['E1']),
                lambda r: r['aggregation'].update(selected_agent_id='B'),
                lambda r: r.update(strengths=[dict(point='invented')])):
            bad = copy.deepcopy(assembled)
            mutate(bad)
            self.assertTrue(_validate_report('paper', bad))

    def test_missing_duplicate_cross_dimension_fractional_and_bad_sum_rejected(self):
        good = report('A')
        for change in (
                lambda r: r['dimensions'][0].pop('criteria'),
                lambda r: r['dimensions'][0]['criteria'][0].update(id='N2'),
                lambda r: r['dimensions'][0]['criteria'][0].update(id='D1'),
                lambda r: r['dimensions'][0]['criteria'][0].update(score=3.5),
                lambda r: r['dimensions'][0].update(score=19),
                lambda r: r['dimensions'][0].update(score='invalid'),
                lambda r: r['dimensions'][0].update(key=['invalid']),
                lambda r: r['dimensions'][0]['criteria'][0]['evidence'][0].update(source_id=['unknown']),
                lambda r: r['dimensions'][0]['criteria'][0]['evidence'][0].update(quote=None),
                lambda r: r['manifest'].pop('effect_context')):
            bad = copy.deepcopy(good)
            change(bad)
            self.assertTrue(_validate_agent_report('paper', bad))

    def test_changed_material_or_version_in_review_is_rejected(self):
        first = self.paths((dict(E1=3), dict(E1=5), {}))
        for field, value in (('manifest', dict(changed=True)), ('rubric_version', '1.0.0')):
            reviews = self.paths(prefix='R')
            bad = report('A')
            bad[field] = value
            reviews[0] = self.write('RA.json', bad)
            code, text, err = self.run_cli('score', 'aggregate', '--kind', 'paper',
                                         '--reports', *first, '--recheck', *reviews, '--json')
            self.assertEqual(code, 1, text + err)
            self.assertIn('manifest', text + err)

    def test_method_path_does_not_invent_application_threshold(self):
        method = report('A')
        self.assertEqual(_validate_agent_report('paper', method), [])
        application = copy.deepcopy(method)
        application['manifest']['effect_context'] = 'application'
        self.assertTrue(_validate_agent_report('paper', application))
        application['manifest']['meaningful_thresholds'] = ['§2: predeclared budget <= 1 unit']
        self.assertEqual(_validate_agent_report('paper', application), [])
        method['manifest']['meaningfulness_basis'] = []
        self.assertTrue(_validate_agent_report('paper', method))
        method = report('A', dict(E3=3))
        method['manifest']['meaningfulness_basis'] = []
        self.assertEqual(_validate_agent_report('paper', method), [])

    def test_store_rejects_new_report_before_any_batch_write(self):
        _, assembled = self.aggregate(self.paths())
        self.service.set_scores(self.document['id'], [dict(kind='paper', score=80, detail=json.dumps(assembled))])
        malformed = copy.deepcopy(assembled)
        malformed['sub_scores'][0]['original_output']['dimensions'][2]['criteria'][0]['score'] = 2
        with self.assertRaises(ValueError):
            self.service.set_scores(self.document['id'], [dict(kind='confidence', score=20),
                                    dict(kind='paper', score=80, detail=json.dumps(malformed))])
        scores = self.service.document_scores(self.document['id'])
        self.assertIsNone(scores['confidence'])
        self.assertEqual(scores['paper']['detail'], assembled)
        with self.assertRaises(ValueError):
            self.service.set_scores(self.document['id'], [dict(kind='paper', score=81, detail=json.dumps(assembled))])

    def test_legacy_reports_keep_original_total_only_review_rule(self):
        from tests.test_cli_extensions import ScoreAggregateTests
        first = []
        for slot, changes in zip('ABC', (dict(E1=3), dict(E1=5), {})):
            legacy = ScoreAggregateTests.agent_report(self, total=80)
            legacy['dimensions'][2]['criteria'] = report(slot, changes)['dimensions'][2]['criteria']
            first.append(self.write(slot + '.json', legacy))
        payload, assembled = self.aggregate(first)
        self.assertFalse(payload['rechecked'])
        self.assertEqual(assembled['rubric_version'], '1.0.0')

    def test_published_schema_accepts_actual_assembled_contract(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest('Optional JSON Schema validator unavailable')
        text = (Path(__file__).resolve().parents[1] / 'rubrics' / 'paper-scoring.md').read_text(encoding='utf-8')
        schema = json.loads(text.split('```json\n', 1)[1].split('\n```', 1)[0])
        Draft202012Validator.check_schema(schema)
        changes = (dict(E1=3), dict(E1=5), {})
        for reviews in (None, self.paths(prefix='R')):
            _, assembled = self.aggregate(self.paths(changes if reviews else None), reviews)
            errors = list(Draft202012Validator(schema).iter_errors(assembled))
            self.assertFalse(errors, '\n'.join(error.message for error in errors))
