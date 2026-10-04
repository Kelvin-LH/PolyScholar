# SPDX-License-Identifier: AGPL-3.0-only
"""Independent external evidence and immutable local report history.

外部证据独立于盲评分；校验结构和身份，不把 agent 摘录认证为事实。
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
from urllib.parse import quote, urlsplit
import uuid

from integrations.github_snapshot import FORMAT, MAX_REPORT_BYTES, MAX_TREE_ITEMS, repository_url

VERIFICATION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS desktop_verifications(
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('github','research','code')),
    status TEXT NOT NULL CHECK(status IN ('checked','partial','unavailable','reported')),
    document_sha256 TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    data TEXT NOT NULL CHECK(json_valid(data)),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS desktop_verifications_document
    ON desktop_verifications(document_id,created_at);
'''


def verification_migration_sql(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='desktop_verifications'").fetchone()
    if not row or "'code'" in row[0]:
        return ''
    # Replace only the constrained table; preserve reports and all identities.
    # 原子更新类型约束，保留既有报告及身份；v14 尚无代码任务外键。
    return '''CREATE TABLE desktop_verifications_v15(
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK(kind IN ('github','research','code')),
        status TEXT NOT NULL CHECK(status IN ('checked','partial','unavailable','reported')),
        document_sha256 TEXT NOT NULL, report_sha256 TEXT NOT NULL,
        data TEXT NOT NULL CHECK(json_valid(data)), created_at TEXT NOT NULL);
        INSERT INTO desktop_verifications_v15 SELECT * FROM desktop_verifications;
        DROP TABLE desktop_verifications;
        ALTER TABLE desktop_verifications_v15 RENAME TO desktop_verifications;'''


def text(value, limit, label):
    if (not isinstance(value, str) or not value.strip()
            or len(value.encode('utf-8')) > limit
            or any(ord(char) < 32 and char not in '\n\r\t' for char in value)):
        raise ValueError(label + '无效或超过大小上限。')
    return value


def keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError('外部报告字段缺失或存在未知字段。')


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('日期必须为 YYYY-MM-DD。')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError('日期无效。') from None


def instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError()
        return parsed
    except (TypeError, ValueError):
        raise ValueError('核验/取回时间必须为带时区且非未来的 ISO 时间。') from None


def source_url(value):
    text(value, 2048, '来源 URL')
    parsed = urlsplit(value)
    try:
        valid = (parsed.scheme == 'https' and parsed.hostname
                 and not parsed.username and not parsed.password
                 and parsed.port in (None, 443) and not any(c.isspace() for c in value))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('来源链接必须为无凭据的 HTTPS 地址。')
    return value


def validate_research(payload, expected_hash):
    keys(payload, ('format', 'kind', 'paper_sha256', 'as_of', 'perspective',
                   'search_scope', 'sources', 'comparisons', 'benchmarks'))
    if (payload['format'] != FORMAT or payload['kind'] != 'research'
            or payload['paper_sha256'] != expected_hash):
        raise ValueError('研究报告格式或论文 SHA-256 不匹配。')
    cutoff = iso_date(payload['as_of'])
    if cutoff > datetime.now(timezone.utc).date():
        raise ValueError('研究进展截止日期不能在未来。')
    if payload['perspective'] not in ('at_publication', 'current'):
        raise ValueError('研究视角必须为 at_publication 或 current。')
    text(payload['search_scope'], 4096, '检索范围/查询说明')
    sources = payload['sources']
    if not isinstance(sources, list) or not 1 <= len(sources) <= 30:
        raise ValueError('研究报告需要 1–30 个实际来源。')
    seen = set()
    for source in sources:
        keys(source, ('id', 'url', 'title', 'published_at', 'retrieved_at', 'excerpt'))
        identifier = text(source['id'], 64, '来源 ID')
        if identifier in seen:
            raise ValueError('来源 ID 不能重复。')
        seen.add(identifier)
        source_url(source['url'])
        text(source['title'], 1024, '来源标题')
        quote = text(source['excerpt'], 4096, '来源摘录')
        retrieved = instant(source['retrieved_at'])
        published = source['published_at']
        if published is None:
            if payload['perspective'] == 'at_publication':
                raise ValueError('发表时视角要求每个来源有可核对的发布日期。')
        else:
            published = iso_date(published)
            if published > cutoff or published > retrieved.date():
                raise ValueError('来源发表于截止日或取回时间之后，不能用于本次对照。')
        source['excerpt_sha256'] = hashlib.sha256(quote.encode('utf-8')).hexdigest()
    comparisons = payload['comparisons']
    benchmarks = payload['benchmarks']
    if (not isinstance(comparisons, list) or len(comparisons) > 30
            or not isinstance(benchmarks, list) or len(benchmarks) > 30
            or not comparisons and not benchmarks):
        raise ValueError('需要主张对照或基准对照，各最多 30 条。')
    for item in comparisons:
        keys(item, ('claim', 'relation', 'basis', 'source_ids', 'explanation'))
        text(item['claim'], 2048, '论文主张')
        text(item['explanation'], 4096, '对照说明')
        if item['relation'] not in ('overlap', 'extends', 'contradicts', 'incomparable', 'uncertain'):
            raise ValueError('主张对照关系无效。')
        if item['basis'] not in ('author_report', 'agent_inference'):
            raise ValueError('对照必须区分作者报告与 agent 推断。')
        identifiers = item['source_ids']
        if (not isinstance(identifiers, list) or not identifiers
                or any(not isinstance(i, str) or i not in seen for i in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise ValueError('对照条目的来源引用无效。')
    for item in benchmarks:
        keys(item, ('task', 'dataset', 'split', 'metric', 'direction', 'candidate',
                    'reference', 'protocol', 'conditions'))
        for key in ('task', 'dataset', 'split', 'metric', 'conditions'):
            text(item[key], 2048, '基准比较条件')
        if item['direction'] not in ('higher', 'lower') or item['protocol'] not in ('comparable', 'incomparable', 'unknown'):
            raise ValueError('指标方向或比较条件状态无效。')
        for key in ('candidate', 'reference'):
            result = item[key]
            keys(result, ('value', 'source_id'))
            value = result['value']
            try:
                finite = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                finite = False
            if (not finite
                    or not isinstance(result['source_id'], str) or result['source_id'] not in seen):
                raise ValueError('基准数值或来源无效。')
        # A numeric delta is only meaningful after an explicit protocol declaration.
        # 未声明可比就不算差值；声明本身仍为 agent 判断，非程序认证。
        item['delta'] = (item['candidate']['value'] - item['reference']['value']
                         if item['protocol'] == 'comparable' else None)
        if item['delta'] is not None and not math.isfinite(item['delta']):
            raise ValueError('基准差值超过有限数值范围。')
    payload.update(status='reported', provenance='agent_report_not_independently_verified')
    return payload


def validate_github(payload):
    keys(payload, ('format', 'kind', 'checked_at', 'repository_url', 'status',
                   'error_code', 'snapshot', 'findings', 'readme', 'limitations'))
    if payload['format'] != FORMAT or payload['kind'] != 'github':
        raise ValueError('GitHub 核验报告格式无效。')
    repository = repository_url(payload['repository_url'])
    instant(payload['checked_at'])
    if payload['status'] not in ('checked', 'partial', 'unavailable'):
        raise ValueError('GitHub 核验状态无效。')
    if payload['error_code'] not in (None, 'response_limit', 'invalid_response',
                                   'rate_limited_or_forbidden', 'not_accessible',
                                   'empty_repository', 'http_error', 'network_unavailable', 'timeout'):
        raise ValueError('GitHub 核验错误码无效。')
    snapshot = payload['snapshot']
    if snapshot:
        keys(snapshot, ('commit_sha', 'tree_sha', 'committed_at', 'tree_complete',
                        'file_count', 'tree_response_sha256', 'commit_url'))
        for key in ('commit_sha', 'tree_sha'):
            if not isinstance(snapshot[key], str) or not re.fullmatch('[0-9a-f]{40}', snapshot[key]):
                raise ValueError('仓库快照 SHA 无效。')
        if snapshot['commit_url'] != repository + '/commit/' + snapshot['commit_sha']:
            raise ValueError('仓库快照来源不匹配。')
        if type(snapshot['tree_complete']) is not bool:
            raise ValueError('目录完整性状态无效。')
        instant(snapshot['committed_at'])
        count = snapshot['file_count']
        digest = snapshot['tree_response_sha256']
        if count is None:
            if digest is not None or snapshot['tree_complete'] or payload['status'] != 'partial':
                raise ValueError('缺失的目录快照不能标记为完整。')
        elif (type(count) is not int or not 0 <= count <= MAX_TREE_ITEMS
              or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('目录计数或摘要无效。')
        if payload['status'] == 'unavailable' or (payload['status'] == 'checked' and not snapshot['tree_complete']):
            raise ValueError('核验状态与目录完整性不一致。')
    elif payload['status'] != 'unavailable':
        raise ValueError('核验结果缺少提交快照。')
    findings = payload['findings']
    if not isinstance(findings, list) or len(findings) > 8:
        raise ValueError('核验观察条目无效。')
    observed_keys = set()
    for finding in findings:
        keys(finding, ('key', 'label', 'status', 'count', 'evidence'))
        if (finding['key'] not in ('source', 'training', 'inference', 'evaluation', 'dependencies', 'license', 'tests', 'ci')
                or finding['key'] in observed_keys or not snapshot or snapshot['file_count'] is None):
            raise ValueError('核验观察类型或目录身份无效。')
        observed_keys.add(finding['key'])
        text(finding['label'], 256, '观察标签')
        if finding['status'] not in ('observed', 'not_observed', 'unknown'):
            raise ValueError('核验观察状态无效。')
        if type(finding['count']) is not int or finding['count'] < 0:
            raise ValueError('核验计数无效。')
        if not isinstance(finding['evidence'], list) or len(finding['evidence']) > 5:
            raise ValueError('核验路径证据无效。')
        if (finding['count'] > snapshot['file_count']
                or len(finding['evidence']) != min(finding['count'], 5)
                or (finding['status'] == 'observed') != (finding['count'] > 0)
                or finding['status'] == 'not_observed' and not snapshot['tree_complete']):
            raise ValueError('观察结论与目录证据不一致。')
        for evidence in finding['evidence']:
            keys(evidence, ('path', 'blob_sha', 'url'))
            validate_blob(evidence, repository, snapshot)
    if payload['readme'] is not None:
        readme = payload['readme']
        keys(readme, ('path', 'blob_sha', 'sha256', 'excerpt', 'excerpt_truncated', 'url'))
        validate_blob(readme, repository, snapshot)
        text(readme['excerpt'], 16384, 'README 摘录')
        if (type(readme['excerpt_truncated']) is not bool or not isinstance(readme['sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', readme['sha256'])):
            raise ValueError('README 摘要或截断状态无效。')
    if not isinstance(payload['limitations'], list) or len(payload['limitations']) > 10:
        raise ValueError('核验边界无效。')
    for limitation in payload['limitations']:
        text(limitation, 1024, '核验边界')
    payload['provenance'] = 'public_github_api_snapshot'
    return payload


def validate_blob(evidence, repository, snapshot):
    path = text(evidence['path'], 4096, '仓库路径')
    if (path.startswith('/') or '\\' in path or any(ord(c) < 32 for c in path)
            or any(part in ('', '.', '..') for part in path.split('/'))
            or not isinstance(evidence['blob_sha'], str)
            or not re.fullmatch('[0-9a-f]{40}', evidence['blob_sha'])
            or not snapshot or evidence['url'] != repository + '/blob/' + snapshot['commit_sha'] + '/' + quote(path, safe='/')):
        raise ValueError('路径证据未绑定有效的提交快照。')


class VerificationLibrary:
    def save_verification(self, document_id, report, expected_hash):
        if not isinstance(expected_hash, str) or not re.fullmatch('[0-9a-f]{64}', expected_hash):
            raise ValueError('核验必须绑定真实 PDF 的 SHA-256。')
        payload = deepcopy(report)
        try:
            encoded_input = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (TypeError, ValueError, UnicodeError):
            raise ValueError('外部报告不是有效 UTF-8 JSON。') from None
        if len(encoded_input) > MAX_REPORT_BYTES:
            raise ValueError('外部报告不能超过 1 MiB。')
        if not isinstance(payload, dict):
            raise ValueError('外部报告必须是 JSON 对象。')
        if payload.get('kind') == 'github':
            payload = validate_github(payload)
        elif payload.get('kind') == 'research':
            payload = validate_research(payload, expected_hash)
        elif payload.get('kind') == 'code':
            pass  # Validate inside the session transaction / 在任务事务内校验。
        else:
            raise ValueError('外部报告类型必须为 github、research 或 code。')
        identifier = str(uuid.uuid4())
        created = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_active(document_id, db)
            row = db.execute('SELECT sha256 FROM desktop_documents WHERE id=?', (document_id,)).fetchone()
            if not row or row[0] != expected_hash:
                raise ValueError('文献已删除或版本改变，核验结果未保存。')
            if payload.get('kind') == 'code':
                from .code_review import validate_code_report
                session = self.code_session(document_id, payload.get('session_id'), db)
                if session['state'] != 'open':
                    raise ValueError('深入核验已完成，不能重复提交或覆盖报告。')
                payload = validate_code_report(payload, session, self.code_files(session, db))
                db.execute("UPDATE desktop_code_sessions SET state='completed' WHERE id=?", (session['session_id'],))
            data = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if len(data.encode('utf-8')) > MAX_REPORT_BYTES:
                raise ValueError('标准化后的外部报告不能超过 1 MiB。')
            digest = hashlib.sha256(data.encode('utf-8')).hexdigest()
            db.execute('INSERT INTO desktop_verifications VALUES(?,?,?,?,?,?,?,?)',
                       (identifier, document_id, payload['kind'], payload['status'],
                        expected_hash, digest, data, created))
            # The report and audit are one commit; never log queries or URLs.
            # 报告与审计同事务，审计只记固定事件，不记录查询内容或 URL。
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',
                       ('external_verification_saved', 'failed' if payload['status'] == 'unavailable' else 'succeeded', created))
        return {'id': identifier, 'kind': payload['kind'], 'status': payload['status'],
                'created_at': created, 'sha256': digest}

    def list_verifications(self, document_id, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('核验历史返回上限必须为 1–100。')
        with self.connection() as db:
            self.require_active(document_id, db)
            rows = db.execute('SELECT id,kind,status,created_at,report_sha256 FROM desktop_verifications '
                              'WHERE document_id=? ORDER BY created_at DESC,id DESC LIMIT ?',
                              (document_id, limit)).fetchall()
        return [dict(zip(('id', 'kind', 'status', 'created_at', 'sha256'), row)) for row in rows]

    def verification_report(self, document_id, report_id):
        with self.connection() as db:
            self.require_active(document_id, db)
            row = db.execute('SELECT data,report_sha256,document_sha256 FROM desktop_verifications '
                             'WHERE document_id=? AND id=?', (document_id, report_id)).fetchone()
        if not row:
            raise ValueError('该文献的核验报告不存在。')
        if hashlib.sha256(row[0].encode('utf-8')).hexdigest() != row[1]:
            raise ValueError('本地核验报告校验和不匹配。')
        return {'id': report_id, 'document_sha256': row[2], 'sha256': row[1], 'report': json.loads(row[0])}
