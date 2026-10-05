# SPDX-License-Identifier: AGPL-3.0-only
"""Command-line interface over the same LocalService contract as the GUI.

人和 AI agent 共用这一入口:每个命令直接调用 LocalService(与界面相同的校验、
审计与存储),`--json` 输出机器可读结果。命令说明见仓库根目录 cli.md——
未来的 MCP 服务即以该文档为工具契约。
"""
import argparse
import hashlib
import sqlite3
import json
import os
import re
import sys
import time
from pathlib import Path

from .service import LocalService
from . import arxiv as arxiv_client
from .arxiv import NetworkError
from .instance import LibraryBusy
from .scoring_review import (
    critical_disagreements, uses_item_review, validate_paper_aggregation,
    validate_paper_items,
)

SCORE_KINDS = ('paper', 'confidence', 'summary')
RUBRIC_FILES = {'paper': 'paper-scoring.md', 'confidence': 'confidence-scoring.md'}
# Same light limits as store.set_scores so `score validate` can pre-flight a report.
DETAIL_MAX_BYTES = 256 * 1024
RATIONALE_MAX_BYTES = 64 * 1024
DIMENSION_LIMITS = {
    'paper': {'novelty': 25, 'innovation_degree': 20, 'effectiveness': 40, 'rigor': 10, 'clarity': 5},
    'confidence': {'evidence': 40, 'consistency': 25, 'traceability': 15, 'plausibility': 20},
}

def _print(value, args):
    if getattr(args, 'json', False):
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        if isinstance(value, str):
            print(value)
        else:
            print(json.dumps(value, ensure_ascii=False, indent=2))

