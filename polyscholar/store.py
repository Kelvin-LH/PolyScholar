# SPDX-License-Identifier: AGPL-3.0-only
"""Local SQLite library and immutable content-addressed PDF objects."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
from urllib.parse import urlsplit
import uuid
from .library import CollectionLibrary, normalize_tags
from .document_ir import DocumentIRLibrary, IR_SCHEMA
from .attachments import AttachmentLibrary, ATTACHMENT_SCHEMA
from .exports import atomic_export
from .instance import LibraryLock
from .searches import SearchLibrary, SEARCH_SCHEMA
from .fulltext import FulltextLibrary, FULLTEXT_SCHEMA, index_current_ir
from .metadata import BIB_FIELDS, metadata_patch

AUDIT_POINTS = frozenset({'document_imported','document_deleted','document_parsed','collection_created','collection_updated','collection_deleted','collection_membership_updated','tag_renamed','tag_removed','translation_finished','job_deleted','artifact_exported','citation_exported','diagnostics_exported','saved_search_created','saved_search_updated','saved_search_deleted','fulltext_index_cleared','fulltext_index_rebuilt','score_updated','score_report_imported'})

MAX_PDF = 100 * 1024 * 1024
SCORE_REPORT_MAX_BYTES = 1024 * 1024

# desktop_scores 的列与约束只有这一份定义:新建库与迁移重建共用同一份文本。
# 此前的回归正是定义漂移造成的——代码里加了 summary 类型,但老库的既有表
# 不会因 CREATE TABLE IF NOT EXISTS 而更新约束,迁移必须显式重建。
SCORES_COLUMNS = "document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,\n                    kind TEXT NOT NULL CHECK(kind IN ('paper','confidence','summary')),\n                    score REAL CHECK(score IS NULL OR (score>=0 AND score<=100)),\n                    rationale TEXT NOT NULL DEFAULT '',\n                    detail TEXT NOT NULL DEFAULT '{}',\n                    created_at TEXT NOT NULL,\n                    updated_at TEXT NOT NULL,\n                    PRIMARY KEY(document_id,kind)"
DESKTOP_SCORES_DDL = ('CREATE TABLE IF NOT EXISTS desktop_scores(' + SCORES_COLUMNS + ');'
                      'CREATE INDEX IF NOT EXISTS desktop_scores_rank ON desktop_scores(kind,score);')
SCHEMA_VERSION = 12
SETTINGS = dict(endpoint='https://api.deepseek.com/v1', model='', engine='html-llm',
                pythonPath='', cachePath='', sourceLanguage='en', targetLanguage='zh', doiEnabled=False, timeoutSeconds=600, keyStorage='')

def timestamp():
    return datetime.now(timezone.utc).isoformat()

class LocalStore(FulltextLibrary, SearchLibrary, CollectionLibrary, DocumentIRLibrary, AttachmentLibrary):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name == 'posix':
            self.root.chmod(0o700)
        self.lock = threading.RLock()
        self._instance = LibraryLock(self.root)
        try:
            self._initialize()
        except Exception:
            self.close()
            raise

    def close(self):
        with self.lock:
            self._instance.close()

    def _initialize(self):
        self.objects = self.root / 'objects'
        self.objects.mkdir(exist_ok=True)
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError('本地数据库来自更新版本，请升级应用。')
            # 每个历史版本在升级前各留一份全库备份;循环取代逐版本复制的块,
            # 新增迁移时只需提升 SCHEMA_VERSION 并追加对应的数据搬迁步骤。
            for target in range(2, SCHEMA_VERSION + 1):
                if not 0 < version < target:
                    continue
                backup = self.root / ('library-before-v%d.sqlite3' % target)
                if not backup.exists():
                    handle = sqlite3.connect(backup)
                    try:
                        db.backup(handle)
                    finally:
                        handle.close()
                    if os.name == 'posix':
                        backup.chmod(0o600)
            if 0 < version < 11:
                # v11 removes the evidence-summary feature; saved claims only
                # survive in the pre-migration backup, never silently deleted.
                db.executescript('DROP TABLE IF EXISTS desktop_claim_provenance;'
                                 'DROP TABLE IF EXISTS desktop_model_summaries;'
                                 'DROP TABLE IF EXISTS desktop_claim_evidence;'
                                 'DROP TABLE IF EXISTS desktop_claims;')
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS desktop_documents(id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS desktop_settings(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS desktop_jobs(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES desktop_documents(id), state TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS desktop_audit(sequence INTEGER PRIMARY KEY AUTOINCREMENT, point TEXT NOT NULL, outcome TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS desktop_collections(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    parent_id TEXT REFERENCES desktop_collections(id) ON DELETE CASCADE);
                CREATE UNIQUE INDEX IF NOT EXISTS desktop_collection_names ON desktop_collections(COALESCE(parent_id,''),name);
                CREATE TABLE IF NOT EXISTS desktop_memberships(document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
                    collection_id TEXT NOT NULL REFERENCES desktop_collections(id) ON DELETE CASCADE,
                    PRIMARY KEY(document_id,collection_id));
                CREATE INDEX IF NOT EXISTS desktop_memberships_collection ON desktop_memberships(collection_id);
                '''+DESKTOP_SCORES_DDL+'''
                CREATE TABLE IF NOT EXISTS desktop_score_reports(
                    document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK(kind IN ('paper','confidence','summary')),
                    agent_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(document_id,kind,agent_id));
            ''')
            db.executescript('BEGIN IMMEDIATE;\n' + IR_SCHEMA + '\n' + ATTACHMENT_SCHEMA + '\n' + SEARCH_SCHEMA + '\n' + FULLTEXT_SCHEMA)
            if version < 8:
                for row in db.execute('SELECT document_id FROM desktop_ir_current').fetchall():
                    index_current_ir(db, row[0])
            values = ','.join("'"+point+"'" for point in sorted(AUDIT_POINTS))
            for action in ('INSERT', 'UPDATE'):
                db.execute(f'DROP TRIGGER IF EXISTS desktop_audit_validate_{action.lower()}')
                db.execute(f"CREATE TRIGGER IF NOT EXISTS desktop_audit_validate_{action.lower()} BEFORE {action} ON desktop_audit "
                           f"WHEN NEW.point NOT IN ({values}) OR NEW.outcome NOT IN ('succeeded','failed') "
                           "BEGIN SELECT RAISE(ABORT, 'Invalid audit event'); END")
            if 0 < version < 12:
                # v12 重建 desktop_scores:summary 的 CHECK 是定义后来才加的,而
                # CREATE TABLE IF NOT EXISTS 不会改既有表的约束,旧库会拒绝提炼
                # 写入。列数据原样搬迁;升级前的全量备份为 library-before-v12。
                db.executescript('CREATE TABLE desktop_scores_v12(' + SCORES_COLUMNS + ');'
                                 'INSERT INTO desktop_scores_v12 SELECT '
                                 'document_id,kind,score,rationale,detail,created_at,updated_at FROM desktop_scores;'
                                 'DROP TABLE desktop_scores;'
                                 'ALTER TABLE desktop_scores_v12 RENAME TO desktop_scores;'
                                 'DROP INDEX IF EXISTS desktop_scores_rank;'
                                 'CREATE INDEX desktop_scores_rank ON desktop_scores(kind,score);')
            db.execute('PRAGMA user_version=12')
        for job in self.list_jobs():
            if job['state'] in ('queued', 'running'):
                job.update(state='failed', error='上次退出时任务未完成，请重新提交；远程请求可能已计费。')
                self.put_job(job)

    @contextmanager
    def connection(self):
        if self._instance.fd is None:
            raise ValueError('文献库已关闭，请重新打开应用。')
        db = sqlite3.connect(self.root / 'library.sqlite3', timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def audit(self, point, outcome='succeeded'):
        if point not in AUDIT_POINTS or outcome not in ('succeeded','failed'):
            raise ValueError('无效的本地审计事件。')
        with self.connection() as db:
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)', (point, outcome, timestamp()))

    def list_documents(self):
        with self.connection() as db:
            documents = [json.loads(row[0]) for row in db.execute('SELECT data FROM desktop_documents ORDER BY rowid DESC')]
            scores = dict(self._score_map(db))
        for document in documents:
            document['scores'] = scores.get(document['id'], {})
        return documents

    @staticmethod
    def _score_map(db):
        """{document_id: {'paper': 87.5, 'confidence': None}} for list sorting."""
        result = {}
        for document_id, kind, score in db.execute('SELECT document_id,kind,score FROM desktop_scores'):
            result.setdefault(document_id, {})[kind] = score
        return result

    def set_scores(self, document_id, entries):
        """Upsert agent scores. entries: [{'kind','score','rationale','detail'}].

        detail is the machine-readable sub-agent report (dimensions, strengths,
        weaknesses, model, rubric version); rationale is the human-readable
        得分/失分理由. Existing rows for the same kind are replaced.
        """
        self.document(document_id)
        if not isinstance(entries, list) or not 1 <= len(entries) <= 3:
            raise ValueError('评分条目无效。')
        prepared = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) - {'kind', 'score', 'rationale', 'detail'}:
                raise ValueError('评分字段无效。')
            kind = entry.get('kind')
            if kind not in ('paper', 'confidence', 'summary'):
                raise ValueError('评分类型必须是 paper、confidence 或 summary。')
            score = entry.get('score')
            if score is not None:
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
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
                    json.loads(detail)
                except json.JSONDecodeError:
                    raise ValueError('评分明细必须是有效 JSON。') from None
            else:
                detail = '{}'
            prepared.append((document_id, kind, score, rationale, detail))
        with self.lock:
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                for document_id, kind, score, rationale, detail in prepared:
                    db.execute('INSERT INTO desktop_scores(document_id,kind,score,rationale,detail,created_at,updated_at) '
                               'VALUES(?,?,?,?,?,?,?) '
                               'ON CONFLICT(document_id,kind) DO UPDATE SET score=excluded.score,'
                               'rationale=excluded.rationale,detail=excluded.detail,updated_at=excluded.updated_at',
                               (document_id, kind, score, rationale, detail, timestamp(), timestamp()))
            self.audit('score_updated')
        return self.document_scores(prepared[0][0])

    def document_scores(self, document_id):
        result = {'paper': None, 'confidence': None, 'summary': None}
        with self.connection() as db:
            for kind, score, rationale, detail in db.execute(
                    'SELECT kind,score,rationale,detail FROM desktop_scores WHERE document_id=?', (document_id,)):
                result[kind] = dict(score=score, rationale=rationale, detail=json.loads(detail) if detail else {})
        return result

    def set_score_report(self, document_id, kind, agent_id, data_text):
        """Archive one blind-review agent report verbatim, keyed by agent slot.

        评委原文按 (document, kind, agent) 归档,与 desktop_scores 的分数行分离:
        detail 里内嵌的 original_output 受 256 KiB 限制,归档表不设实质上限,
        保存后可经 `score reports` 原样读回。
        """
        self.document(document_id)
        if kind not in ('paper', 'confidence', 'summary'):
            raise ValueError('评分类型必须是 paper、confidence 或 summary。')
        agent = (agent_id or '').strip()
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
                db.execute('INSERT INTO desktop_score_reports(document_id,kind,agent_id,sha256,data,created_at) '
                           'VALUES(?,?,?,?,?,?) ON CONFLICT(document_id,kind,agent_id) DO UPDATE SET '
                           'sha256=excluded.sha256,data=excluded.data,created_at=excluded.created_at',
                           (document_id, kind, agent, digest, data_text, timestamp()))
        self.audit('score_report_imported')
        return dict(documentId=document_id, kind=kind, agentId=agent, sha256=digest,
                    bytes=len(data_text.encode('utf-8')))

    def score_reports(self, document_id, kind, agent_id=None):
        """List archived reports; with agent_id, return the verbatim text too."""
        if kind not in ('paper', 'confidence', 'summary'):
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

    def document(self, document_id):
        with self.connection() as db:
            row = db.execute('SELECT data FROM desktop_documents WHERE id=?', (document_id,)).fetchone()
            owner = db.execute('SELECT parent_document_id FROM desktop_attachment_links WHERE child_document_id=?', (document_id,)).fetchone()
        if not row:
            raise ValueError('文献不存在。')
        document = json.loads(row[0])
        if owner:
            document['parentDocumentId'] = owner[0]
        return document

    def object_path(self, document):
        digest = document['sha256']
        if not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('无效文献哈希。')
        return self.objects / (digest + '.pdf')

    def import_pdf(self, path):
        return self._import_pdf(path)

    def _import_pdf(self, path, parent_id=None, role=None):
        source = Path(path)
        if not source.is_file() or source.stat().st_size > MAX_PDF:
            raise ValueError('请选择不超过 100 MiB 的 PDF 文件。')
        with source.open('rb') as stream:
            data = stream.read(MAX_PDF + 1)
        if len(data) > MAX_PDF or not data.startswith(b'%PDF-'):
            raise ValueError('PDF 文件无效或过大。')
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            target = self.objects / (digest + '.pdf')
            created_object = False
            try:
                with self.connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if parent_id is not None:
                        self._require_root(db, parent_id)
                    row = db.execute('SELECT data FROM desktop_documents WHERE sha256=?', (digest,)).fetchone()
                    if row:
                        existing = json.loads(row[0])
                        owner = db.execute('SELECT parent_document_id FROM desktop_attachment_links WHERE child_document_id=?',
                                           (existing['id'],)).fetchone()
                        if parent_id is not None:
                            if existing['id'] == parent_id:
                                raise ValueError('该 PDF 已是条目的原始文献。')
                            if not owner or owner[0] != parent_id:
                                raise ValueError('该 PDF 已属于另一文献条目，不能重复归属。')
                        sources = existing.get('sourcePaths', [])
                        source_path = str(source.resolve())
                        if source_path not in sources:
                            existing['sourcePaths'] = [*sources, source_path]
                            db.execute('UPDATE desktop_documents SET data=? WHERE id=?',
                                       (json.dumps(existing, ensure_ascii=False), existing['id']))
                        return existing
                    document = dict(id=str(uuid.uuid4()), title=source.stem or '未命名文献', authors='', doi='', year='', tags=[], notes='',
                                    sha256=digest, filename=source.name, sizeBytes=len(data), createdAt=timestamp(),
                                    sourcePaths=[str(source.resolve())])
                    if not target.exists():
                        temporary = self.objects / (str(uuid.uuid4()) + '.tmp')
                        try:
                            with temporary.open('xb') as stream:
                                stream.write(data)
                                stream.flush()
                                os.fsync(stream.fileno())
                            temporary.chmod(stat.S_IRUSR)
                            os.replace(temporary, target)
                            created_object = True
                        finally:
                            if temporary.exists():
                                temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
                                temporary.unlink()
                    db.execute('INSERT INTO desktop_documents VALUES(?,?,?)',
                               (document['id'], digest, json.dumps(document, ensure_ascii=False)))
                    if parent_id is not None:
                        db.execute('INSERT INTO desktop_attachment_links VALUES(?,?,?,?)',
                                   (parent_id, document['id'], role, source.name[:1024]))
                    db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',
                               ('document_imported', 'succeeded', timestamp()))
                return document
            except Exception:
                # The database transaction rolls back both document and link. Only
                # remove an object created here, never an existing immutable PDF.
                if created_object:
                    if os.name == 'nt':
                        target.chmod(stat.S_IWRITE)
                    target.unlink(missing_ok=True)
                raise

    def update_document(self, document_id, patch):
        fields = {'title', 'authors', 'doi', 'year', 'tags', 'notes', 'itemType', 'creators', 'abstract', 'url', *BIB_FIELDS}
        if not isinstance(patch, dict) or set(patch) - fields:
            raise ValueError('文献修改字段无效。')
        for key, value in patch.items():
            if key == 'creators':
                continue
            if key == 'tags':
                patch = {**patch, 'tags': normalize_tags(value)}
            elif not isinstance(value, str):
                raise ValueError('文献元数据必须是文本。')
            elif len(value.encode('utf-8')) > 65536:
                raise ValueError('每个文献元数据字段不能超过 64 KiB。')
        with self.lock:
            document = self.document(document_id)
            document = metadata_patch(document, patch)
            if not document['title'].strip():
                raise ValueError('标题不能为空。')
            with self.connection() as db:
                db.execute('UPDATE desktop_documents SET data=? WHERE id=?', (json.dumps(document, ensure_ascii=False), document_id))
            return document

    def delete_document(self, document_id):
        with self.lock:
            with self.connection() as db:
                self._require_root(db, document_id)
                children = [row[0] for row in db.execute(
                    'SELECT child_document_id FROM desktop_attachment_links WHERE parent_document_id=?', (document_id,))]
            self._delete_documents([document_id, *children])

    def _delete_documents(self, document_ids):
        with self.lock:
            documents = [self.document(identifier) for identifier in document_ids]
            jobs = [j for j in self.list_jobs() if j['documentId'] in document_ids]
            slots = ','.join('?' for _ in document_ids)
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute(f"SELECT 1 FROM desktop_jobs WHERE document_id IN ({slots}) AND state IN ('queued','running')",
                              document_ids).fetchone():
                    raise ValueError('请等待当前翻译任务结束后删除。')
                db.execute(f'DELETE FROM desktop_jobs WHERE document_id IN ({slots})', document_ids)
                db.execute(f'DELETE FROM desktop_documents WHERE id IN ({slots})', document_ids)
                unused = [document for document in documents if not db.execute(
                    'SELECT 1 FROM desktop_documents WHERE sha256=?', (document['sha256'],)).fetchone()]
                db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',
                           ('document_deleted', 'succeeded', timestamp()))
            for document in unused:
                path = self.object_path(document)
                if os.name == 'nt' and path.exists():
                    path.chmod(stat.S_IWRITE)
                path.unlink(missing_ok=True)
            import shutil
            for job in jobs:
                output = self.output_path(job)
                if output.name == job['id'] and output.parent.name == 'jobs':
                    shutil.rmtree(output, ignore_errors=True)

    def read_pdf(self, document_id):
        return self.read_bounded_pdf(self.object_path(self.document(document_id)))

    @staticmethod
    def read_bounded_pdf(path):
        try:
            with Path(path).open('rb') as stream:
                data = stream.read(MAX_PDF + 1)
        except OSError:
            raise ValueError('PDF 不存在、已被清理或不可读取。') from None
        if len(data) > MAX_PDF or not data.startswith(b'%PDF-'):
            raise ValueError('PDF 无效或超过 100 MiB。')
        return data

    def get_settings(self):
        with self.connection() as db:
            row = db.execute('SELECT data FROM desktop_settings WHERE id=1').fetchone()
        return {**SETTINGS, **(json.loads(row[0]) if row else {})}

    def prepare_cache(self, configured):
        path = Path(configured) if configured.strip() else self.root / 'cache'
        if not path.is_absolute():
            raise ValueError('缓存目录必须是本机绝对路径。')
        path.mkdir(parents=True, exist_ok=True)
        path = path.resolve()
        if path.is_relative_to(self.objects.resolve()):
            raise ValueError('缓存不能位于源文献对象库。')
        if os.name == 'posix':
            # A private child is replaceable beneath an untrusted writable parent.
            # Root-owned sticky /tmp remains safe for an owned child; shared 0777
            # directories without sticky protection must not hold private outputs.
            for ancestor in (path, *path.parents):
                info = ancestor.stat()
                if info.st_uid not in (0, os.getuid()):
                    raise ValueError('缓存路径由其他用户控制，请选择自己的本地文件夹。')
                if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
                    raise ValueError('缓存路径允许其他用户替换文件，请选择非共享的本地文件夹。')
        # Only tighten our own subtree; never chmod a user-selected shared folder.
        jobs = path / 'jobs'
        if jobs.is_symlink():
            raise ValueError('缓存任务目录不能是符号链接。')
        jobs.mkdir(mode=0o700, exist_ok=True)
        if os.name == 'posix':
            if jobs.stat().st_uid != os.getuid():
                raise ValueError('缓存任务目录不属于当前用户。')
            jobs.chmod(0o700)
        probe = jobs / ('.polyscholar-write-' + str(uuid.uuid4()))
        try:
            with probe.open('xb'):
                pass
            probe.unlink()
        except OSError:
            raise ValueError('缓存目录不可写。') from None
        return path

    def save_settings(self, settings):
        if not isinstance(settings, dict) or set(settings) - set(SETTINGS):
            raise ValueError('设置字段无效；不得保存 API 密钥。')
        merged = {**self.get_settings(), **settings}
        for key, value in merged.items():
            if key == 'timeoutSeconds':
                if type(value) is not int or not 1 <= value <= 86400:
                    raise ValueError('任务超时必须为 1 到 86400 秒。')
            elif key == 'doiEnabled':
                if type(value) is not bool:
                    raise ValueError('DOI 开关无效。')
            elif not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 32 for c in value):
                raise ValueError('设置参数无效。')
        if merged['keyStorage'] not in ('', 'os', 'file'):
            raise ValueError('密钥存储方式无效。')
        try:
            endpoint = urlsplit(merged['endpoint'])
            local = endpoint.hostname in ('localhost', '127.0.0.1', '::1')
            valid = endpoint.hostname and (endpoint.scheme == 'https' or (local and endpoint.scheme == 'http'))
            valid = valid and not (endpoint.username or endpoint.password or endpoint.query or endpoint.fragment)
            endpoint.port  # Validate malformed port specifications.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('模型地址必须是无凭据、查询参数和片段的 HTTPS 地址，本机环回可用 HTTP。')
        for key in ('sourceLanguage', 'targetLanguage'):
            if not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})*', merged[key]):
                raise ValueError('语言代码无效。')
        merged['cachePath'] = str(self.prepare_cache(merged['cachePath']))
        with self.connection() as db:
            db.execute('INSERT INTO desktop_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (json.dumps(merged),))
        return merged

    def list_jobs(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM desktop_jobs ORDER BY rowid DESC')]

    def put_job(self, job):
        with self.connection() as db:
            db.execute('INSERT INTO desktop_jobs VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,data=excluded.data',
                       (job['id'], job['documentId'], job['state'], json.dumps(job, ensure_ascii=False)))

    def new_job(self, document_id, engine):
        if engine not in ('html-llm', 'babeldoc', 'pdfmathtranslate'):
            raise ValueError('不支持的翻译引擎。')
        self.document(document_id)
        identifier = str(uuid.uuid4())
        output = self.prepare_cache(self.get_settings()['cachePath']) / 'jobs' / identifier
        job = dict(id=identifier, documentId=document_id, engine=engine, state='queued', createdAt=timestamp(),
                   error=None, artifacts=[], outputDir=str(output), timeoutSeconds=self.get_settings()['timeoutSeconds'])
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM desktop_jobs WHERE state IN ('queued','running')").fetchone():
                raise ValueError('首版同时仅运行一个翻译任务，请等待当前任务结束。')
            db.execute('INSERT INTO desktop_jobs VALUES(?,?,?,?)', (identifier, document_id, 'queued', json.dumps(job)))
        return job

    def delete_job(self, job_id):
        """Remove a finished job record and its owned output directory.

        Active (queued/running) jobs are refused; the UUID guard keeps the
        deletion from ever touching an arbitrary directory stored in the record.
        """
        with self.lock:
            job = next((j for j in self.list_jobs() if j['id'] == job_id), None)
            if not job:
                raise ValueError('任务记录不存在。')
            if job['state'] in ('queued', 'running'):
                raise ValueError('任务仍在进行中，结束后再删除记录。')
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                cursor = db.execute('DELETE FROM desktop_jobs WHERE id=? AND state NOT IN (?,?)',
                                    (job_id, 'queued', 'running'))
                if cursor.rowcount != 1:
                    raise ValueError('任务仍在进行中，结束后再删除记录。')
            output = self.output_path(job)
            if output.name == job['id'] and output.parent.name == 'jobs':
                import shutil
                shutil.rmtree(output, ignore_errors=True)
            self.audit('job_deleted')
            return True

    def output_path(self, job):
        return Path(job.get('outputDir') or self.root / 'jobs' / job['id'])

    def artifact_path(self, job_id, artifact_index, kinds=('.pdf',)):
        job = next((j for j in self.list_jobs() if j['id'] == job_id), None)
        if not job or job['state'] != 'completed':
            raise ValueError('翻译任务尚未成功完成。')
        if type(artifact_index) is not int or not 0 <= artifact_index < len(job['artifacts']):
            raise ValueError('译文产物不存在。')
        name = job['artifacts'][artifact_index]
        if not isinstance(name, str) or Path(name).name != name or name in ('.', '..') or '/' in name or '\\' in name:
            raise ValueError('产物名称无效。')
        try:
            root = self.output_path(job).resolve(strict=True)
            candidate = root / name
            if candidate.is_symlink():
                raise ValueError('产物不能是符号链接。')
            path = candidate.resolve(strict=True)
        except OSError:
            raise ValueError('任务产物不存在或已被清理。') from None
        if not path.is_relative_to(root) or path.suffix.lower() not in kinds or not path.is_file():
            raise ValueError('无效译文产物。')
        if path.stat().st_size > MAX_PDF:
            raise ValueError('译文超过 100 MiB。')
        return path

    def write_export(self, destination, data, extra_protected=()):
        documents=self.list_documents()
        originals=[path for document in documents for path in document.get('sourcePaths',[])]
        protected=[*self.objects.glob('*.pdf'), *self.root.glob('*.sqlite3*')]
        for job in self.list_jobs():
            protected.extend(self.output_path(job).glob('*.pdf'))
        return atomic_export(destination,data,
            protected_roots=[self.root,*extra_protected,*[self.output_path(job) for job in self.list_jobs()]],
            protected_files=protected, original_paths=originals,original_hashes={document['sha256'] for document in documents})

    def export_translation(self, job_id, artifact_index, destination):
        source=self.artifact_path(job_id,artifact_index)
        data=self.read_bounded_pdf(source)
        self.write_export(destination,data)
        self.audit('artifact_exported')
