# SPDX-License-Identifier: AGPL-3.0-only
"""Pure paper-review consistency rules / 论文评阅的一致性规则，不执行 IO。"""
import re

PAPER_VERSION = '1.1.0'
PAPER_CRITERIA = {
    'novelty': tuple('N%d' % n for n in range(1, 6)),
    'innovation_degree': tuple('D%d' % n for n in range(1, 5)),
    'effectiveness': tuple('E%d' % n for n in range(1, 9)),
    'rigor': ('R1', 'R2'),
    'clarity': ('C1',),
}
CRITICAL_ITEMS = ('D1', 'D2', 'D3', 'D4', 'E1', 'E3', 'E4', 'E6', 'E7')
CRITICAL_SPREAD = 2


def uses_item_review(kind, report):
    # Historical reports keep their original contract / 历史版本不静默套用新规则。
    return kind == 'paper' and report.get('rubric_version') == PAPER_VERSION


def validate_paper_items(report):
    """Check complete item scores and traceable evidence, never scientific truth.

    只验证完整性与证据字段；原文是否支持连续条件仍须由评委核对。
    """
    errors = []
    if report.get('agent_id') not in ('A', 'B', 'C') and 'sub_scores' not in report:
        errors.append('单评委报告 agent_id 必须为 A/B/C。')
    for field in ('strengths', 'weaknesses'):
        if not isinstance(report.get(field), list):
            errors.append('%s 必须为列表。' % field)
    if type(report.get('total')) is not int or not 0 <= report['total'] <= 100:
        errors.append('1.1.0 total 必须是 0–100 整数。')
    dimensions = report.get('dimensions')
    if not isinstance(dimensions, list):
        return errors + ['1.1.0 需要完整的五维条目。']
    keys = [dim.get('key') if isinstance(dim, dict) else None for dim in dimensions]
    if keys != list(PAPER_CRITERIA):
        errors.append('1.1.0 五维必须按规定顺序出现且无重复。')
    manifest = report.get('manifest')
    if not isinstance(manifest, dict):
        return errors + ['缺少 manifest。']
    if not isinstance(manifest.get('paper_ref'), str) or not manifest['paper_ref'].strip():
        errors.append('manifest.paper_ref 需要明确论文版本。')
    digest = manifest.get('rubric_sha256')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        errors.append('manifest.rubric_sha256 必须为 SHA-256。')
    if manifest.get('evidence_mode') not in ('empirical', 'theoretical', 'mixed'):
        errors.append('manifest.evidence_mode 无效。')
    for field in ('main_claims', 'comparison_set', 'primary_metrics', 'meaningful_thresholds'):
        values = manifest.get(field)
        if (not isinstance(values, list) or (field == 'main_claims' and not values)
                or any(not isinstance(value, str) or not value.strip() for value in values)):
            errors.append('manifest.%s 需要材料清单。' % field)
    if manifest.get('effect_context') not in ('application', 'method', 'theoretical', 'mixed'):
        errors.append('manifest.effect_context 必须声明实际意义的研究类型。')
    basis = manifest.get('meaningfulness_basis')
    if not isinstance(basis, list) or any(not isinstance(value, str) or not value.strip() for value in basis):
        errors.append('manifest.meaningfulness_basis 必须为原文依据列表（未报告可为空）。')
    inputs = manifest.get('inputs')
    source_ids = set()
    if isinstance(inputs, list):
        source_ids = {
            item['source_id'] for item in inputs
            if isinstance(item, dict) and isinstance(item.get('source_id'), str)
        }
    if not source_ids:
        errors.append('manifest.inputs 需要材料 source_id。')
    if isinstance(inputs, list):
        if len(source_ids) != len(inputs):
            errors.append('材料 source_id 缺失或重复。')
        for item in inputs:
            if not isinstance(item, dict):
                continue
            digest = item.get('sha256')
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                errors.append('材料 sha256 必须为 SHA-256。')
    for dimension in dimensions:
        if (not isinstance(dimension, dict) or not isinstance(dimension.get('key'), str)
                or dimension['key'] not in PAPER_CRITERIA):
            continue
        key = dimension['key']
        if (type(dimension.get('score')) is not int or type(dimension.get('max')) is not int
                or dimension['max'] != 5 * len(PAPER_CRITERIA[key])):
            errors.append('%s 维度分须为整数，max 须符合固定权重。' % key)
        items = dimension.get('criteria')
        if not isinstance(items, list):
            errors.append('%s 缺少 criteria。' % key)
            continue
        ids = [item.get('id') if isinstance(item, dict) else None for item in items]
        if ids != list(PAPER_CRITERIA[key]):
            errors.append('%s 条目必须按规定顺序完整出现且无重复。' % key)
        scores = []
        for item in items:
            if not isinstance(item, dict):
                errors.append('%s 条目必须是对象。' % key)
                continue
            where = '%s:%s' % (key, item.get('id'))
            score = item.get('score')
            if type(score) is not int or not 0 <= score <= 5:
                errors.append('%s 分数必须是 0–5 整数。' % where)
            else:
                scores.append(score)
            if item.get('id') == 'E3' and type(score) is int and score >= 4:
                if not basis:
                    errors.append('E3≥4 必须记录所用的实际意义依据。')
                if manifest.get('effect_context') == 'application' and not manifest.get('meaningful_thresholds'):
                    errors.append('应用型 E3≥4 必须记录预定义阈值/约束。')
            if not isinstance(item.get('rationale'), str) or not item['rationale'].strip():
                errors.append('%s 缺少连续条件理由。' % where)
            evidence = item.get('evidence')
            if not isinstance(evidence, list) or not evidence:
                errors.append('%s 缺少证据/未报告的核对位置。' % where)
                continue
            for entry in evidence:
                if not isinstance(entry, dict):
                    errors.append('%s 证据必须是对象。' % where)
                    continue
                if not isinstance(entry.get('source_id'), str) or entry['source_id'] not in source_ids:
                    errors.append('%s 证据 source_id 不在冻结材料中。' % where)
                if not isinstance(entry.get('locator'), str) or not entry['locator'].strip():
                    errors.append('%s 缺少原文核对位置。' % where)
                status = entry.get('status')
                quote = entry.get('quote')
                if status == 'observed':
                    if not isinstance(quote, str) or not quote.strip():
                        errors.append('%s observed 证据需要摘录。' % where)
                elif status not in ('not_reported', 'unverifiable') or quote is not None:
                    errors.append('%s 证据状态或空摘录无效。' % where)
        if len(scores) == len(items) and dimension.get('score') != sum(scores):
            errors.append('%s 维度分不等于条目加总。' % key)
    return errors


