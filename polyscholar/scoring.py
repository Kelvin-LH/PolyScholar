# SPDX-License-Identifier: AGPL-3.0-only
"""Local agent review storage; scores describe reports, not scientific truth.
本地代理评阅存储；评分记录报告意见，不判断科学结论真伪。
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from .scoring_review import uses_item_review, validate_paper_aggregation, validate_paper_items

SCORE_REPORT_MAX_BYTES = 1024 * 1024
SCORE_KINDS = ('paper', 'confidence', 'summary')
SCORES_COLUMNS = """document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('paper','confidence','summary')),
    score REAL CHECK(score IS NULL OR (score>=0 AND score<=100)),
    rationale TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(document_id,kind)"""
SCORE_SCHEMA = 'CREATE TABLE IF NOT EXISTS desktop_scores(' + SCORES_COLUMNS + ');' + """
CREATE INDEX IF NOT EXISTS desktop_scores_rank ON desktop_scores(kind,score);
CREATE TABLE IF NOT EXISTS desktop_score_reports(
    document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('paper','confidence','summary')),
    agent_id TEXT NOT NULL, sha256 TEXT NOT NULL, data TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY(document_id,kind,agent_id));
"""


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class ScoringLibrary:
    """Shared persistence for GUI/CLI reviews, with source-preserving lifecycle.
    GUI/CLI 共用评阅存储；回收站保留记录，永久清理通过外键级联删除。
    Bibliographic merging keeps source reviews attached to source identities.
    书目合并保留源记录身份及其评阅，不把不同材料版本的评分混为一份。
    """

    @staticmethod
    def scoring_migration_sql(db):
        """Rebuild only the old two-kind score constraint, preserving every row.
        仅重建历史两类型评分约束，原样保留所有行；不删除证据摘要表。
        """
        row = db.execute("SELECT sql FROM sqlite_master WHERE name='desktop_scores'").fetchone()
        if not row or "'summary'" in row[0]:
            return ''
        return ('CREATE TABLE desktop_scores_v13(' + SCORES_COLUMNS + ');'
                'INSERT INTO desktop_scores_v13 SELECT document_id,kind,score,rationale,detail,created_at,updated_at FROM desktop_scores;'
                'DROP TABLE desktop_scores;'
                'ALTER TABLE desktop_scores_v13 RENAME TO desktop_scores;')

    def scores_for_documents(self):
        """Fetch all list scores in one query; 单次查询供列表使用，避免逐条 IO。"""
        with self.connection() as db:
            return self._score_map(db)

    @staticmethod
    def _score_map(db):
        """Return list-sort values; 返回按文献归组的列表排序评分。"""
        result = {}
        for document_id, kind, score in db.execute('SELECT document_id,kind,score FROM desktop_scores'):
            result.setdefault(document_id, {})[kind] = score
        return result

    def set_scores(self, document_id, entries):
        """Upsert agent scores. entries: [{'kind','score','rationale','detail'}].

        GUI and CLI share this atomic validation/write path.
        界面与命令行共用原子校验及写入路径；不能写入回收站记录。
        detail is the machine-readable sub-agent report (dimensions, strengths,
        weaknesses, model, rubric version); rationale is the human-readable
        得分/失分理由. Existing rows for the same kind are replaced.
        """
        self.document(document_id)
        if not isinstance(entries, list) or not 1 <= len(entries) <= 3:
            raise ValueError('评分条目无效。')
        prepared = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) - {'kind', 'score', 'rationale', 'detail'}:
                raise ValueError('评分字段无效。')
            kind = entry.get('kind')
            if kind not in SCORE_KINDS:
                raise ValueError('评分类型必须是 paper、confidence 或 summary。')
            if kind in seen:
                raise ValueError('同一批次不能重复提交评分类型。')
            seen.add(kind)
            score = entry.get('score')
            if score is not None:
                if isinstance(score, bool) or not isinstance(score, (int, float)) or (not math.isfinite(score) or not 0 <= score <= 100):
                    raise ValueError('分数必须在 0–100 之间。')
                score = float(score)
            rationale = entry.get('rationale', '')
            detail = entry.get('detail', '{}')
            if not isinstance(rationale, str) or len(rationale.encode('utf-8')) > 65536:
                raise ValueError('评分理由必须是不超过 64 KiB 的文本。')
            if not isinstance(detail, str) or len(detail.encode('utf-8')) > 262144:
                raise ValueError('评分明细 JSON 不能超过 256 KiB。')
            if detail.strip():
                try:
                    parsed = json.loads(detail)
                except json.JSONDecodeError:
                    raise ValueError('评分明细必须是有效 JSON。') from None
            else:
                detail = '{}'
                parsed = {}
            # Validate new reports before any write; legacy rows keep their contract.
            # 新报告在事务前完整校验；历史记录不静默转换，整批失败不写入。
            if isinstance(parsed, dict) and uses_item_review(kind, parsed):
                errors = validate_paper_items(parsed) + validate_paper_aggregation(parsed)
                if score != parsed.get('total'):
                    errors.append('存储分数与汇总 total 不一致。')
                if errors:
                    raise ValueError('论文 1.1.0 报告无效：' + ';'.join(errors))
            prepared.append((document_id, kind, score, rationale, detail))
        with self.lock:
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                self.require_active(document_id, db)
                for document_id, kind, score, rationale, detail in prepared:
                    db.execute('INSERT INTO desktop_scores(document_id,kind,score,rationale,detail,created_at,updated_at) '
                               'VALUES(?,?,?,?,?,?,?) '
                               'ON CONFLICT(document_id,kind) DO UPDATE SET score=excluded.score,'
                               'rationale=excluded.rationale,detail=excluded.detail,updated_at=excluded.updated_at',
                               (document_id, kind, score, rationale, detail, timestamp(), timestamp()))
                db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',
                           ('score_updated', 'succeeded', timestamp()))
        return self.document_scores(prepared[0][0])

    def document_scores(self, document_id):
        """Read persisted reviews including restored entries; 读取持久化评阅及恢复记录。"""
        self.document(document_id)
        result = {kind: None for kind in SCORE_KINDS}
        with self.connection() as db:
            for kind, score, rationale, detail in db.execute(
                    'SELECT kind,score,rationale,detail FROM desktop_scores WHERE document_id=?', (document_id,)):
                result[kind] = dict(score=score, rationale=rationale, detail=json.loads(detail) if detail else {})
        return result

    def set_score_report(self, document_id, kind, agent_id, data_text):
        """Archive one blind-review agent report verbatim, keyed by agent slot.

        评委原文按 (document, kind, agent) 归档,与 desktop_scores 的分数行分离:
        detail 里内嵌的 original_output 受 256 KiB 限制,单份归档上限 1 MiB,
        保存后可经 `score reports` 原样读回。
        """
        self.document(document_id)
        if kind not in SCORE_KINDS:
            raise ValueError('评分类型必须是 paper、confidence 或 summary。')
        if not isinstance(agent_id, str):
            raise ValueError('agent 标识必须是文本。')
        agent = agent_id.strip()
        if not agent or len(agent) > 64 or any(ch in agent for ch in '\r\n\t'):
            raise ValueError('agent 标识必须是 1–64 个可见字符。')
        if not isinstance(data_text, str) or not data_text.strip():
            raise ValueError('报告必须是非空 JSON 文本。')
        if len(data_text.encode('utf-8')) > SCORE_REPORT_MAX_BYTES:
            raise ValueError('单份评委报告不能超过 1 MiB。')
        try:
            parsed = json.loads(data_text)
        except json.JSONDecodeError:
            raise ValueError('评委报告必须是有效 JSON。') from None
        if not isinstance(parsed, dict):
            raise ValueError('评委报告必须是 JSON 对象。')
        digest = hashlib.sha256(data_text.encode('utf-8')).hexdigest()
        with self.lock:
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                self.require_active(document_id, db)
                db.execute('INSERT INTO desktop_score_reports(document_id,kind,agent_id,sha256,data,created_at) '
                           'VALUES(?,?,?,?,?,?) ON CONFLICT(document_id,kind,agent_id) DO UPDATE SET '
                           'sha256=excluded.sha256,data=excluded.data,created_at=excluded.created_at',
                           (document_id, kind, agent, digest, data_text, timestamp()))
                db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',
                           ('score_report_imported', 'succeeded', timestamp()))
        return dict(documentId=document_id, kind=kind, agentId=agent, sha256=digest,
                    bytes=len(data_text.encode('utf-8')))

    def score_reports(self, document_id, kind, agent_id=None):
        """List archived reports and optional agent JSON; 列出报告及指定评委的原文数据。"""
        self.document(document_id)
        if kind not in SCORE_KINDS:
            raise ValueError('评分类型必须是 paper、confidence 或 summary。')
        query = ('SELECT agent_id,sha256,data,created_at FROM desktop_score_reports '
                 'WHERE document_id=? AND kind=?')
        parameters = [document_id, kind]
        if agent_id:
            query += ' AND agent_id=?'
            parameters.append(agent_id.strip())
        query += ' ORDER BY agent_id'
        rows = []
        with self.connection() as db:
            for agent, digest, data, created in db.execute(query, parameters):
                row = dict(agentId=agent, sha256=digest, bytes=len(data.encode('utf-8')), createdAt=created)
                if agent_id:
                    row['data'] = json.loads(data)
                rows.append(row)
        return rows
