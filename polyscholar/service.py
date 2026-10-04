# SPDX-License-Identifier: AGPL-3.0-only
"""Python desktop application services. Direct calls, no local web service."""
import json
import math
import platform
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import uuid
from urllib.request import Request, build_opener
from .store import LocalStore
from .workbench import AgentWorkbenchService
from .code_review import CodeReviewService
from .model_endpoint import endpoint_url
from .metadata import exchange_metadata
from .citation_import import CitationImporter
from .zotero_migration import ZoteroMigrationImporter
from .summary_model import selected_blocks, request_summary, NoRedirect
from integrations.engines import VERSIONS, limited_environment, stop_process
from integrations.managed_process import WindowsProcess, start_process, run_captured

from integrations.github_snapshot import empty_report, repository_url, MAX_REPORT_BYTES

VERIFICATION_TIMEOUT = 60


class VerificationTimeout(ValueError):
    """Budget expiration is distinct from invalid worker output / 超时与无效响应分开。"""

MAX_EVENT = 1024 * 1024
ERROR_CODES = {'invalid_request','engine_unavailable','engine_failed','timeout','cancelled','source_changed','invalid_output','io_error','internal_error','protocol_error'}

def environment():
    return limited_environment()

def app_data_dir():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'PolyScholar'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/PolyScholar'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'PolyScholar'