def critical_disagreements(reports):
    """Called only after item validation / 仅对已通过条目校验的报告调用。"""
    tables = [{item['id']: item['score'] for dim in report['dimensions']
               for item in dim['criteria']} for _, report in reports]
    return [item for item in CRITICAL_ITEMS
            if max(table[item] for table in tables) - min(table[item] for table in tables) >= CRITICAL_SPREAD]


def validate_paper_aggregation(report):
    """Recompute review gates from originals so flags cannot bypass a review.

    从完整首轮/复核报告重算门槛；单独标记已复核不能绕过规则。
    """
    errors, first, final = [], [], []
    aggregation = report.get('aggregation')
    subs = report.get('sub_scores')
    if not isinstance(aggregation, dict) or not isinstance(subs, list) or len(subs) != 3:
        return ['1.1.0 需要 aggregation 和三份完整原文。']
    rechecked = aggregation.get('rechecked')
    if type(rechecked) is not bool:
        errors.append('aggregation.rechecked 必须是布尔值。')
    if (aggregation.get('method') != 'median' or type(aggregation.get('rounds')) is not int
            or aggregation['rounds'] != (1 if rechecked else 0)):
        errors.append('汇总方法/复核轮数与规则不符。')
    if type(aggregation.get('unresolved_disagreement')) is not bool:
        errors.append('unresolved_disagreement 必须是布尔值。')
    for field in ('spread', 'initial_spread'):
        if type(aggregation.get(field)) is not int:
            errors.append('aggregation.%s 必须为整数。' % field)
    for slot, sub in zip('ABC', subs):
        if not isinstance(sub, dict) or sub.get('agent_id') != slot:
            errors.append('sub_scores 必须按 A/B/C 排列。')
            continue
        original = sub.get('original_output')
        review = sub.get('recheck_output')
        current = review if rechecked else original
        for label, candidate in (('首轮', original), ('有效', current)):
            if not isinstance(candidate, dict):
                errors.append('%s %s缺少完整报告对象。' % (slot, label))
                continue
            if candidate.get('rubric_version') != PAPER_VERSION or candidate.get('manifest') != report.get('manifest'):
                errors.append('%s %s版本/冻结材料与汇总不一致。' % (slot, label))
            if candidate.get('agent_id') != slot:
                errors.append('%s %s agent_id 不一致。' % (slot, label))
            errors.extend(validate_paper_items(candidate))
            total = candidate.get('total')
            if type(total) is not int or not 0 <= total <= 100:
                errors.append('%s %s总分无效。' % (slot, label))
            elif isinstance(candidate.get('dimensions'), list) and all(
                    isinstance(dim, dict) and type(dim.get('score')) is int for dim in candidate['dimensions']):
                if total != sum(dim['score'] for dim in candidate['dimensions']):
                    errors.append('%s %s总分不等于维度和。' % (slot, label))
        if not rechecked and review is not None:
            errors.append('%s 未复核时不能包含复核报告。' % slot)
        if isinstance(current, dict) and sub.get('total') != current.get('total'):
            errors.append('%s 子评分与有效原文不一致。' % slot)
        first.append((slot, original))
        final.append((slot, current))
    if errors:
        return errors
    initial_spread = max(r['total'] for _, r in first) - min(r['total'] for _, r in first)
    spread = max(r['total'] for _, r in final) - min(r['total'] for _, r in final)
    initial_items, items = critical_disagreements(first), critical_disagreements(final)
    if (initial_spread > 10 or initial_items or spread > 10 or items) and not rechecked:
        errors.append('总分或关键条目分歧触发复核，必须提供三份有效复核报告。')
    for field, expected in (('initial_spread', initial_spread), ('spread', spread),
                            ('initial_critical_items', initial_items), ('critical_items', items),
                            ('unresolved_disagreement', bool(rechecked and (spread > 10 or items)))):
        if aggregation.get(field) != expected:
            errors.append('aggregation.%s 与完整原文不一致。' % field)
    median = sorted(r['total'] for _, r in final)[1]
    slot, selected = next((slot, r) for slot, r in final if r['total'] == median)
    if aggregation.get('selected_agent_id') != slot:
        errors.append('代表评委必须按中位数及 A/B/C 顺序选择。')
    for field in ('total', 'dimensions', 'strengths', 'weaknesses'):
        if report.get(field) != selected.get(field):
            errors.append('汇总 %s 与代表评委不一致。' % field)
    if aggregation.get('unresolved_disagreement') and not aggregation.get('recheck_reason'):
        errors.append('未解决分歧必须说明原因。')
    return errors
