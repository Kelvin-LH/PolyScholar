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
from .exports import atomic_export
from .instance import LibraryLock

AUDIT_POINTS = frozenset({'document_imported','document_deleted','collection_created','collection_updated','collection_deleted','collection_membership_updated','tag_renamed','tag_removed','translation_finished','artifact_exported','citation_exported','diagnostics_exported'})

MAX_PDF = 100 * 1024 * 1024
SETTINGS = dict(endpoint='https://api.deepseek.com/v1', model='', engine='babeldoc',
                pythonPath='', cachePath='', sourceLanguage='en', targetLanguage='zh', doiEnabled=False, timeoutSeconds=600)

def timestamp():
    return datetime.now(timezone.utc).isoformat()

class LocalStore(CollectionLibrary):
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
            if version > 3:
                raise ValueError('本地数据库来自更新版本，请升级应用。')
            if version == 1:
                backup = self.root / 'library-before-v2.sqlite3'
                if not backup.exists():
                    target = sqlite3.connect(backup)
                    try:
                        db.backup(target)
                    finally:
                        target.close()
                    if os.name == 'posix':
                        backup.chmod(0o600)
            if version in (1,2):
                backup = self.root / 'library-before-v3.sqlite3'
                if not backup.exists():
                    target = sqlite3.connect(backup)
                    try:
                        db.backup(target)
                    finally:
                        target.close()
                    if os.name == 'posix':
                        backup.chmod(0o600)
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
                PRAGMA user_version=3;
            ''')
        values = ','.join("'"+point+"'" for point in sorted(AUDIT_POINTS))
        with self.connection() as db:
            for action in ('INSERT', 'UPDATE'):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS desktop_audit_validate_{action.lower()} BEFORE {action} ON desktop_audit "
                           f"WHEN NEW.point NOT IN ({values}) OR NEW.outcome NOT IN ('succeeded','failed') "
                           "BEGIN SELECT RAISE(ABORT, 'Invalid audit event'); END")
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
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM desktop_documents ORDER BY rowid DESC')]

    def document(self, document_id):
        with self.connection() as db:
            row = db.execute('SELECT data FROM desktop_documents WHERE id=?', (document_id,)).fetchone()
        if not row:
            raise ValueError('文献不存在。')
        return json.loads(row[0])

    def object_path(self, document):
        digest = document['sha256']
        if not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('无效文献哈希。')
        return self.objects / (digest + '.pdf')

    def import_pdf(self, path):
        source = Path(path)
        if not source.is_file() or source.stat().st_size > MAX_PDF:
            raise ValueError('请选择不超过 100 MiB 的 PDF 文件。')
        with source.open('rb') as stream:
            data = stream.read(MAX_PDF + 1)
        if len(data) > MAX_PDF or not data.startswith(b'%PDF-'):
            raise ValueError('PDF 文件无效或过大。')
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            existing = next((d for d in self.list_documents() if d['sha256'] == digest), None)
            if existing:
                sources=existing.get('sourcePaths',[])
                source_path=str(source.resolve())
                if source_path not in sources:
                    existing['sourcePaths']=[*sources,source_path]
                    with self.connection() as db:
                        db.execute('UPDATE desktop_documents SET data=? WHERE id=?',(json.dumps(existing,ensure_ascii=False),existing['id']))
                return existing
            document = dict(id=str(uuid.uuid4()), title=source.stem or '未命名文献', authors='', doi='', year='', tags=[], notes='',
                            sha256=digest, filename=source.name, sizeBytes=len(data), createdAt=timestamp(),sourcePaths=[str(source.resolve())])
            target = self.object_path(document)
            if not target.exists():
                temporary = self.objects / (str(uuid.uuid4()) + '.tmp')
                try:
                    with temporary.open('xb') as stream:
                        stream.write(data)
                    temporary.chmod(stat.S_IRUSR)
                    os.replace(temporary, target)
                finally:
                    if temporary.exists():
                        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
                        temporary.unlink()
            with self.connection() as db:
                db.execute('INSERT INTO desktop_documents VALUES(?,?,?)', (document['id'], digest, json.dumps(document, ensure_ascii=False)))
            self.audit('document_imported')
            return document

    def update_document(self, document_id, patch):
        fields = {'title', 'authors', 'doi', 'year', 'tags', 'notes'}
        if not isinstance(patch, dict) or set(patch) - fields:
            raise ValueError('文献修改字段无效。')
        for key, value in patch.items():
            if key == 'tags':
                patch = {**patch, 'tags': normalize_tags(value)}
            elif not isinstance(value, str):
                raise ValueError('文献元数据必须是文本。')
            elif len(value.encode('utf-8')) > 65536:
                raise ValueError('每个文献元数据字段不能超过 64 KiB。')
        with self.lock:
            document = self.document(document_id)
            document.update(patch)
            if not document['title'].strip():
                raise ValueError('标题不能为空。')
            with self.connection() as db:
                db.execute('UPDATE desktop_documents SET data=? WHERE id=?', (json.dumps(document, ensure_ascii=False), document_id))
            return document

    def delete_document(self, document_id):
        with self.lock:
            document = self.document(document_id)
            jobs = [j for j in self.list_jobs() if j['documentId'] == document_id]
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM desktop_jobs WHERE document_id=? AND state IN ('queued','running')", (document_id,)).fetchone():
                    raise ValueError('请等待当前翻译任务结束后删除。')
                db.execute('DELETE FROM desktop_jobs WHERE document_id=?', (document_id,))
                db.execute('DELETE FROM desktop_documents WHERE id=?', (document_id,))
                referenced = db.execute('SELECT 1 FROM desktop_documents WHERE sha256=?', (document['sha256'],)).fetchone()
            if not referenced:
                path = self.object_path(document)
                if os.name == 'nt' and path.exists():
                    path.chmod(stat.S_IWRITE)
                path.unlink(missing_ok=True)
            import shutil
            for job in jobs:
                output = self.output_path(job)
                # Only delete a UUID task directory, never an arbitrary stored directory.
                if output.name == job['id'] and output.parent.name == 'jobs':
                    shutil.rmtree(output, ignore_errors=True)
            self.audit('document_deleted')

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
        if merged['engine'] not in ('babeldoc', 'pdfmathtranslate'):
            raise ValueError('不支持的翻译引擎。')
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
        if engine not in ('babeldoc', 'pdfmathtranslate'):
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

    def output_path(self, job):
        return Path(job.get('outputDir') or self.root / 'jobs' / job['id'])

    def artifact_path(self, job_id, artifact_index):
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
        if not path.is_relative_to(root) or path.suffix.lower() != '.pdf' or not path.is_file():
            raise ValueError('无效 PDF 产物。')
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