class LocalService(CodeReviewService, AgentWorkbenchService):
    def __init__(self, data_dir=None, resources_dir=None):
        self.store = LocalStore(data_dir or app_data_dir())
        if resources_dir is None:
            if getattr(sys, 'frozen', False):
                resources_dir = Path(sys.executable).parents[1] / 'Resources/resources' if sys.platform == 'darwin' else Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent)) / 'resources'
            else:
                resources_dir = Path(__file__).resolve().parents[1]
        self.resources = Path(resources_dir).resolve()
        self._key = ''
        self._lock = threading.RLock()
        self._children = {}
        self._child_documents = {}
        self._threads = {}
        self._html_cancellations = {}
        self._closed = False
        self._subscribers = []
        self._citation_importer = CitationImporter()
        self._zotero_importer = ZoteroMigrationImporter(self.store.root)

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
            'platform': sys.platform, 'engines': {k:v[1] for k,v in VERSIONS.items()},
            'jobs': [{'engine':j['engine'],'state':j['state'],
                      'errorCode':j.get('errorCode') if j.get('errorCode') in ERROR_CODES else None,
                      'timeoutSeconds':j.get('timeoutSeconds',600)} for j in self.store.list_jobs()]}, indent=2)

    def export_diagnostics(self, path):
        self.store.write_export(path, self.diagnostic_report().encode('utf-8'), extra_protected=[self.resources])
        self.store.audit('diagnostics_exported')

    def list_documents(self):
        return self._with_scores(self.store.list_root_documents())

    def preview_zotero_migration(self, directory, linked_directory=None):
        return self._zotero_importer.preview(directory, linked_directory)

    def cancel_zotero_migration(self):
        self._zotero_importer.cancel()

    def import_zotero_preview(self, preview, selected_ids=None):
        state, selected = self._zotero_importer.selected(preview, selected_ids)
        with self._lock:
            if self._closed:
                raise ValueError('应用正在关闭，未导入任何条目。')
            result = self.store.import_zotero_state(
                state, selected, self._zotero_importer._cancelled,
                self._zotero_importer.validate_state,
            )
            self._zotero_importer.consumed(preview)
            return result

    def export_zotero_resource(self, receipt_id, source_id, destination):
        with self._lock:
            return self.store.export_zotero_resource(receipt_id, source_id, destination)

    def list_zotero_migrations(self):
        return self.store.list_zotero_migrations()

    def read_zotero_migration(self, receipt_id):
        return self.store.read_zotero_migration(receipt_id)

    def preview_metadata_import(self, path, format):
        return self._citation_importer.preview(path, format)

    def import_metadata_preview(self, preview, selected_indices, collection_id=None):
        metadata_items = self._citation_importer.selected(preview, selected_indices)
        with self._lock:
            if self._closed:
                raise ValueError('应用正在关闭，未导入任何条目。')
            self._citation_importer.ensure_preview(preview)
            result = self.store.import_bibliographic_items(metadata_items, collection_id)
            self._citation_importer.consumed(preview)
            return result

    def create_bibliographic_item(self, metadata, collection_id=None):
        with self._lock:
            return self.store.create_bibliographic_item(metadata,collection_id)

    def primary_pdf_id(self, root_id):
        return self.store.primary_pdf_id(root_id)

    def set_primary_pdf(self, root_id, pdf_id):
        with self._lock:
            return self.store.set_primary_pdf(root_id,pdf_id)

    def list_attachments(self, parent_id):
        return self.store.list_attachments(parent_id)

    def import_attachment(self, parent_id, path, role='supplement'):
        return self.store.import_attachment(parent_id, path, role)

    def delete_attachment(self, parent_id, document_id):
        with self._lock:
            self._require_idle_workers(document_id)
            return self.store.delete_attachment(parent_id, document_id)

    def search_fulltext(self, text, **criteria):
        return self.store.search_fulltext(text, **criteria)

    def clear_fulltext_index(self, document_id=None):
        return self.store.clear_fulltext_index(document_id)

    def rebuild_fulltext_index(self, document_id=None):
        return self.store.rebuild_fulltext_index(document_id)

    def parse_document(self, document_id):
        self.store.require_pdf(document_id)
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
        if getattr(sys, 'frozen', False):
            runtime = self.resources / 'runtime/babeldoc'
            python = runtime / ('python.exe' if os.name == 'nt' else 'bin/python3')
        else:
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
                self.store.require_pdf(document_id)
                self._children[identifier] = child
                self._child_documents[identifier] = document_id
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
                self._child_documents.pop(identifier, None)

    def current_document_ir(self, document_id):
        self.store.require_pdf(document_id)
        return self.store.current_document_ir(document_id)

    def document_blocks(self, document_id, revision_id=None):
        self.store.require_pdf(document_id)
        return self.store.document_blocks(document_id, revision_id)

    def save_claim(self, document_id, text, evidence):
        self.store.require_pdf(document_id)
        return self.store.save_claim(document_id, text, evidence)

    def list_claims(self, document_id):
        self.store.require_pdf(document_id)
        return self.store.list_claims(document_id)

    def summarize_document(self, document_id, block_ids, revision_id=None, expected_settings=None):
        """Send only explicitly selected current blocks; atomically save checked claims."""
        self.store.require_pdf(document_id)
        with self._lock:
            if self._closed:
                raise ValueError('应用正在关闭。')
            token = self._effective_key()
            settings = self.store.get_settings()
        if expected_settings is not None and expected_settings != {k: settings[k] for k in ('endpoint', 'model', 'targetLanguage')}:
            raise ValueError('模型配置已变更，请确认发送范围后重新生成。')
        if not token:
            raise ValueError('请先设置会话 API 密钥。')
        address = endpoint_url(settings['endpoint'])
        current = self.store.current_document_ir(document_id)
        if not current or current['status'] != 'ready':
            raise ValueError('请先提取本地文献文本。')
        if revision_id is not None and revision_id != current['id']:
            raise ValueError('所选解析版本已失效，请刷新后重新选择。')
        revision_id = current['id']
        blocks = selected_blocks(self.store.document_blocks(document_id, revision_id), block_ids)
        if getattr(sys, 'frozen', False):
            python = self.resources / 'runtime/babeldoc' / ('python.exe' if os.name == 'nt' else 'bin/python3')
        else:
            python = Path(sys.executable)
        identifier = 'summary-' + str(uuid.uuid4())
        def started(child):
            with self._lock:
                if self._closed:
                    raise ValueError('应用正在关闭。')
                self.store.require_pdf(document_id)
                self._children[identifier] = child
                self._child_documents[identifier] = document_id
        def finished(child):
            with self._lock:
                self._children.pop(identifier, None)
                self._child_documents.pop(identifier, None)
        claims, usage = request_summary(address, token, settings['model'], settings['targetLanguage'], blocks,
            min(settings['timeoutSeconds'], 120), python_path=python,
            worker_path=self.resources / 'integrations/summary_worker.py', on_spawn=started, on_done=finished)
        with self._lock:
            if self._closed:
                raise ValueError('应用正在关闭，摘要结果未保存。')
            return self.store.save_model_summary(document_id, revision_id, claims, settings['model'], usage,
                                                 input_block_ids=[b['id'] for b in blocks])

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
        self.store.require_active(document_id)
        return self.store.document_collections(document_id)

    def list_tags(self):
        return self.store.list_tags()

    def rename_tag(self, old, new=None):
        return self.store.rename_tag(old, new)

    def search_documents(self, **criteria):
        return self._with_scores(self.store.search_documents(**criteria))

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

    def update_document(self, document_id, patch):
        return self.store.update_document(document_id, patch)

    def _require_idle_workers(self, document_id):
        scope = set(self.store.document_family_ids(document_id))
        jobs = {job['id']: job['documentId'] for job in self.store.list_jobs()}
        if any(thread.is_alive() and jobs.get(key) in scope for key, thread in self._threads.items()):
            raise ValueError('请等待翻译任务完全结束后操作。')
        if any(identifier in scope and self._children.get(key) is not None
               and self._children[key].poll() is None
               for key, identifier in self._child_documents.items()):
            raise ValueError('请等待文献解析、摘要或外部核验任务结束后操作。')

    def list_duplicate_candidates(self, limit=200):
        return self.store.list_duplicate_candidates(limit)

    def merge_preview(self, document_ids, master_id=None):
        master_id = self.store.validate_merge_selection(document_ids, master_id)
        with self._lock:
            for identifier in document_ids if isinstance(document_ids, list) else []:
                self._require_idle_workers(identifier)
            return self.store.merge_preview(document_ids, master_id)

    def merge_documents(self, document_ids, master_id, field_sources, expected_revision):
        master_id = self.store.validate_merge_selection(document_ids, master_id)
        with self._lock:
            for identifier in document_ids if isinstance(document_ids, list) else []:
                self._require_idle_workers(identifier)
            return self.store.merge_documents(document_ids, master_id, field_sources, expected_revision)

    def list_merge_history(self, master_id):
        return self.store.list_merge_history(master_id)

    def trash_document(self, document_id):
        with self._lock:
            self._require_idle_workers(document_id)
            return self.store.trash_document(document_id)

    def restore_document(self, document_id):
        with self._lock:
            self._require_idle_workers(document_id)
            return self.store.restore_document(document_id)

    def purge_document(self, document_id):
        with self._lock:
            self._require_idle_workers(document_id)
            return self.store.purge_document(document_id)

    def list_trash(self):
        return self.store.list_trash()

    def deletion_preview(self, document_id):
        return self.store.deletion_preview(document_id)

    def list_pending_cleanup(self):
        return self.store.list_pending_cleanup()

    def retry_cleanup(self, cleanup_id=None):
        return self.store.retry_cleanup(cleanup_id)

    def delete_document(self, document_id):
        with self._lock:
            self._require_idle_workers(document_id)
            return self.store.delete_document(document_id)

    def read_pdf(self, document_id):
        return self.store.read_pdf(document_id)

    def get_settings(self):
        settings = self.store.get_settings()
        discovered = self.discover_engine(settings['engine'])
        settings['pythonPath'] = discovered['pythonPath']
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

    def discover_engine(self, engine):
        if engine not in VERSIONS:
            return dict(pythonPath='', available=False, message='不支持的翻译引擎。')
        package, expected = VERSIONS[engine]
        candidates = []
        # Both released bundles and repository staging contain independent interpreters.
        for directory in (self.resources / 'runtime' / engine, self.resources / '.runtime' / engine):
            if os.name == 'nt':
                candidates.extend((directory / 'python.exe', directory / 'Scripts/python.exe'))
            else:
                candidates.extend((directory / 'bin/python3', directory / 'bin/python'))
        for python in candidates:
            if not python.is_file():
                continue
            try:
                result = subprocess.run([str(python), '-I', '-c',
                    f'import importlib.metadata; print(importlib.metadata.version({package!r}))'],
                    env=environment(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=10, check=False)
                if result.returncode == 0 and result.stdout.strip() == expected.encode():
                    return dict(pythonPath=str(python), available=True, message=f'已发现自带 {engine} {expected} 独立 Python 运行环境。')
            except (OSError, subprocess.TimeoutExpired):
                continue
        return dict(pythonPath='', available=False,
                    message='此安装包尚未包含完整且匹配版本的内置引擎运行环境。请安装包含运行环境的正式包；无需配置系统 Python。')

    def list_models(self, endpoint=None, key=None):
        """GET configured OpenAI-compatible /models; returns list[str]. GUI runs in QThread."""
        address = endpoint_url(endpoint if endpoint is not None else self.store.get_settings()['endpoint'])
        with self._lock:
            token = self._effective_key() if key is None else key
        if not isinstance(token, str) or not token or len(token) > 16384 or any(ord(c) < 32 for c in token):
            raise ValueError('请先设置会话 API 密钥。')
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
        return [job for job in self.store.list_jobs() if self.store.is_active(job['documentId'])]

    def start_translation(self, document_id, pages='', engine=None):
        self.store.require_pdf(document_id)
        if not isinstance(pages, str) or len(pages) > 4096:
            raise ValueError('页码范围无效。')
        if pages:
            for item in pages.split(','):
                if not re.fullmatch(r'[1-9][0-9]*(?:-[1-9][0-9]*)?', item):
                    raise ValueError('页码范围无效。')
                bounds = [int(v) for v in item.split('-')]
                if len(bounds) == 2 and bounds[0] > bounds[1]:
                    raise ValueError('页码范围倒置。')
        settings = self.store.get_settings()
        if engine is not None:
            if engine not in VERSIONS:
                raise ValueError('不支持的 PDF 翻译引擎。')
            settings['engine'] = engine
        endpoint_url(settings['endpoint'])
        discovery = self.discover_engine(settings['engine'])
        if not discovery['available']:
            raise ValueError(discovery['message'])
        if not settings['model'].strip():
            raise ValueError('请先选择或填写模型名称。')
        worker = self.resources / 'integrations/job_worker.py'
        if not worker.is_file():
            raise ValueError('安装包缺少内置工作进程。')
        with self._lock:
            if self._closed:
                raise ValueError('应用正在关闭。')
            key = self._effective_key()
            if not key:
                raise ValueError('请先设置 API 密钥。')
            document = self.store.document(document_id)
            job = self.store.new_job(document_id, settings['engine'])
            payload = dict(protocol_version=1, job_id=job['id'], engine=settings['engine'], python=discovery['pythonPath'],
                source=str(self.store.object_path(document)), output=job['outputDir'], endpoint=settings['endpoint'], model=settings['model'],
                api_key=key, source_language=settings['sourceLanguage'], target_language=settings['targetLanguage'], pages=pages,
                timeout=job['timeoutSeconds'], allow_document_upload=True, allow_asset_download=True)
            thread = threading.Thread(target=self._run, args=(job.copy(), payload, worker), daemon=True, name='polyscholar-engine')
            self._threads[job['id']] = thread
            thread.start()
            return job

    def _run(self, job, payload, worker):
        child = None
        try:
            job['state'] = 'running'
            self.store.put_job(job)
            self._notify_job(job)
            Path(job['outputDir']).parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                if self._closed:
                    raise ValueError()
                # 冻结模块不是真实脚本目录；use the bundled resource copy.
                gate = (self.resources / 'integrations/process_gate.py'
                        if getattr(sys, 'frozen', False) else None)
                child = start_process([payload['python'], '-I', str(worker)], env=environment(),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    gate_path=gate)
                self._children[job['id']] = child
            request = json.dumps(payload, ensure_ascii=False).encode() + b'\n'
            if len(request) > MAX_EVENT:
                raise ValueError()
            child.stdin.write(request)
            child.stdin.close()
            payload['api_key'] = ''
            completed = False
            while True:
                line = child.stdout.readline(MAX_EVENT + 1)
                if not line:
                    break
                if len(line) > MAX_EVENT:
                    raise ValueError()
                event = json.loads(line)
                if not isinstance(event, dict) or event.get('protocol_version') != 1 or event.get('job_id') != job['id']:
                    raise ValueError()
                if event.get('event') not in ('started','progress','completed','failed'):
                    raise ValueError()
                metrics = self._event_metrics(event)
                if metrics:
                    job.update(metrics)
                    self.store.put_job(job)
                    self._notify_job(job)
                if event.get('event') == 'failed':
                    code = event.get('error_code')
                    job['errorCode'] = code if code in ERROR_CODES else 'protocol_error'
                    raise ValueError()
                if event.get('event') == 'completed':
                    completed = True
            if child.wait(timeout=15) != 0 or not completed:
                raise ValueError()
            output = Path(job['outputDir'])
            artifacts = sorted(p.name for p in output.glob('*.pdf') if p.is_file() and not p.is_symlink())
            if not artifacts:
                raise ValueError()
            for artifact in artifacts:
                self.store.read_bounded_pdf(output / artifact)
            job.update(state='completed', artifacts=artifacts)
        except Exception:
            # Never retain raw exceptions, engine output, provider bodies, credentials or PDF text.
            job.setdefault('errorCode', 'protocol_error')
            job.update(state='failed', error='翻译任务未完成。请检查内置引擎、模型设置与网络后重试；已发送的 API 请求可能计费。')
        finally:
            payload['api_key'] = ''
            if child:
                try:
                    self._stop(child)
                except (OSError, subprocess.TimeoutExpired):
                    # 未确认退出不能记为成功。Do not publish success on cleanup failure.
                    job.update(state='failed', errorCode='io_error',
                               error='翻译进程清理未能确认完成，请关闭应用后重试。')
                if child.stdin and not child.stdin.closed:
                    try:
                        child.stdin.close()
                    except OSError:
                        pass  # The worker may already have closed its request pipe.
                if child.stdout:
                    child.stdout.close()
            # Retain the thread until all database writes close, so shutdown can join it.
            try:
                self.store.put_job(job)
                self.store.audit('translation_finished', 'succeeded' if job['state'] == 'completed' else 'failed')
                self._notify_job(job)
            finally:
                with self._lock:
                    self._children.pop(job['id'], None)
                    self._threads.pop(job['id'], None)
                    if self._closed and not self._threads:
                        self.store.close()

    @staticmethod
    def _stop(child):
        if isinstance(child, WindowsProcess):
            child.stop()
            return
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

    def export_translation(self, job_id, artifact_index, path):
        source = self.store.artifact_path(job_id, artifact_index, kinds=('.pdf', '.html'))
        data = self.store.read_bounded_pdf(source) if source.suffix.lower() == '.pdf' else source.read_bytes()
        self.store.write_export(path, data, extra_protected=[self.resources])
        self.store.audit('artifact_exported')

    def export_metadata(self, document_ids, format, path):
        body = self.format_metadata(document_ids, format)
        self.store.write_export(path,body.encode('utf-8'),extra_protected=[self.resources])
        self.store.audit('citation_exported')

    def format_metadata(self, document_ids, format):
        identifiers = [document_ids] if isinstance(document_ids, str) else document_ids
        if not isinstance(identifiers, list) or any(not isinstance(value, str) for value in identifiers):
            raise ValueError('文献导出列表无效。')
        for identifier in identifiers:
            self.store.require_active(identifier)
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
        self._zotero_importer.close()
        self._citation_importer.close()
        with self._lock:
            self._closed = True
            self._key = ''
            children = list(self._children.items())
            threads = list(self._threads.values())
            for cancellation in self._html_cancellations.values():
                cancellation.set()
        for identifier, child in children:
            if identifier.startswith('verify-'):
                self._stop_verification(child)
            else:
                self._stop(child)
        for thread in threads:
            thread.join(timeout=6)
        if not any(thread.is_alive() for thread in threads):
            self.store.close()


    def verify_github(self, document_id, url, authorized=False):
        """Only the explicit repository identifier crosses the worker boundary.

        核验前必须确认外发范围；工作进程只收到仓库 URL，不收到论文或密钥。
        """
        normalized = repository_url(url)
        if authorized is not True:
            raise ValueError('GitHub 核验需要显式触发：只向 api.github.com 发送仓库标识，不发送论文正文、标题或模型密钥。')
        document = self.verification_document(document_id)
        document_id = document['id']
        try:
            report = self._run_github_worker(document_id, {'repository_url': normalized})
        except VerificationTimeout:
            report = empty_report(normalized)
            report['error_code'] = 'timeout'
        if report.get('repository_url') != normalized:
            raise ValueError('核验返回的仓库与授权范围不一致，未保存报告。')
        with self._lock:
            if self._closed:
                raise ValueError('应用正在关闭，核验结果未保存。')
            return self.store.save_verification(document_id, report, document['sha256'])

    def _run_github_worker(self, document_id, request):
        """Share bounded process supervision across snapshot and code reads.

        快照与代码读取共用有界进程托管；仅外发仓库及对象身份，不传论文。
        """
        worker = self.resources / 'integrations/github_snapshot.py'
        if not worker.is_file() or getattr(sys, 'frozen', False):
            raise ValueError('当前运行环境不能启动独立核验组件；冻结安装包的核验运行时尚未验收。')
        identifier = 'verify-' + str(uuid.uuid4())
        child_env = environment()
        for name in ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY',
                     'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy'):
            if name in os.environ:
                child_env[name] = os.environ[name]

        def started(child):
            with self._lock:
                if self._closed:
                    raise ValueError('应用正在关闭。')
                self.store.require_active(document_id)
                self._children[identifier] = child
                self._child_documents[identifier] = document_id

        def finished(child):
            with self._lock:
                self._children.pop(identifier, None)
                self._child_documents.pop(identifier, None)

        try:
            raw_request = json.dumps(request).encode('utf-8')
            try:
                # Reuse the branch's bounded capture and owned process tree.
                # 复用新架构的有界捕获与进程树托管，Windows 硬退出也回收后代。
                raw, _, exit_code, truncated = run_captured(
                    [sys.executable, '-I', str(worker)], env=child_env,
                    input_data=raw_request + b'\n', timeout=VERIFICATION_TIMEOUT,
                    max_bytes=MAX_REPORT_BYTES, on_spawn=started, on_done=finished,
                )
            except subprocess.TimeoutExpired:
                raise VerificationTimeout('核验超过 60 秒上限，网络进程已终止。') from None
            else:
                if exit_code or truncated:
                    raise ValueError('外部核验已取消或组件返回无效结果，未保存报告。')
                report = json.loads(raw)
                if not isinstance(report, dict):
                    raise ValueError('核验组件返回无效结果。')
            with self._lock:
                if self._closed:
                    raise ValueError('应用正在关闭，核验结果未保存。')
                return report
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, AttributeError):
            raise ValueError('核验组件不可用或响应无效，请检查运行环境后重试。') from None

    def cancel_verifications(self):
        """Interrupt only network checks; preserve other IO shutdown contracts.

        关闭窗口时终止核验网络进程，其他本地 IO 仍按原生命周期收尾。
        """
        with self._lock:
            children = [child for key, child in self._children.items() if key.startswith('verify-')]
        for child in children:
            self._stop_verification(child)

    @staticmethod
    def _stop_verification(child):
        # The owned Job/POSIX session also handles descendants after parent exit.
        # 使用共用 Job / POSIX 会话，在父进程已退出时仍确认整棵进程树终止。
        stop_process(child)

    def import_research_report(self, document_id, path):
        if not isinstance(path, (str, Path)):
            raise ValueError('请指定本地研究报告 JSON 文件。')
        try:
            with Path(path).open('rb') as stream:
                raw = stream.read(MAX_REPORT_BYTES + 1)
            if len(raw) > MAX_REPORT_BYTES:
                raise ValueError('研究报告不能超过 1 MiB。')
            report = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError('研究报告文件不可读取或不是有效 UTF-8 JSON。') from None
        if not isinstance(report, dict) or report.get('kind') != 'research':
            raise ValueError('此入口只接受带来源的 research 报告，不接受外部评分或伪造 GitHub 快照。')
        document = self.verification_document(document_id)
        return self.store.save_verification(document['id'], report, document['sha256'])

    def verification_document(self, document_id):
        """Resolve fileless roots to a real PDF without inventing a digest.

        无文件书目只映射到真实主要 PDF，不生成虚构哈希；对话框固定该身份。
        """
        self.store.require_active(document_id)
        document = self.store.document(document_id)
        if not document.get('sha256'):
            identifier = self.store.primary_pdf_id(document_id)
            if not identifier:
                raise ValueError('外部核验需要绑定实际 PDF；请先为书目添加主要 PDF。')
            self.store.require_active(identifier)
            document = self.store.document(identifier)
        return document

    def list_verifications(self, document_id):
        document = self.verification_document(document_id)
        return self.store.list_verifications(document['id'])

    def verification_report(self, document_id, report_id):
        document = self.verification_document(document_id)
        return self.store.verification_report(document['id'], report_id)

    def export_verification(self, document_id, report_id, path):
        report = self.verification_report(document_id, report_id)
        data = json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')
        self.store.write_export(path, data, extra_protected=[self.resources])
        self.store.audit('external_verification_exported')
