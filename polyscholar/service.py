# SPDX-License-Identifier: AGPL-3.0-only
"""Python desktop application services. Direct calls, no local web service."""
import json
import math
import platform
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .store import LocalStore
from .metadata import exchange_metadata
from . import arxiv as arxiv_client
from . import secrets
from . import html_translate
from integrations import limited_environment

MAX_EVENT = 1024 * 1024
ERROR_CODES = {'invalid_request','engine_unavailable','engine_failed','timeout','cancelled','source_changed','invalid_output','io_error','internal_error','protocol_error'}

def environment():
    return limited_environment()

def legacy_app_data_dir():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'PolyScholar'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/PolyScholar'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'PolyScholar'

def is_source_checkout():
    """Source checkout (repo with pyproject.toml) -> portable data dir beside code."""
    root = Path(__file__).resolve().parents[1]
    return (root / 'pyproject.toml').is_file() and (root / 'polyscholar' / 'app.py').is_file()

def app_data_dir():
    # Frozen bundles and pip-installed packages may live in read-only or shared
    # locations (site-packages), so they use the platform directory; only a
    # source checkout is portable and keeps data beside the code.
    if getattr(sys, 'frozen', False) or not is_source_checkout():
        return legacy_app_data_dir()
    return Path(__file__).resolve().parents[1] / 'data'

def endpoint_url(value):
    try:
        p = urlsplit(value)
        _ = p.port
        local = p.hostname in ('localhost', '127.0.0.1', '::1')
        valid = p.hostname and (p.scheme == 'https' or (local and p.scheme == 'http'))
        if not valid or p.username or p.password or p.query or p.fragment or any(ord(c) < 32 for c in value):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('模型地址必须是无凭据、查询参数和片段的 HTTPS 地址，本机环回可用 HTTP。') from None
    return value.rstrip('/')

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a model credential to a redirect destination.
        return None

