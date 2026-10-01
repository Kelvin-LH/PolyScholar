# SPDX-License-Identifier: AGPL-3.0-only
"""Python desktop application services. Direct calls, no local web service."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .store import LocalStore

VERSIONS = {'babeldoc': ('babeldoc', '0.6.4'), 'pdfmathtranslate': ('pdf2zh', '1.9.11')}
MAX_EVENT = 1024 * 1024

def environment():
    return {k: os.environ[k] for k in ('PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP',
            'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if k in os.environ}

def app_data_dir():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'PolyScholar'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/PolyScholar'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'PolyScholar'

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
        self._threads = {}
        self._closed = False

    def list_documents(self):
        return self.store.list_documents()

    def import_pdf(self, path):
        return self.store.import_pdf(path)

    def update_document(self, document_id, patch):
        return self.store.update_document(document_id, patch)

    def delete_document(self, document_id):
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
            token = self._key if key is None else key
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
        return self.store.list_jobs()

    def start_translation(self, document_id, pages=''):
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
            if not self._key:
                raise ValueError('请先设置仅当前会话使用的 API 密钥。')
            document = self.store.document(document_id)
            job = self.store.new_job(document_id, settings['engine'])
            payload = dict(protocol_version=1, job_id=job['id'], engine=settings['engine'], python=discovery['pythonPath'],
                source=str(self.store.object_path(document)), output=job['outputDir'], endpoint=settings['endpoint'], model=settings['model'],
                api_key=self._key, source_language=settings['sourceLanguage'], target_language=settings['targetLanguage'], pages=pages,
                timeout=600, allow_document_upload=True, allow_asset_download=True)
            thread = threading.Thread(target=self._run, args=(job.copy(), payload, worker), daemon=True, name='polyscholar-engine')
            self._threads[job['id']] = thread
            thread.start()
            return job

    def _run(self, job, payload, worker):
        child = None
        try:
            job['state'] = 'running'
            self.store.put_job(job)
            Path(job['outputDir']).parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                if self._closed:
                    raise ValueError()
                child = subprocess.Popen([payload['python'], '-I', str(worker)], env=environment(),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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
                if event.get('event') == 'failed':
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
            job.update(state='failed', error='翻译任务未完成。请检查内置引擎、模型设置与网络后重试；已发送的 API 请求可能计费。')
        finally:
            payload['api_key'] = ''
            if child:
                try:
                    self._stop(child)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if child.stdout:
                    child.stdout.close()
            # Retain the thread until all database writes close, so shutdown can join it.
            try:
                self.store.put_job(job)
                self.store.audit('translation_finished', 'succeeded' if job['state'] == 'completed' else 'failed')
            finally:
                with self._lock:
                    self._children.pop(job['id'], None)
                    self._threads.pop(job['id'], None)

    @staticmethod
    def _stop(child):
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
        return self.store.export_translation(job_id, artifact_index, path)

    def export_metadata(self, document_ids, format, path):
        identifiers = [document_ids] if isinstance(document_ids, str) else document_ids
        if not isinstance(identifiers, list) or any(not isinstance(value, str) for value in identifiers):
            raise ValueError('文献导出列表无效。')
        documents = [self.store.document(value) for value in dict.fromkeys(identifiers)]
        bodies = [self._metadata(document, format) for document in documents]
        if format == 'csl-json':
            body = json.dumps([json.loads(value)[0] for value in bodies], ensure_ascii=False, indent=2)
        elif format in ('bibtex', 'ris'):
            body = ''.join(bodies)
        else:
            raise ValueError('不支持的元数据格式。')
        Path(path).write_text(body, encoding='utf-8')
        self.store.audit('citation_exported')

    def _metadata(self, document, format):
        authors = [a.strip() for a in document['authors'].split(';') if a.strip()]
        def line(value):
            return value.replace('\r', ' ').replace('\n', ' ')
        def bib(value):
            return ''.join('\\textbackslash{}' if c == '\\' else '\\' + c if c in '{}' else c for c in line(value))
        if format == 'csl-json':
            item = dict(id=document['id'], type='article-journal', title=document['title'], author=[{'literal': a} for a in authors])
            if document['doi']:
                item['DOI'] = document['doi']
            if re.fullmatch(r'\d{4}', document['year']):
                item['issued'] = {'date-parts': [[int(document['year'])]]}
            body = json.dumps([item], ensure_ascii=False, indent=2)
        elif format == 'bibtex':
            body = '@article{polyscholar_' + document['id'].replace('-', '_') + ',\n' + ',\n'.join(
                '  ' + key + ' = {' + bib(value) + '}' for key, value in [('title', document['title']), ('author', ' and '.join(authors)),
                ('year', document['year']), ('doi', document['doi'])]) + '\n}\n'
        elif format == 'ris':
            body = '\n'.join(['TY  - JOUR', 'TI  - ' + line(document['title'])] + ['AU  - ' + line(a) for a in authors] +
                             ['PY  - ' + line(document['year']), 'DO  - ' + line(document['doi']), 'ER  -']) + '\n'
        else:
            raise ValueError('不支持的元数据格式。')
        return body

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