def resolve_document(service, reference):
    """Accept a full id, an id prefix or a unique title substring."""
    documents = service.list_documents()
    for document in documents:
        if document['id'] == reference:
            return document
    for document in documents:
        if document['id'].startswith(reference):
            return document
    matches = [d for d in documents if reference.lower() in d['title'].lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError('未找到文献:' + reference)
    listing = '\n'.join('- %s %s' % (d['id'][:8], d['title'][:60]) for d in matches)
    raise ValueError('引用不唯一,请用更长的 id 前缀或完整标题:\n' + listing)

def resolve_collection(service, reference):
    matches = [c for c in service.list_collections() if c['name'] == reference or c['id'].startswith(reference)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError('未找到集合:' + reference)
    listing = '\n'.join('- %s %s' % (c['id'][:8], c['name']) for c in matches)
    raise ValueError('集合名不唯一,请用 id 前缀:\n' + listing)

# ---------------------------------------------------------------- 打分文档
def _rubrics_dir():
    """Locate rubrics: env override -> source checkout -> frozen bundle resources."""
    override = os.environ.get('POLYSCHOLAR_RUBRICS_DIR')
    if override:
        return Path(override)
    from .resource_paths import resource_path
    return resource_path('rubrics')

def _rubric_version(data):
    match = re.search(r'版本[：:]\s*([0-9]+\.[0-9]+\.[0-9]+)', data.decode('utf-8', errors='replace')[:2000])
    return match.group(1) if match else ''

def _rubric_payload(kind, directory):
    path = directory / RUBRIC_FILES[kind]
    if not path.is_file():
        raise ValueError('rubric 文件缺失:%s(可用 POLYSCHOLAR_RUBRICS_DIR 指定目录)' % path)
    data = path.read_bytes()
    return dict(kind=kind, path=str(path), sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data), version=_rubric_version(data))

def cmd_rubric(service, args):
    if args.action == 'show' and not args.kind:
        raise ValueError('rubric show 需要 paper 或 confidence。')
    directory = _rubrics_dir()
    if args.action == 'list':
        rows = [_rubric_payload(kind, directory) for kind in ('paper', 'confidence')]
        _print(rows, args)
        return 0
    payload = _rubric_payload(args.kind, directory)
    payload['text'] = (directory / RUBRIC_FILES[args.kind]).read_text(encoding='utf-8')
    _print(payload, args)
    return 0

def cmd_status(service, args):
    """Lock/coverage diagnostics without requiring the library lock.

    main() runs this before LocalService when no service was injected, so a
    busy library can still be inspected instead of failing with exit code 2.
    """
    from .instance import LibraryBusy, LibraryLock
    from .service import app_data_dir
    if service is not None:
        root = Path(service.store.root)
        report = dict(data_dir=str(root), lock='self(本进程持有)')
    else:
        root = Path(args.data_dir).resolve() if args.data_dir else Path(app_data_dir())
        report = dict(data_dir=str(root), lock='unknown')
        try:
            lock = LibraryLock(root)
            lock.close()
            report['lock'] = 'free'
        except LibraryBusy:
            report['lock'] = 'busy'
        except (OSError, ValueError):
            pass
    try:
        import sqlite3
        db = sqlite3.connect('file:%s?mode=ro' % (root / 'library.sqlite3').as_posix(), uri=True)
        try:
            report['documents'] = db.execute('SELECT count(*) FROM desktop_documents').fetchone()[0]
            report['scores'] = dict(db.execute(
                'SELECT kind,count(*) FROM desktop_scores GROUP BY kind').fetchall())
            report['jobs'] = dict(db.execute(
                'SELECT state,count(*) FROM desktop_jobs GROUP BY state').fetchall())
        finally:
            db.close()
    except Exception:
        report['note'] = '数据库统计不可用(可能目录尚未创建或正被其他进程写入)。'
    import platform
    report['python'] = platform.python_version()
    report['rubrics_dir'] = str(_rubrics_dir())
    _print(report, args)
    return 0

# ---------------------------------------------------------------- 文献
META_FIELDS = {'title', 'authors', 'doi', 'year', 'url', 'abstract', 'tags', 'itemType', 'creators'}

def _load_meta_patch(path):
    """Explicit metadata for local PDF imports; never guessed from page text."""
    try:
        patch = json.loads(Path(path).read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ValueError('--meta 文件不是有效 JSON:%s' % error) from None
    if not isinstance(patch, dict) or set(patch) - META_FIELDS:
        raise ValueError('--meta 字段无效,允许:' + ','.join(sorted(META_FIELDS)))
    return patch

def cmd_add(service, args):
    patch = _load_meta_patch(args.meta) if args.meta else None
    if args.arxiv:
        entries = service.arxiv_lookup(args.arxiv)
        if patch and len(entries) > 1:
            raise ValueError('--meta 仅支持单篇导入。')
        collection_id = resolve_collection(service, args.collection)['id'] if args.collection else None
        result = service.arxiv_import(entries, collection_id)
        if not result['imported']:
            raise ValueError(result['errors'] or '导入失败。')
        if patch:
            service.update_document(result['imported'][0]['id'], patch)
        if args.quiet:
            _print(dict(imported=[row['id'] for row in result['imported']],
                        errors=result['errors']), args)
        elif len(result['imported']) == 1:
            _print(service.document(result['imported'][0]['id']), args)
        else:
            _print(dict(imported=result['imported'], errors=result['errors']), args)
        return 0
    if not args.pdf:
        raise ValueError('必须提供 PDF 路径或 --arxiv 链接。')
    document_id = service.import_pdf(args.pdf).get('id')
    if patch:
        service.update_document(document_id, patch)
    if args.collection:
        collection = resolve_collection(service, args.collection)
        service.set_membership(document_id, collection['id'])
    document = service.document(document_id)
    if getattr(args, 'quiet', False):
        _print(dict(id=document['id'], title=document['title']), args)
    else:
        _print(document, args)
    return 0

def cmd_search(service, args):
    """Library-wide or per-paper full-text lookup; the built-in grep for agents."""
    document_id = resolve_document(service, args.doc)['id'] if args.doc else None
    result = service.search_fulltext(args.query, limit=args.limit, document_id=document_id)
    items = [dict(documentId=item['documentId'], title=item['title'], page=item['pageNumber'],
                  blockId=item['blockId'], snippet=item['snippet'], kind=item['kind'])
             for item in result['items']]
    payload = dict(total=result['total'], truncated=result['truncated'], items=items)
    if not result['total']:
        statuses = sorted({entry['status'] for entry in result['coverage']}) or ['unparsed']
        payload['hint'] = '未命中;范围内文献解析/索引状态:' + ','.join(statuses) + '(未解析的先执行 parse)。'
    if args.json:
        _print(payload, args)
    else:
        for item in items:
            print('- %s p%d [%s] %s' % (item['title'][:40], item['page'], item['blockId'][:12], item['snippet']))
        if payload['truncated']:
            print('…仅显示前 %d 条,共 %d 条(可用 --limit 调整)' % (len(items), payload['total']))
        if 'hint' in payload:
            print(payload['hint'])
    return 0

def cmd_list(service, args):
    documents = service.list_documents()
    if args.json:
        if args.quiet:
            documents = [dict(id=d['id'], title=d['title'], year=d.get('year', ''),
                              scores=d.get('scores') or {}) for d in documents]
        _print(documents, args)
        return 0
    for d in documents:
        scores = d.get('scores') or {}
        line = '- %s %s' % (d['id'][:8], d['title'][:60])
        marks = []
        if scores.get('paper') is not None: marks.append('论文 %g' % scores['paper'])
        if scores.get('confidence') is not None: marks.append('置信 %g' % scores['confidence'])
        if marks: line += '  [' + '/'.join(marks) + ']'
        print(line)
    return 0

def cmd_show(service, args):
    document = resolve_document(service, args.document)
    payload = dict(document)
    payload['scores'] = service.document_scores(document['id'])
    member_ids = service.document_collections(document['id'])
    names = {c['id']: c['name'] for c in service.list_collections()}
    payload['collections'] = [names[i] for i in member_ids if i in names]
    if args.text:
        blocks = service.document_blocks(document['id'])
        pages = _parse_page_range(getattr(args, 'pages', None))
        if pages:
            blocks = [b for b in blocks if pages[0] <= b['pageNumber'] <= pages[1]]
        payload['text'] = '\n\n'.join('[p%d] %s' % (b['pageNumber'], b['text']) for b in blocks)
    _print(payload, args)
    return 0

def _parse_page_range(text):
    """Accept 'A' or 'A-B'; None means the whole document."""
    if not text:
        return None
    match = re.fullmatch(r'(\d+)(?:-(\d+))?', text.strip())
    if not match:
        raise ValueError('--pages 格式应为 A 或 A-B,例如 2-5。')
    start, end = int(match.group(1)), int(match.group(2) or match.group(1))
    if start < 1 or end < start:
        raise ValueError('--pages 范围无效。')
    return (start, end)

def cmd_update(service, args):
    document = resolve_document(service, args.document)
    patch = {}
    for key in ('title', 'authors', 'doi', 'year', 'url', 'abstract'):
        value = getattr(args, key, None)
        if value is not None:
            patch[key] = value
    if args.tags is not None:
        patch['tags'] = [t.strip() for t in args.tags.split(',') if t.strip()]
    if not patch:
        raise ValueError('没有提供任何要修改的字段。')
    document = service.update_document(document['id'], patch)
    _print(document, args)
    return 0

def cmd_remove(service, args):
    document = resolve_document(service, args.document)
    if not args.yes:
        answer = input('删除文献「%s」及其附件/任务/评分?原文件不受影响。[y/N] ' % document['title'][:50])
        if answer.strip().lower() not in ('y', 'yes'):
            print('已取消。');return 0
    service.delete_document(document['id'])
    print('已删除', document['id'])
    return 0

def cmd_text(service, args):
    document = resolve_document(service, args.document)
    blocks = service.document_blocks(document['id'])
    if args.json:
        _print(blocks, args);return 0
    for block in blocks:
        print('[p%d %s]' % (block['pageNumber'], block['id']), block['text'])
    return 0

# ---------------------------------------------------------------- 集合
def cmd_collection(service, args):
    if args.action == 'add':
        parent = resolve_collection(service, args.parent) if args.parent else None
        collection = service.create_collection(args.name, parent['id'] if parent else None)
        _print(collection, args)
    elif args.action == 'remove':
        collection = resolve_collection(service, args.name)
        service.delete_collection(collection['id'])
        print('已删除集合', collection['name'])
    elif args.action == 'list':
        collections = service.list_collections()
        if args.json:
            _print(collections, args);return 0
        names = {c['id']: c['name'] for c in collections}
        for c in collections:
            parent = names.get(c['parentId'], '—') if c.get('parentId') else '—'
            print('- %s (%s) 父:%s | %d 篇' % (c['name'], c['id'][:8], parent, c.get('count', 0)))
    elif args.action in ('attach', 'detach'):
        reference = args.document or args.name
        if not reference or not args.collection:
            raise ValueError('attach/detach 需要 <doc> 位置参数与 --collection <集合名>。')
        document = resolve_document(service, reference)
        collection = resolve_collection(service, args.collection)
        present = args.action == 'attach'
        service.set_membership(document['id'], collection['id'], present)
        print('已加入' if present else '已移出', collection['name'])
    return 0

# ---------------------------------------------------------------- 翻译与任务
def cmd_translate(service, args):
    if args.no_wait:
        raise ValueError('命令行暂不支持后台翻译；请去掉 --no-wait 或在桌面应用创建任务。')
    document = resolve_document(service, args.document)
    job = (service.start_html_translation(document['id']) if args.engine == 'html-llm'
           else service.start_translation(document['id'], engine=args.engine))
    print('任务已创建:', job['id'], file=sys.stderr)
    deadline = time.monotonic() + max(60, job.get('timeoutSeconds', 600))
    last = ''
    while time.monotonic() < deadline:
        current = next((j for j in service.list_jobs() if j['id'] == job['id']), None)
        if current is None:break
        progress = current.get('progress')
        line = '%s %s' % (current['state'], ('%s/%s' % (progress['current'], progress['total'])) if isinstance(progress, dict) else '')
        if line != last:
            print(line, file=sys.stderr, flush=True);last = line
        if current['state'] in ('completed', 'failed'):break
        time.sleep(1)
    current = next((j for j in service.list_jobs() if j['id'] == job['id']), None)
    if current is None:
        raise ValueError('翻译任务已移除。')
    if current['state'] != 'completed':
        raise ValueError(current.get('error') or '翻译失败。')
    artifacts = service.document_translations(document['id'])
    artifacts = [item for item in artifacts if item.get('jobId') == job['id']]
    if not artifacts:
        raise ValueError('任务完成但没有可用译文。')
    _print(artifacts, args)
    return 0

def cmd_jobs(service, args):
    jobs = service.list_jobs()
    if args.json:
        _print(jobs, args);return 0
    for j in jobs:
        print('- %s %s %s %s' % (j['id'][:8], j.get('engine'), j['state'], j.get('error') or ''))
    return 0

def cmd_export(service, args):
    document = resolve_document(service, args.document)
    translations = service.document_translations(document['id'])
    if not translations:
        raise ValueError('该文献没有可导出的译文。')
    entry = translations[0]
    name = Path(entry.get('path', 'translated.html')).name
    destination = args.output or (args.document[:8] + '-' + name)
    job = next(j for j in service.list_jobs() if j['id'] == entry['jobId'])
    index = job['artifacts'].index(name)
    service.export_translation(job['id'], index, str(Path(destination).resolve()))
    print('已导出:', Path(destination).resolve())
    return 0

def cmd_parse(service, args):
    document = resolve_document(service, args.document)
    revision = service.parse_document(document['id'])
    blocks = service.document_blocks(document['id'])
    print('解析完成:', revision['id'][:12], '| 文本块:', len(blocks))
    return 0

# ---------------------------------------------------------------- 评分与提炼
def _load_detail_file(path):
    """Read a score detail file with the same light limits as store.set_scores."""
    if not path:
        return '{}'
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise ValueError('无法读取明细文件:%s' % error) from None
    if len(data) > DETAIL_MAX_BYTES:
        raise ValueError('评分明细 JSON 不能超过 256 KiB(当前 %d 字节)。' % len(data))
    text = data.decode('utf-8')
    if text.strip():
        try:
            json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError('评分明细必须是有效 JSON:%s' % error) from None
        return text
    return '{}'

def _validate_report(kind, report):
    """Structural checks mirroring rubric §6.2: sums, medians and recheck rule.

    只做程序可核验的结构校验,不评判内容质量;语义仍由 rubric 与 agent 负责。
    """
    errors = []
    if not isinstance(report, dict):
        return ['报告必须是 JSON 对象。']
    if uses_item_review(kind, report):
        errors.extend(validate_paper_items(report))
        errors.extend(validate_paper_aggregation(report))
    total = report.get('total')
    if not isinstance(total, (int, float)) or isinstance(total, bool) or not 0 <= total <= 100:
        errors.append('total 必须是 0–100 的数值。')
    elif kind == 'paper' and float(total) != int(total):
        errors.append('paper 总分必须是整数。')
    elif kind == 'confidence' and float(total) * 2 != int(float(total) * 2):
        errors.append('confidence 总分必须是 0.5 的倍数。')
    limits = DIMENSION_LIMITS[kind]
    dims = report.get('dimensions')
    if not isinstance(dims, list):
        errors.append('dimensions 必须是列表。')
    else:
        seen = {}
        for dim in dims:
            key = dim.get('key') if isinstance(dim, dict) else None
            if not isinstance(key, str) or key not in limits:
                errors.append('维度 key 无效:%r(允许:%s)' % (key, ','.join(limits)))
                continue
            score = dim.get('score')
            if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= limits[key]:
                errors.append('维度 %s 分数必须在 0–%g。' % (key, limits[key]))
            seen[key] = score
        missing = [key for key in limits if key not in seen]
        if missing:
            errors.append('缺少维度:' + ','.join(missing))
        elif (all(isinstance(value, (int, float)) and not isinstance(value, bool)
                  for value in seen.values())
              and isinstance(total, (int, float)) and not isinstance(total, bool)):
            if round(sum(seen.values()), 4) != round(float(total), 4):
                errors.append('维度之和 %g 不等于 total %g。' % (sum(seen.values()), float(total)))
    for field in (('strengths', 'weaknesses') if kind == 'paper' else ('red_flags',)):
        if not isinstance(report.get(field), list):
            errors.append('%s 必须是列表。' % field)
    if kind == 'confidence' and report.get('verdict') not in ('高度可信', '较可信', '存疑', '明显存疑', '高度存疑'):
        errors.append('verdict 必须是五档之一(高度可信/较可信/存疑/明显存疑/高度存疑)。')
    subs = report.get('sub_scores')
    if not isinstance(subs, list) or len(subs) != 3:
        errors.append('sub_scores 必须是三个子代理的评分。')
        return errors
    totals = [sub.get('total') if isinstance(sub, dict) else None for sub in subs]
    if any(not isinstance(t, (int, float)) or isinstance(t, bool) or not 0 <= t <= 100 for t in totals):
        errors.append('每个子代理 total 必须是 0–100 的数值。')
        return errors
    ordered = sorted(totals)
    spread = ordered[2] - ordered[0]
    if isinstance(total, (int, float)) and not isinstance(total, bool) and ordered[1] != float(total):
        errors.append('total %g 不是三个子评分的中位数 %g。' % (float(total), ordered[1]))
    aggregation = report.get('aggregation')
    if not isinstance(aggregation, dict):
        errors.append('缺少 aggregation 对象。')
        return errors
    if aggregation.get('method') != 'median':
        errors.append('aggregation.method 必须是 "median"。')
    expected_rounds = 1 if aggregation.get('rechecked') else 0
    if aggregation.get('rounds') != expected_rounds:
        errors.append('aggregation.rounds %r 与复核状态不符(rubric 规定:未复核 rounds=0,复核过 rounds=1)。'
                      % (aggregation.get('rounds'),))
    declared = aggregation.get('spread')
    if not isinstance(declared, (int, float)) or isinstance(declared, bool) or round(float(declared), 4) != round(spread, 4):
        errors.append('aggregation.spread %r 与子评分实际极差 %g 不符。' % (declared, spread))
    threshold = 10 if kind == 'paper' else 15
    if spread > threshold and not aggregation.get('rechecked'):
        errors.append('极差 %g 超过 %d,rubric 要求复核后才能汇总(aggregation.rechecked 必须为 true)。' % (spread, threshold))
    return errors

# ---------------------------------------------------------------- 合卷(三盲评聚合)
AGENT_REPORT_MAX_BYTES = 1024 * 1024
VERDICTS = ('高度可信', '较可信', '存疑', '明显存疑', '高度存疑')
SUMMARY_SECTIONS = ('problem', 'method', 'results', 'limitations')
SUMMARY_SECTION_ITEMS = 16
SUMMARY_TEXT_BYTES = 2000
SUMMARY_EVIDENCE_BYTES = 500
SUMMARY_NAMES = {'problem': '解决的问题', 'method': '使用的方法',
                 'results': '实验效果', 'limitations': '不足与缺陷'}

def _load_json_file(path, what):
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise ValueError('无法读取%s %s:%s' % (what, path, error)) from None
    if len(data) > AGENT_REPORT_MAX_BYTES:
        raise ValueError('%s %s 超过 1 MiB 上限。' % (what, path))
    try:
        return json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('%s %s 不是有效 UTF-8 JSON:%s' % (what, path, error)) from None

def _validate_agent_report(kind, report):
    """Structural checks for one blind-review report (no sub_scores/aggregation)."""
    errors = []
    if not isinstance(report, dict):
        return ['报告必须是 JSON 对象。']
    if kind == 'paper' and report.get('rubric_version') not in ('1.0.0', '1.1.0'):
        errors.append('不支持的论文量表版本；请读取 rubric show paper。')
    if uses_item_review(kind, report):
        errors.extend(validate_paper_items(report))
    if not isinstance(report.get('rubric_version'), str) or not report['rubric_version']:
        errors.append('缺少 rubric_version。')
    if not isinstance(report.get('manifest'), dict):
        errors.append('缺少 manifest(冻结材料包)。')
    total = report.get('total')
    if not isinstance(total, (int, float)) or isinstance(total, bool) or not 0 <= total <= 100:
        errors.append('total 必须是 0–100 的数值。')
    elif kind == 'paper' and float(total) != int(total):
        errors.append('paper 总分必须是整数。')
    elif kind == 'confidence' and float(total) * 2 != int(float(total) * 2):
        errors.append('confidence 总分必须是 0.5 的倍数。')
    limits = DIMENSION_LIMITS[kind]
    dims = report.get('dimensions')
    if not isinstance(dims, list):
        errors.append('dimensions 必须是列表。')
    else:
        seen = {}
        for dim in dims:
            key = dim.get('key') if isinstance(dim, dict) else None
            if not isinstance(key, str) or key not in limits:
                errors.append('维度 key 无效:%r' % (key,))
                continue
            score = dim.get('score')
            if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= limits[key]:
                errors.append('维度 %s 分数必须在 0–%g。' % (key, limits[key]))
            seen[key] = score
        missing = [key for key in limits if key not in seen]
        if missing:
            errors.append('缺少维度:' + ','.join(missing))
        elif (all(isinstance(value, (int, float)) and not isinstance(value, bool)
                  for value in seen.values())
              and isinstance(total, (int, float)) and not isinstance(total, bool)):
            if round(sum(seen.values()), 4) != round(float(total), 4):
                errors.append('维度之和 %g 不等于 total %g。' % (sum(seen.values()), float(total)))
    for field in (('strengths', 'weaknesses') if kind == 'paper' else ('red_flags',)):
        if not isinstance(report.get(field), list):
            errors.append('%s 必须是列表。' % field)
    if kind == 'confidence' and report.get('verdict') not in VERDICTS:
        errors.append('verdict 必须是五档之一。')
    return errors

def _disagreement_items(reports):
    """Dimension and criteria keys where the three agents' scores differ."""
    per_agent = []
    for _, report in reports:
        table = {}
        for dim in report.get('dimensions') or []:
            key = dim.get('key')
            if not isinstance(key, str):
                continue
            entries = {}
            for item in dim.get('criteria') or []:
                if isinstance(item, dict) and item.get('id'):
                    entries[str(item['id'])] = item.get('score')
            table[key] = (dim.get('score'), entries)
        per_agent.append(table)
    differing = []
    for key in per_agent[0]:
        if len({agent.get(key, (None, {}))[0] for agent in per_agent}) > 1:
            differing.append(key)
        criteria_maps = [agent.get(key, (None, {}))[1] for agent in per_agent]
        # Criteria-level comparison only when all three agree on the item ids.
        if all(criteria_maps) and len({frozenset(item) for item in criteria_maps}) == 1:
            for item_id in criteria_maps[0]:
                if len({item.get(item_id) for item in criteria_maps}) > 1:
                    differing.append('%s:%s' % (key, item_id))
    return differing

def _load_report_text(path):
    """One judge report for the archive; same 1 MiB cap as aggregation input."""
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise ValueError('无法读取报告文件:%s' % error) from None
    if len(data) > AGENT_REPORT_MAX_BYTES:
        raise ValueError('单份评委报告不能超过 1 MiB。')
    text = data.decode('utf-8')
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError('评委报告必须是有效 JSON:%s' % error) from None
    if not isinstance(parsed, dict):
        raise ValueError('评委报告必须是 JSON 对象。')
    return text

def _validate_summary_agent_report(report):
    """Contract for one extraction agent: the four sections, each a list of items."""
    if not isinstance(report, dict):
        return ['报告必须是 JSON 对象。']
    errors = []
    if set(report) - set(SUMMARY_SECTIONS):
        extra = ','.join(sorted(set(report) - set(SUMMARY_SECTIONS)))
        errors.append('存在未知字段:%s(仅允许 %s)。' % (extra, '/'.join(SUMMARY_SECTIONS)))
    for key in SUMMARY_SECTIONS:
        items = report.get(key)
        if not isinstance(items, list) or not 1 <= len(items) <= SUMMARY_SECTION_ITEMS:
            errors.append('%s 必须是 1–%d 条的列表。' % (SUMMARY_NAMES[key], SUMMARY_SECTION_ITEMS))
            continue
        for index, item in enumerate(items):
            where = '%s[%d]' % (key, index)
            if not isinstance(item, dict) or set(item) - {'text', 'evidence'}:
                errors.append(where + ' 必须是 {text, evidence?} 对象。')
                continue
            body = str(item.get('text') or '').strip()
            if not body:
                errors.append(where + ' 的 text 不能为空。')
            elif len(body.encode('utf-8')) > SUMMARY_TEXT_BYTES:
                errors.append(where + ' 的 text 超过 2000 字节。')
            evidence = item.get('evidence')
            if evidence is not None and (not isinstance(evidence, str)
                                         or len(evidence.encode('utf-8')) > SUMMARY_EVIDENCE_BYTES):
                errors.append(where + ' 的 evidence 必须是不超过 500 字节的文本。')
    return errors

def _merge_summary_reports(reports):
    """Union the two agents' items per section; exact duplicates keep both agent tags.

    合并是确定性程序操作:按 A→B 顺序遍历,规范空白后完全相同的条目合并为一条并
    记录来源代理;语义相近但措辞不同的一律保留,不做模型裁量。
    """
    assembled = {'structure': 'summary-1'}
    duplicates = 0
    counts = {}
    for key in SUMMARY_SECTIONS:
        merged = []
        for slot, report in reports:
            for item in report.get(key) or []:
                body = ' '.join(str(item.get('text') or '').split())
                evidence = (item.get('evidence') or '').strip() or None
                for existing in merged:
                    if existing['text'].casefold() == body.casefold():
                        if slot not in existing['agents']:
                            existing['agents'].append(slot)
                        duplicates += 1
                        break
                else:
                    entry = dict(text=body, agents=[slot])
                    if evidence:
                        entry['evidence'] = evidence
                    merged.append(entry)
        assembled[key] = merged
        counts[key] = len(merged)
    assembled['meta'] = dict(agents=[slot for slot, _ in reports], duplicates_dropped=duplicates)
    return assembled, counts

def _export_aggregate_report(service, destination, report):
    """Use the same protected atomic writer as desktop exports.
    与桌面导出共用受保护的原子写入器，不能覆盖库、原文或运行资源。
    """
    target = Path(destination).absolute()
    body = json.dumps(report, ensure_ascii=False, indent=1).encode('utf-8')
    published = service.store.write_export(target, body, extra_protected=[service.resources])
    return str(published)


def _cmd_score_summary_aggregate(service, args):
    """Two extraction agents, one deterministic merge, written straight to the library.

    提炼合卷:两个隔离子代理各产出四节结构化报告(问题/方法/效果/不足),
    程序校验、合并去重并经 --apply 写入 summary 提炼维度;无需评分与盲评中位数。
    """
    if not args.reports or len(args.reports) != 2:
        raise ValueError('aggregate --kind summary 需要 --reports 恰好两份提炼报告文件(A/B 顺序)。')
    if args.recheck:
        raise ValueError('提炼没有复核轮;两份报告直接合并。')
    slots = ('A', 'B')
    reports = [(slots[index], _load_json_file(path, '提炼报告'))
               for index, path in enumerate(args.reports)]
    per_file_errors = {slot: errors for slot, errors in
                       ((slot, _validate_summary_agent_report(report)) for slot, report in reports) if errors}
    if per_file_errors:
        _print(dict(valid=False, errors=per_file_errors), args)
        return 1
    assembled, counts = _merge_summary_reports(reports)
    overview = '双代理提炼:问题 %d / 方法 %d / 效果 %d / 不足 %d 条(完全重合 %d 条已合并)。' % (
        counts['problem'], counts['method'], counts['results'], counts['limitations'],
        assembled['meta']['duplicates_dropped'])
    payload = dict(total=None, sections=counts,
                   duplicates_dropped=assembled['meta']['duplicates_dropped'])
    if args.out:
        payload['out'] = _export_aggregate_report(service, args.out, assembled)
    if args.apply:
        document = resolve_document(service, args.apply)
        rationale = args.rationale or ''
        if args.rationale_file:
            rationale = Path(args.rationale_file).read_text(encoding='utf-8')
        if not rationale:
            rationale = overview
        if len(rationale.encode('utf-8')) > RATIONALE_MAX_BYTES:
            raise ValueError('提炼理由必须是不超过 64 KiB 的文本。')
        service.set_scores(document['id'], [dict(kind='summary', score=None,
                                                 rationale=rationale,
                                                 detail=json.dumps(assembled, ensure_ascii=False))])
        payload['applied'] = document['id']
        payload['rationale'] = rationale
    _print(payload, args)
    return 0

def _cmd_score_aggregate(service, args):
    """Assemble three blind-review reports into the rubric §6 summary report.

    程序化 rubric §4.2/§4.3 的确定性部分:逐份校验、核对版本与冻结材料包一致、
    取中位数、按 A→B→C 选代表报告、嵌入三份原文;首轮总分/关键分项超限先发复核指令,
    复核后仍超限按 rubric 输出中位数并保留分歧。agent 不再自写聚合脚本。
    """
    if args.kind == 'summary':
        return _cmd_score_summary_aggregate(service, args)
    if not args.reports or len(args.reports) != 3:
        raise ValueError('aggregate 需要 --reports 三份首轮报告文件(A/B/C 顺序)。')
    if args.recheck and len(args.recheck) != 3:
        raise ValueError('--recheck 需要恰好三份复核报告文件。')
    if args.apply and not (args.rationale or args.rationale_file):
        raise ValueError('--apply 需要 --rationale 或 --rationale-file 作为入库理由。')
    slots = ('A', 'B', 'C')
    first = [(slots[index], _load_json_file(path, '首轮报告'))
             for index, path in enumerate(args.reports)]
    per_file_errors = {slot: errors for slot, errors in
                       ((slot, _validate_agent_report(args.kind, report)) for slot, report in first) if errors}
    if per_file_errors:
        _print(dict(valid=False, errors=per_file_errors), args)
        return 1
    item_review = uses_item_review(args.kind, first[0][1])
    if item_review and any(report.get('agent_id') != slot for slot, report in first):
        raise ValueError('1.1.0 首轮 agent_id 必须对应 --reports 的 A/B/C 顺序。')
    versions = {report.get('rubric_version') for _, report in first}
    if len(versions) > 1:
        raise ValueError('三份报告的 rubric_version 不一致:' + ','.join(sorted(versions)))
    manifests = [json.dumps(report.get('manifest'), sort_keys=True, ensure_ascii=False)
                 for _, report in first]
    if len(set(manifests)) > 1:
        raise ValueError('三份报告的冻结材料包(manifest)不一致;材料变更必须重开三个隔离首轮。')
    recheck = None
    if args.recheck:
        recheck = [(slots[index], _load_json_file(path, '复核报告'))
                   for index, path in enumerate(args.recheck)]
        recheck_errors = {slot: errors for slot, errors in
                          ((slot, _validate_agent_report(args.kind, report)) for slot, report in recheck) if errors}
        if recheck_errors:
            _print(dict(valid=False, errors=recheck_errors), args)
            return 1
        for slot, report in recheck:
            if report.get('rubric_version') not in versions or json.dumps(
                    report.get('manifest'), sort_keys=True, ensure_ascii=False) != manifests[0]:
                raise ValueError('复核报告的版本/manifest 与首轮不一致；材料变更必须重开首轮。')
            if item_review and report.get('agent_id') != slot:
                raise ValueError('1.1.0 复核 agent_id 必须对应 A/B/C 顺序。')
    initial_totals = [report['total'] for _, report in first]
    initial_spread = max(initial_totals) - min(initial_totals)
    threshold = 10 if args.kind == 'paper' else 15
    initial_items = critical_disagreements(first) if item_review else []
    if (initial_spread > threshold or initial_items) and recheck is None:
        items = _disagreement_items(first)
        _print(dict(needs_recheck=True, spread=initial_spread, threshold=threshold,
                    disagreement=items, critical_items=initial_items,
                    directive=('本轮需复核条目:%s。请重新核对这些条目的连续条件与原文证据,并自检所有加总;'
                               '不要猜测其他代理的评分,不要以缩小分歧为目标;只按打分文档纠正不符合条文的判断,'
                               '重新输出完整 agent_report。' % ('、'.join(items) if items
                                    else '(条目级分数一致而总分不同,请核对各自加总与 rationale)')),
                    recheck_usage='--recheck R_A.json R_B.json R_C.json'), args)
        return 0
    final = recheck or first
    final_totals = [report['total'] for _, report in final]
    ordered = sorted(final_totals)
    median, spread = ordered[1], ordered[2] - ordered[0]
    critical_items = critical_disagreements(final) if item_review else []
    unresolved = spread > threshold or bool(critical_items)
    selected = next(slot for slot, report in final if report['total'] == median)
    representative = final[slots.index(selected)][1]
    sub_scores = []
    for index, slot in enumerate(slots):
        entry = dict(agent_id=slot, total=final_totals[index], original_output=first[index][1])
        if recheck is not None:
            entry['recheck_output'] = recheck[index][1]
        sub_scores.append(entry)
    assembled = dict(rubric_version=list(versions)[0], manifest=first[0][1]['manifest'],
                     total=median, dimensions=representative.get('dimensions'),
                     sub_scores=sub_scores,
                     aggregation=dict(method='median', spread=spread, initial_spread=initial_spread,
                                      rechecked=recheck is not None,
                                      rounds=1 if recheck is not None else 0,
                                      selected_agent_id=selected,
                                      unresolved_disagreement=unresolved,
                                      recheck_reason=None))
    if item_review:
        assembled['aggregation'].update(initial_critical_items=initial_items, critical_items=critical_items)
    if unresolved:
        assembled['aggregation']['recheck_reason'] = (
            '复核后仍有总分或关键条目分歧；总分极差 %g，关键条目：%s。保留中位数与分歧，不强制统一。'
            % (spread, '、'.join(critical_items) or '无'))
    if args.kind == 'paper':
        assembled['strengths'] = representative.get('strengths')
        assembled['weaknesses'] = representative.get('weaknesses')
    else:
        assembled['red_flags'] = representative.get('red_flags')
        assembled['verdict'] = representative.get('verdict')
    residual = _validate_report(args.kind, assembled)
    if residual:
        raise ValueError('聚合结果未通过结构校验(请检查报告字段):' + ';'.join(residual))
    payload = dict(total=median, spread=spread, initial_spread=initial_spread,
                   selected_agent_id=selected, unresolved_disagreement=unresolved,
                   rechecked=recheck is not None)
    if item_review:
        payload.update(initial_critical_items=initial_items, critical_items=critical_items)
    if args.out:
        payload['out'] = _export_aggregate_report(service, args.out, assembled)
    if args.apply:
        document = resolve_document(service, args.apply)
        rationale = args.rationale or ''
        if args.rationale_file:
            rationale = Path(args.rationale_file).read_text(encoding='utf-8')
        if len(rationale.encode('utf-8')) > RATIONALE_MAX_BYTES:
            raise ValueError('评分理由必须是不超过 64 KiB 的文本。')
        service.set_scores(document['id'], [dict(kind=args.kind, score=float(median),
                                                 rationale=rationale,
                                                 detail=json.dumps(assembled, ensure_ascii=False))])
        payload['applied'] = document['id']
    _print(payload, args)
    return 0

def cmd_score(service, args):
    if args.action in ('set', 'show') and not args.document:
        raise ValueError('score %s 需要 <doc> 文献引用;validate/aggregate 不需要。' % args.action)
    if args.action in ('import-report', 'reports') and not args.document:
        raise ValueError('score %s 需要 <doc> 文献引用。' % args.action)
    if args.action == 'aggregate':
        return _cmd_score_aggregate(service, args)
    if args.action == 'set':
        document = resolve_document(service, args.document)
        if args.detail and args.detail_file:
            raise ValueError('--detail 与 --detail-file 只能同时提供一个。')
        if args.detail_file:
            detail = _load_detail_file(args.detail_file)
        else:
            detail = args.detail or '{}'
            if len(detail.encode('utf-8')) > DETAIL_MAX_BYTES:
                raise ValueError('评分明细 JSON 不能超过 256 KiB。')
            if detail.strip():
                try:
                    json.loads(detail)
                except json.JSONDecodeError as error:
                    raise ValueError('评分明细必须是有效 JSON:%s' % error) from None
            else:
                detail = '{}'
        rationale = args.rationale or ''
        if args.rationale_file:
            rationale = Path(args.rationale_file).read_text(encoding='utf-8')
        if len(rationale.encode('utf-8')) > RATIONALE_MAX_BYTES:
            raise ValueError('评分理由必须是不超过 64 KiB 的文本。')
        entry = dict(kind=args.kind, rationale=rationale, detail=detail)
        if args.kind != 'summary':
            if args.score is None:
                raise ValueError('paper/confidence 评分需要 --score。')
            entry['score'] = args.score
        else:
            entry['score'] = None
        service.set_scores(document['id'], [entry])
        # 回执只报结果不回显明细:写入的 detail 由调用方自己生成,完整内容
        # 需要时经 `score show` 读取,避免每次写入重复输出几十 KB。
        _print(dict(id=document['id'], kind=args.kind, score=entry.get('score'),
                    detail_bytes=len(entry['detail'].encode('utf-8'))), args)
    elif args.action == 'show':
        document = resolve_document(service, args.document)
        scores = service.document_scores(document['id'])
        if getattr(args, 'summary', False):
            # Cheap reads for agents: drop embedded judge originals, keep the
            # aggregation skeleton (full text stays in `reports`/detail).
            for entry in scores.values():
                if isinstance(entry, dict):
                    for sub in (entry.get('detail') or {}).get('sub_scores') or []:
                        if isinstance(sub, dict):
                            sub.pop('original_output', None)
                            sub.pop('recheck_output', None)
        _print(scores, args)
    elif args.action == 'import-report':
        document = resolve_document(service, args.document)
        if not args.agent:
            raise ValueError('import-report 需要 --agent A(评委槽位标识)。')
        text = _load_report_text(args.detail_file) if args.detail_file else ''
        result = service.set_score_report(document['id'], args.kind, args.agent, text)
        _print(result, args)
    elif args.action == 'reports':
        document = resolve_document(service, args.document)
        _print(service.score_reports(document['id'], args.kind, agent_id=args.agent), args)
    elif args.action == 'validate':
        # Preflight a report before writing: agents avoid burning a score slot
        # on a structurally invalid aggregation.
        if args.kind == 'summary':
            raise ValueError('validate 仅支持 paper/confidence;提炼报告用 score aggregate --kind summary 校验并合并。')
        text = _load_detail_file(args.detail_file)
        report = json.loads(text) if text.strip() else None
        errors = _validate_report(args.kind, report)
        payload = dict(kind=args.kind, file=args.detail_file, valid=not errors, errors=errors)
        _print(payload, args)
        if errors:
            return 1
    return 0

# ---------------------------------------------------------------- 解析器
def cmd_verify(service, args):
    if args.depth is not None and args.action != 'begin-code':
        raise ValueError('--depth 仅用于 begin-code；基础核验使用 github，不自动升级。')
    document = resolve_document(service, args.document)
    identifier = document['id']
    if args.action == 'github':
        result = service.verify_github(identifier, args.repo, authorized=args.yes)
        _print(result, args)
        if result['status'] == 'unavailable':
            return 3
    elif args.action == 'plan':
        _print(service.code_review_plan(identifier), args)
    elif args.action == 'begin-code':
        if not args.report:
            raise ValueError('begin-code 需要 --report 指定已归档 GitHub 快照。')
        _print(service.begin_code_review(identifier, args.report, depth=args.depth, authorized=args.yes,
                                         max_files=args.max_files, max_bytes=args.max_bytes,
                                         scope=args.scope), args)
    elif args.action in ('code-status', 'code-tree', 'read-code'):
        if not args.session:
            raise ValueError('代码核验操作需要 --session 指定已确认任务。')
        if args.action == 'code-status':
            result = service.code_review_status(identifier, args.session)
        elif args.action == 'code-tree':
            result = service.code_review_tree(identifier, args.session, args.prefix, args.offset, args.limit)
        else:
            if not args.path:
                raise ValueError('read-code 需要 --path 指定目录中的文件。')
            result = service.read_code(identifier, args.session, args.path, args.start_line, args.end_line)
        _print(result, args)
    elif args.action == 'import-code':
        if args.payload is not None:
            if args.file:
                raise ValueError('import-code 只能提供 --payload 或 --file 之一。')
            try:
                payload = json.loads(args.payload)
            except (ValueError, TypeError):
                raise ValueError('--payload 必须为有效的深入核验 JSON。') from None
            result = service.submit_code_review(identifier, payload)
        else:
            result = service.import_code_review(identifier, args.file)
        _print(result, args)
    elif args.action == 'import-research':
        _print(service.import_research_report(identifier, args.file), args)
    elif args.action == 'list':
        _print(service.list_verifications(identifier), args)
    elif args.action in ('show', 'export'):
        if not args.report:
            raise ValueError('show/export 需要 --report 指定该文献的报告 ID。')
        if args.action == 'show':
            _print(service.verification_report(identifier, args.report), args)
        else:
            if not args.out:
                raise ValueError('export 需要 --out 指定本地目标文件。')
            service.export_verification(identifier, args.report, args.out)
            _print({'id': args.report, 'exported': True}, args)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog='polyscholar-cli',
        description='PolyScholar 命令行:文献库、翻译与 AI 评分的程序化入口。命令详情见 cli.md。')
    parser.add_argument('--data-dir', help='资料目录(默认标准应用目录;开发/便携模式为仓库 data/)')
    parser.add_argument('--json', action='store_true', help='以 JSON 输出结果')
    # Same flag accepted after the subcommand (agents write it there naturally).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--json', action='store_true', default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('add', help='导入 PDF 或 arXiv 论文', parents=[common])
    p.add_argument('pdf', nargs='?', help='PDF 文件路径')
    p.add_argument('--arxiv', help='arXiv 链接或编号')
    p.add_argument('--meta', help='导入后应用的元数据 JSON 文件(title/authors/year/doi/url/abstract/tags/itemType/creators)')
    p.add_argument('--collection', help='同时加入的集合名')
    p.add_argument('--quiet', action='store_true', help='精简输出:仅返回 id 与标题')
    p.set_defaults(func=cmd_add)

    p = sub.add_parser('list', help='列出文献(含评分)', parents=[common])
    p.add_argument('--quiet', action='store_true', help='--json 时仅输出 id/标题/年份/评分,减小返回体积')
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('show', help='显示文献详情;--text 附全文块(agent 阅读入口)', parents=[common])
    p.add_argument('document', help='id / id 前缀 / 唯一标题片段')
    p.add_argument('--text', action='store_true')
    p.add_argument('--pages', help='--text 时仅取页范围,如 2-5(节省 token)')
    p.set_defaults(func=cmd_show)

    p = sub.add_parser('search', help='本地全文检索(库或单篇;agent 定位原文用,免整篇重读)', parents=[common])
    p.add_argument('query', help='关键词(单行,≤4 KiB)')
    p.add_argument('--doc', help='限定单篇文献(含其附件)')
    p.add_argument('--limit', type=int, default=40, help='返回上限 1–1000(默认 40)')
    p.set_defaults(func=cmd_search)

    p = sub.add_parser('update', help='修改元数据字段', parents=[common])
    p.add_argument('document')
    p.add_argument('--title');p.add_argument('--authors');p.add_argument('--doi')
    p.add_argument('--year');p.add_argument('--url');p.add_argument('--abstract')
    p.add_argument('--tags', help='逗号分隔')
    p.set_defaults(func=cmd_update)

    p = sub.add_parser('remove', help='删除文献(保护进行中任务)', parents=[common])
    p.add_argument('document')
    p.add_argument('--yes', action='store_true', help='跳过确认(供 agent 使用)')
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser('text', help='输出 DocumentIR 全文块', parents=[common])
    p.add_argument('document')
    p.set_defaults(func=cmd_text)

    p = sub.add_parser('parse', help='解析 PDF 生成文本块(翻译/摘要/全文检索的前置)', parents=[common])
    p.add_argument('document')
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser('collection', help='集合管理', parents=[common])
    p.add_argument('action', choices=['add', 'remove', 'list', 'attach', 'detach'])
    p.add_argument('name', nargs='?', help='集合名(add/remove 时)')
    p.add_argument('document', nargs='?', help='文献引用(attach/detach 时)')
    p.add_argument('--parent', help='父集合引用(add 时)')
    p.add_argument('--collection', help='集合引用(attach/detach 时)')
    p.set_defaults(func=cmd_collection)

    p = sub.add_parser('translate', help='arXiv 论文整篇翻译为双语 HTML', parents=[common])
    p.add_argument('document')
    p.add_argument('--engine', choices=['babeldoc', 'pdfmathtranslate', 'html-llm'], help='默认使用设置中的 PDF 引擎')
    p.add_argument('--no-wait', action='store_true', help='兼容旧参数；当前拒绝无托管的后台任务')
    p.set_defaults(func=cmd_translate)

    p = sub.add_parser('jobs', help='列出翻译任务', parents=[common])
    p.set_defaults(func=cmd_jobs)

    p = sub.add_parser('export-translation', help='导出最新中文译文 HTML', parents=[common])
    p.add_argument('document')
    p.add_argument('-o', '--output', help='输出路径(默认 <id前缀>-translated.html)')
    p.set_defaults(func=cmd_export)

    p = sub.add_parser('score', help='AI 评分与提炼(set/show/validate/aggregate/import-report/reports)', parents=[common])
    p.add_argument('action', choices=['set', 'show', 'validate', 'aggregate', 'import-report', 'reports'])
    p.add_argument('document', nargs='?', help='文献引用(set/show/import-report/reports 必填)')
    p.add_argument('--kind', choices=SCORE_KINDS, help='paper/confidence 需分数;summary 为提炼')
    p.add_argument('--score', type=float, help='0–100(summary 不需要)')
    p.add_argument('--rationale', help='得分理由或提炼概览')
    p.add_argument('--rationale-file', help='从文件读取理由(长文本推荐,UTF-8)')
    p.add_argument('--detail', help='明细 JSON 字符串')
    p.add_argument('--detail-file', help='明细 JSON 文件路径(agent 推荐用法,≤256 KiB)')
    p.add_argument('--reports', nargs='+', metavar='REPORT', help='aggregate:评委报告 JSON 文件(评分三份,提炼两份)')
    p.add_argument('--recheck', nargs='+', metavar='REPORT', help='aggregate:三份复核报告(极差超限时)')
    p.add_argument('--apply', help='aggregate:直接把聚合结果写入该文献(需 --rationale)')
    p.add_argument('--agent', help='import-report/reports:评委槽位标识(如 A)')
    p.add_argument('--summary', action='store_true', help='show:剔除内嵌评委原文,精简返回')
    p.add_argument('--out', help='aggregate:同时把汇总报告写入该文件')
    p.set_defaults(func=cmd_score)

    p = sub.add_parser('rubric', help='获取打分文档 rubrics(list/show,含版本与 SHA-256)', parents=[common])
    p.add_argument('action', choices=['list', 'show'])
    p.add_argument('kind', nargs='?', choices=['paper', 'confidence'], help='show 时必填')
    p.set_defaults(func=cmd_rubric)

    p = sub.add_parser('status', help='库状态:锁占用、文献/评分/任务计数(不取锁)', parents=[common])
    p.set_defaults(func=cmd_status)

    p = sub.add_parser('verify', help='独立外部核验，绝不修改盲评分', parents=[common])
    p.add_argument('action', choices=['github', 'import-research', 'list', 'show', 'export',
                                    'plan', 'begin-code', 'code-status', 'code-tree', 'read-code', 'import-code'])
    p.add_argument('document')
    p.add_argument('--repo', help='GitHub HTTPS 仓库根 URL；只向 GitHub 发送仓库标识')
    p.add_argument('--yes', action='store_true', help='用户已确认本次基础/深入核验；agent 不得自行替用户授权')
    p.add_argument('--file', help='import-research:已由 agent 整理的来源/时间/比较条件 JSON')
    p.add_argument('--payload', help='import-code:直接提交 JSON，不需要宿主文件访问；也可用 --file')
    p.add_argument('--report', help='show/export:报告 ID')
    p.add_argument('--out', help='export:本地 JSON 目标')
    p.add_argument('--depth', choices=['basic', 'deep'], help='begin-code 必须明确为 deep；基础使用 github')
    p.add_argument('--session', help='深入核验任务 ID')
    p.add_argument('--scope', default='论文核心方法、训练配置与评测流程', help='用户确认的检查范围')
    p.add_argument('--max-files', type=int, default=12, help='深入核验最多读取文件数，默认 12，上限 32')
    p.add_argument('--max-bytes', type=int, default=262144, help='累计唯一文件字节预算，默认 256 KiB，上限 512 KiB')
    p.add_argument('--prefix', default='', help='code-tree:目录路径前缀')
    p.add_argument('--offset', type=int, default=0)
    p.add_argument('--limit', type=int, default=50)
    p.add_argument('--path', help='read-code:固定快照内的文件路径')
    p.add_argument('--start-line', type=int, default=1)
    p.add_argument('--end-line', type=int, default=80)
    p.set_defaults(func=cmd_verify)
    return parser


def _configure_stdio():
    """CLI output uses UTF8 even on redirected Windows pipes.

    即使 Windows 使用重定向管道，CLI 输出仍为 UTF8；不改变测试注入流。
    """
    for stream in (sys.stdout, sys.stderr):
        configure = getattr(stream, 'reconfigure', None)
        if callable(configure):
            configure(encoding='utf-8', errors='backslashreplace')


def main(argv=None, service=None):
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    owned = service is None
    try:
        if args.command == 'status' and service is None:
            # Deliberately runs before LocalService: a busy library is exactly
            # what status must be able to report.
            return cmd_status(None, args)
        if service is None:
            service = LocalService(args.data_dir) if args.data_dir else LocalService()
        return args.func(service, args) or 0
    except LibraryBusy as error:
        print(str(error), file=sys.stderr)
        return 2
    except NetworkError as error:
        print(str(error), file=sys.stderr)
        return 3
    except sqlite3.IntegrityError as error:
        # 数据库层约束拒绝(如旧库未迁移的 CHECK);给出可操作提示而非原始 traceback。
        print('数据库约束拒绝该操作:%s。若库来自旧版本,请重新打开应用完成迁移后重试。' % error,
              file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('已中断。', file=sys.stderr)
        return 130
    finally:
        if owned:
            try:
                service.close()
            except Exception:
                pass

if __name__ == '__main__':
    sys.exit(main())