class LocalService:
    def __init__(self, data_dir=None, resources_dir=None):
        if data_dir is None:
            data_dir = app_data_dir()
            self._migrate_legacy_data(data_dir)
        self.store = LocalStore(data_dir)
        if resources_dir is None:
            if getattr(sys, 'frozen', False):
                resources_dir = Path(sys.executable).parents[1] / 'Resources/resources' if sys.platform == 'darwin' else Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent)) / 'resources'
            else:
                resources_dir = Path(__file__).resolve().parents[1]
        self.resources = Path(resources_dir).resolve()
        self._key = ''
        self._lock = threading.RLock()
        self._children = {}
        self._threads = {}
        self._closed = False
        self._subscribers = []

    def _migrate_legacy_data(self, target):
        """Copy pre-portable AppData content into the portable directory once.

        The legacy directory is kept untouched as a backup; only a fresh
        (empty or absent) portable target is populated, and only when a
        legacy library actually exists.
        """
        target = Path(target)
        legacy = legacy_app_data_dir()
        if target == legacy or not legacy.is_dir():
            return
        if target.is_dir() and any(target.iterdir()):
            return
        try:
            target.mkdir(parents=True, exist_ok=True)
            for name in ('library.sqlite3', 'library.sqlite3-wal', 'library.sqlite3-shm'):
                if (legacy / name).is_file():
                    shutil.copy2(legacy / name, target / name)
            for name in ('objects', 'cache'):
                if (legacy / name).is_dir():
                    shutil.copytree(legacy / name, target / name)
            self._rewrite_legacy_paths(target, legacy, target)
            self._reset_legacy_cache_path(target, legacy)
        except OSError as error:
            raise ValueError('迁移本地数据到 %s 失败：%s' % (target, error)) from None

    @staticmethod
    def _reset_legacy_cache_path(db_root, legacy):
        """A cachePath copied from the legacy location must not point there anymore."""
        import sqlite3
        database = db_root / 'library.sqlite3'
        if not database.is_file():
            return
        db = sqlite3.connect(database, timeout=10)
        try:
            with db:
                row = db.execute('SELECT data FROM desktop_settings WHERE id=1').fetchone()
                if not row:
                    return
                payload = json.loads(row[0])
                cache_path = payload.get('cachePath', '')
                if cache_path and str(legacy) in cache_path:
                    payload['cachePath'] = ''
                    db.execute('UPDATE desktop_settings SET data=? WHERE id=1',
                               (json.dumps(payload, ensure_ascii=False),))
        except (sqlite3.Error, json.JSONDecodeError, ValueError) as error:
            raise ValueError('迁移后重置缓存目录失败：%s' % error) from None
        finally:
            db.close()

    @staticmethod
    def _rewrite_legacy_paths(db_root, legacy, target):
        """Point migrated job records at the copied cache so old artifacts stay readable.

        Values are replaced inside parsed JSON: raw-text matching would miss
        Windows paths because JSON escapes every backslash.
        """
        import sqlite3
        database = db_root / 'library.sqlite3'
        if not database.is_file():
            return
        prefix_old, prefix_new = str(legacy), str(target)
        db = sqlite3.connect(database, timeout=10)
        try:
            with db:
                rows = db.execute('SELECT id, data FROM desktop_jobs').fetchall()
                for identifier, data in rows:
                    payload = json.loads(data)
                    changed = False
                    def walk(value):
                        nonlocal changed
                        if isinstance(value, str):
                            if prefix_old in value:
                                changed = True
                                return value.replace(prefix_old, prefix_new)
                            return value
                        if isinstance(value, dict):
                            return {key: walk(item) for key, item in value.items()}
                        if isinstance(value, list):
                            return [walk(item) for item in value]
                        return value
                    payload = walk(payload)
                    if changed:
                        db.execute('UPDATE desktop_jobs SET data=? WHERE id=?',
                                   (json.dumps(payload, ensure_ascii=False), identifier))
        except (sqlite3.Error, json.JSONDecodeError, ValueError) as error:
            raise ValueError('迁移后改写任务路径失败：%s' % error) from None
        finally:
            db.close()

    def subscribe_jobs(self, callback):
        with self._lock:
            self._subscribers.append(callback)
        def unsubscribe():
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)
        return unsubscribe

    def _notify_job(self, job):
        # Only allowlisted protocol fields cross the callback boundary.
        event = {key: job[key] for key in ('id','state','progress','usage','cost','errorCode') if key in job}
        with self._lock:
            callbacks = list(self._subscribers)
        for callback in callbacks:
            try:
                callback(event.copy())
            except Exception:
                pass  # A detached UI listener must not invalidate a persisted job.

    @staticmethod
    def _event_metrics(event):
        result = {}
        progress = event.get('progress')
        if type(progress) in (int, float) and math.isfinite(progress) and 0 <= progress <= 100:
            result['progress'] = progress
        usage = event.get('usage')
        if isinstance(usage, dict):
            valid = {k: v for k, v in usage.items() if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and 0 <= v <= 10**12}
            if valid:
                result['usage'] = valid
        # Costs lack a currency/units contract, so retain no ambiguous amounts.
        return result

    def diagnostic_report(self):
        # Explicit local export excludes titles, paths, endpoints, model names, IDs and raw errors.
        return json.dumps({'schemaVersion': 1, 'python': platform.python_version(),
            'platform': sys.platform, 'translation': 'arxiv-html-llm',
            'jobs': [{'engine':j['engine'],'state':j['state'],
                      'errorCode':j.get('errorCode') if j.get('errorCode') in ERROR_CODES else None,
                      'timeoutSeconds':j.get('timeoutSeconds',600)} for j in self.store.list_jobs()]}, indent=2)

    def export_diagnostics(self, path):
        self.store.write_export(path, self.diagnostic_report().encode('utf-8'), extra_protected=[self.resources])
        self.store.audit('diagnostics_exported')

    def list_documents(self):
        return self.store.list_root_documents()

    def list_attachments(self, parent_id):
        return self.store.list_attachments(parent_id)

    def import_attachment(self, parent_id, path, role='supplement'):
        return self.store.import_attachment(parent_id, path, role)

    def delete_attachment(self, parent_id, document_id):
        return self.store.delete_attachment(parent_id, document_id)

    def search_fulltext(self, text, **criteria):
        return self.store.search_fulltext(text, **criteria)

    def clear_fulltext_index(self, document_id=None):
        return self.store.clear_fulltext_index(document_id)

    def rebuild_fulltext_index(self, document_id=None):
        return self.store.rebuild_fulltext_index(document_id)

    def parse_document(self, document_id):
        try:
            return self._parse_document(document_id)
        except Exception:
            with self._lock:
                if not self._closed:
                    self.store.note_parse_failure(document_id)
            raise

    def _parse_document(self, document_id):
        document = self.store.document(document_id)
        worker = self.resources / 'integrations/parse_worker.py'
        if not worker.is_file():
            raise ValueError('安装包缺少本地文献解析组件。')
        python = Path(sys.executable)
        if not python.is_file():
            raise ValueError('内置文献解析运行环境缺失。')
        identifier = 'parse-' + str(uuid.uuid4())
        child = None
        try:
            with self._lock:
                if self._closed:
                    raise ValueError('应用正在关闭。')
                child = subprocess.Popen([str(python), '-I', str(worker)], env=environment(),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self._children[identifier] = child
            request = json.dumps({'source': str(self.store.object_path(document)), 'sha256': document['sha256']}).encode('utf-8')
            raw, _ = child.communicate(request+b'\n', timeout=120)
            if child.returncode or len(raw) > 16*1024*1024+1:
                raise ValueError('文献解析失败或超过安全大小限制。')
            result = json.loads(raw)
            messages = {'encrypted_pdf':'此 PDF 已加密，请先在本地解锁后导入。',
                'parse_limit':'文献内容超过当前解析限制，未保存截断结果。',
                'parser_unavailable':'内置文献解析组件版本不匹配。',
                'source_changed':'库内原文发生变化，请重新导入。'}
            if result.get('ok') is not True:
                raise ValueError(messages.get(result.get('code'),'未能解析此 PDF，原文件未修改。'))
            if result.get('sha256') != document['sha256']:
                raise ValueError('解析结果与库内原文不匹配。')
            with self._lock:
                if self._closed:
                    raise ValueError('应用正在关闭，解析结果未保存。')
                return self.store.replace_document_ir(document_id, result['parser'], result['pages'])
        except subprocess.TimeoutExpired:
            raise ValueError('本地解析超时，结果未保存。') from None
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
            raise ValueError('本地文献解析失败，请检查文件或内置运行环境。') from None
        finally:
            if child:
                self._stop(child)
                for stream in (child.stdin, child.stdout):
                    if stream and not stream.closed:
                        stream.close()
            with self._lock:
                self._children.pop(identifier, None)

    def current_document_ir(self, document_id):
        return self.store.current_document_ir(document_id)

    def document_blocks(self, document_id, revision_id=None):
        return self.store.document_blocks(document_id, revision_id)

    def list_collections(self):
        return self.store.list_collections()

    def create_collection(self, name, parent_id=None):
        return self.store.create_collection(name, parent_id)

    def update_collection(self, identifier, name, parent_id=None):
        return self.store.update_collection(identifier, name, parent_id)

    def delete_collection(self, identifier):
        return self.store.delete_collection(identifier)

    def set_membership(self, document_id, collection_id, present=True):
        return self.store.set_membership(document_id, collection_id, present)

    def document_collections(self, document_id):
        return self.store.document_collections(document_id)

    def list_tags(self):
        return self.store.list_tags()

    def rename_tag(self, old, new=None):
        return self.store.rename_tag(old, new)

    def search_documents(self, **criteria):
        return self.store.search_documents(**criteria)

    def list_saved_searches(self):
        return self.store.list_saved_searches()

    def save_saved_search(self, name, query, search_id=None):
        return self.store.save_saved_search(name, query, search_id)

    def delete_saved_search(self, search_id):
        return self.store.delete_saved_search(search_id)

    def import_pdf(self, path):
        document = self.store.import_pdf(path)
        # Ordinary library import deduplicates to the bibliographic root, while
        # attachment import keeps the individual PDF identity for reading/jobs.
        with self.store.connection() as db:
            owner = db.execute('SELECT parent_document_id FROM desktop_attachment_links WHERE child_document_id=?',
                               (document['id'],)).fetchone()
        return self.store.document(owner[0]) if owner else document

    def arxiv_lookup(self, query, endpoint=None):
        identifiers = arxiv_client.parse_identifiers(query)
        if not identifiers:
            raise ValueError('未识别到 arXiv 编号；请粘贴 arxiv.org 链接或编号，每行一个。')
        if len(identifiers) > arxiv_client.MAX_ENTRIES:
            raise ValueError('一次最多查询 %d 篇。' % arxiv_client.MAX_ENTRIES)
        return arxiv_client.fetch_metadata(identifiers, endpoint or arxiv_client.API_URL)

    def arxiv_import(self, entries, collection_id=None):
        """Download and import checked arXiv entries; per-entry errors are sanitized."""
        return arxiv_client.import_batch(self, entries, collection_id)

    def update_document(self, document_id, patch):
        return self.store.update_document(document_id, patch)

    def set_scores(self, document_id, entries):
        """Persist AI scores for a paper; ready for MCP/CLI writers later."""
        return self.store.set_scores(document_id, entries)

    def document_scores(self, document_id):
        return self.store.document_scores(document_id)

    def set_score_report(self, document_id, kind, agent_id, data_text):
        """Archive one blind-review judge report verbatim (first-class artifact)."""
        return self.store.set_score_report(document_id, kind, agent_id, data_text)

    def score_reports(self, document_id, kind, agent_id=None):
        return self.store.score_reports(document_id, kind, agent_id)

    def document(self, document_id):
        """Public single-document lookup (metadata + scores exposure for CLI/MCP)."""
        document = self.store.document(document_id)
        document['scores'] = {kind: (value or {}).get('score') if isinstance(value, dict) else None
                              for kind, value in self.store.document_scores(document_id).items()}
        return document

    def delete_document(self, document_id):
        return self.store.delete_document(document_id)

    def read_pdf(self, document_id):
        return self.store.read_pdf(document_id)

    def get_settings(self):
        settings = self.store.get_settings()
        if not settings['cachePath']:
            settings['cachePath'] = str(self.store.prepare_cache(''))
        return settings

    def save_settings(self, settings):
        return self.store.save_settings(settings)

    def set_session_key(self, key):
        if not isinstance(key, str) or len(key) > 16384 or any(ord(c) < 32 for c in key):
            raise ValueError('密钥含无效字符或过长。')
        with self._lock:
            self._key = key

    def store_api_key(self, key, mode='os'):
        """Validate, activate for this session and persist per the chosen mode.

        mode='os' writes the OS credential store; mode='file' writes the plain
        agent-style config file, an explicit user choice whose risks are documented.
        """
        self.set_session_key(key)
        if mode == 'file':
            secrets.store_secret_file(key)
        else:
            secrets.store_secret(key)

    def clear_stored_api_key(self):
        secrets.clear_secret()
        secrets.clear_secret_file()

    def api_key_stored(self):
        return secrets.load_secret() is not None

    def stored_key_location(self):
        if self.api_key_stored():
            return 'os'
        if secrets.load_secret_file():
            return 'file'
        return None

    def _effective_key(self):
        # Session key wins; otherwise fall back to stored credentials (store, then file).
        with self._lock:
            if self._key:
                return self._key
        stored = secrets.load_secret() or secrets.load_secret_file() or ''
        if stored:
            with self._lock:
                self._key = stored
        return stored

    def list_models(self, endpoint=None, key=None):
        """GET configured OpenAI-compatible /models; returns list[str]. GUI runs in QThread."""
        address = endpoint_url(endpoint if endpoint is not None else self.store.get_settings()['endpoint'])
        token = key if key is not None else self._effective_key()
        if not isinstance(token, str) or not token or len(token) > 16384 or any(ord(c) < 32 for c in token):
            raise ValueError('请先设置或记住 API 密钥。')
        request = Request(address + '/models', headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}, method='GET')
        try:
            with build_opener(NoRedirect()).open(request, timeout=15) as response:
                raw = response.read(MAX_EVENT + 1)
            if len(raw) > MAX_EVENT:
                raise ValueError()
            body = json.loads(raw)
            if not isinstance(body, dict) or not isinstance(body.get('data'), list):
                raise ValueError()
            identifiers = []
            for item in body['data']:
                value = item.get('id') if isinstance(item, dict) else None
                if isinstance(value, str) and value and len(value) <= 256 and not any(ord(c) < 32 for c in value):
                    identifiers.append(value)
            return sorted(set(identifiers))
        except Exception:
            raise ValueError('无法获取模型列表，请检查模型地址、密钥与网络；该服务可能未提供 /models，可手动填写模型名。') from None

    def list_jobs(self):
        return self.store.list_jobs()

    def delete_job(self, job_id):
        """Delete a finished job record; in-flight jobs are owned by this service."""
        with self._lock:
            if job_id in self._threads:
                raise ValueError('任务仍在进行中，结束后再删除记录。')
        return self.store.delete_job(job_id)

    def job_output_dir(self, job_id):
        """Real output directory of a completed job, for 'open folder' in the UI."""
        job = next((j for j in self.store.list_jobs() if j['id'] == job_id), None)
        if not job:
            raise ValueError('任务记录不存在。')
        if job['state'] != 'completed':
            raise ValueError('任务尚未完成。')
        output = Path(job['outputDir'])
        # Only ever expose a UUID-named directory the application itself created.
        if output.name != job_id or output.parent.name != 'jobs' or not output.is_dir():
            raise ValueError('任务产物目录不存在。')
        return output

    def attachment_file_path(self, document_id):
        """Local object path of a library PDF, for 'open with default app'."""
        document = self.store.document(document_id)
        return self.store.object_path(document)

    def start_translation(self, document_id, pages=''):
        """Translate an arXiv paper into a bilingual HTML artifact.

        The HTML pipeline always covers the full text; `pages` is accepted for
        UI compatibility and intentionally ignored.
        """
        settings = self.store.get_settings()
        endpoint_url(settings['endpoint'])
        if not settings['model'].strip():
            raise ValueError('请先选择或填写模型名称。')
        with self._lock:
            if self._closed:
                raise ValueError('应用正在关闭。')
            api_key = self._effective_key()
            if not api_key:
                raise ValueError('请先设置或记住 API 密钥。')
            document = self.store.document(document_id)
            identifier = arxiv_client.parse_identifier(document.get('url') or '')
            if not identifier:
                raise ValueError('HTML 翻译仅支持带 arXiv 链接的文献；请通过 arXiv 导入。')
            job = self.store.new_job(document_id, 'html-llm')
            thread = threading.Thread(target=self._run_html,
                args=(job.copy(), identifier, settings['endpoint'], settings['model'], api_key),
                daemon=True, name='polyscholar-html')
            self._threads[job['id']] = thread
            thread.start()
            return job

    def _write_partial_html(self, job, html_partial):
        """Progressive reading: rewrite the artifact as completed blocks arrive (throttled)."""
        cache = getattr(self, '_partial_ts', None)
        if cache is None:
            cache = self._partial_ts = {}
        now = time.monotonic()
        if now - cache.get(job['id'], 0.0) < 2.0:
            return
        cache[job['id']] = now
        (Path(job['outputDir']) / 'translated.html').write_text(html_partial, encoding='utf-8')

    def _run_html(self, job, identifier, endpoint, model, api_key):
        try:
            job['state'] = 'running'
            self.store.put_job(job)
            self._notify_job(job)
            output = Path(job['outputDir'])
            output.mkdir(parents=True, exist_ok=True)
            artifact = output / 'translated.html'
            def write_partial(html_partial):
                self._write_partial_html(job, html_partial)
            def progress(current, total):
                job['progress'] = {'current': current, 'total': total}
                self.store.put_job(job)
            html, _total, translated = html_translate.translate_paper(
                identifier, endpoint, model, api_key, progress=progress, on_partial=write_partial)
            artifact.write_text(html, encoding='utf-8')
            if '</html>' not in html or not html.strip():
                raise ValueError('译文 HTML 未生成。')
            job.update(state='completed', artifacts=['translated.html'],
                       translatedBlocks=translated, totalBlocks=_total)
        except Exception as error:
            # Never retain raw exceptions, provider bodies, credentials or paper text.
            import os as _os, traceback as _tb
            if _os.environ.get('POLYSCHOLAR_DEBUG_TRACE'):
                import sys as _sys
                print(_tb.format_exc(), file=_sys.stderr, flush=True)
            job.setdefault('errorCode', 'engine_failed')
            job.update(state='failed', error='HTML 翻译未完成。请检查模型设置与网络后重试；已发送的 API 请求可能计费。')
        finally:
            try:
                self.store.put_job(job)
                self.store.audit('translation_finished', 'succeeded' if job['state'] == 'completed' else 'failed')
                self._notify_job(job)
            finally:
                with self._lock:
                    self._threads.pop(job['id'], None)
                    if self._closed and not self._threads:
                        self.store.close()

    def artifact_file(self, job_id, artifact_index=0):
        """Bounded bytes of a completed job artifact; HTML jobs have no PDF output."""
        job = next((j for j in self.store.list_jobs() if j['id'] == job_id), None)
        if not job or job['state'] != 'completed':
            raise ValueError('翻译任务尚未成功完成。')
        name = job['artifacts'][artifact_index] if 0 <= artifact_index < len(job.get('artifacts') or []) else None
        if not name:
            raise ValueError('译文产物不存在。')
        output = Path(job['outputDir'])
        candidate = output / name
        if candidate.name != name or candidate.is_symlink() or not candidate.is_file():
            raise ValueError('译文产物不存在。')
        if candidate.suffix.lower() == '.pdf':
            return self.store.read_bounded_pdf(candidate)
        data = candidate.open('rb').read(24 * 1024 * 1024 + 1)
        if len(data) > 24 * 1024 * 1024:
            raise ValueError('译文超过 24 MiB 上限。')
        return data

    @staticmethod
    def _stop(child):
        # Still used by the parse/summary worker subprocesses (not translation).
        if child.poll() is not None:
            return
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            else:
                child.send_signal(signal.SIGTERM)
            child.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            child.kill()
            child.wait(timeout=5)

    def read_artifact_pdf(self, job_id, artifact_index=0):
        return self.store.read_bounded_pdf(self.store.artifact_path(job_id, artifact_index))

    def document_translations(self, document_id):
        """All completed HTML translations belonging to a document (including
        its child attachments), newest first. The translation belongs to the
        paper, not to the task record that produced it."""
        document = self.store.document(document_id)
        identifiers = {document_id}
        with self.store.connection() as db:
            for (child,) in db.execute(
                    'SELECT child_document_id FROM desktop_attachment_links WHERE parent_document_id=?',
                    (document_id,)):
                identifiers.add(child)
        result = []
        for job in self.store.list_jobs():
            if job.get('state') != 'completed' or job.get('documentId') not in identifiers:
                continue
            for index, name in enumerate(job.get('artifacts') or []):
                if not name.lower().endswith('.html'):
                    continue
                try:
                    path = self.store.artifact_path(job['id'], index, kinds=('.html',))
                except ValueError:
                    continue
                result.append(dict(jobId=job['id'], index=index, path=str(path),
                                   createdAt=job.get('createdAt', '')))
        result.sort(key=lambda item: item['createdAt'], reverse=True)
        return result

    def open_translation(self, document_id):
        """Newest translation of a document, ready for the system browser."""
        translations = self.document_translations(document_id)
        if not translations:
            raise ValueError('该文献还没有中文译文；请先在翻译任务页创建翻译。')
        return Path(translations[0]['path'])

    def artifact_path(self, job_id, artifact_index, kinds=('.pdf',)):
        return self.store.artifact_path(job_id, artifact_index, kinds=kinds)

    def export_translation(self, job_id, artifact_index, path):
        """Export a finished artifact; PDF keeps its checks, HTML gets a text-safe copy."""
        job = next((j for j in self.store.list_jobs() if j['id'] == job_id), None)
        if not job:
            raise ValueError('任务记录不存在。')
        name = (job.get('artifacts') or [None])[artifact_index] if 0 <= artifact_index < len(job.get('artifacts') or []) else None
        if name and str(name).lower().endswith('.html'):
            source = self.store.artifact_path(job_id, artifact_index, kinds=('.html',))
            data = source.read_bytes()
            if len(data) > 24 * 1024 * 1024:
                raise ValueError('译文超过 24 MiB 上限。')
            self.store.write_export(path, data, extra_protected=[self.resources])
        else:
            source = self.store.artifact_path(job_id, artifact_index)
            self.store.write_export(path, self.store.read_bounded_pdf(source), extra_protected=[self.resources])
        self.store.audit('artifact_exported')

    def export_metadata(self, document_ids, format, path):
        body = self.format_metadata(document_ids, format)
        self.store.write_export(path,body.encode('utf-8'),extra_protected=[self.resources])
        self.store.audit('citation_exported')

    def format_metadata(self, document_ids, format):
        identifiers = [document_ids] if isinstance(document_ids, str) else document_ids
        if not isinstance(identifiers, list) or any(not isinstance(value, str) for value in identifiers):
            raise ValueError('文献导出列表无效。')
        documents = [self.store.document(value) for value in dict.fromkeys(identifiers)]
        if any(document.get('parentDocumentId') for document in documents):
            raise ValueError('附件不能单独导出引用，请选择所属文献。')
        bodies = [self._metadata(document, format) for document in documents]
        if format == 'csl-json':
            return json.dumps([json.loads(value)[0] for value in bodies], ensure_ascii=False, indent=2)
        if format in ('bibtex', 'ris'):
            return ''.join(bodies)
        raise ValueError('不支持的元数据格式。')

    def _metadata(self, document, format):
        return exchange_metadata(document, format)

    def close(self):
        with self._lock:
            self._closed = True
            self._key = ''
            children = list(self._children.values())
            threads = list(self._threads.values())
        for child in children:
            self._stop(child)
        for thread in threads:
            thread.join(timeout=6)
        if not any(thread.is_alive() for thread in threads):
            self.store.close()
