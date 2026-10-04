# SPDX-License-Identifier: AGPL-3.0-only
"""Agent workbench services alongside the existing PDF workflows.
代理工作台服务；与现有 PDF 流程并存，复用本地存储和任务生命周期。
"""
import json
from pathlib import Path
import threading

from . import arxiv, secrets


class AgentWorkbenchService:
    """Share agent operations between desktop, CLI and MCP.
    桌面、CLI 和 MCP 共用代理操作，不复制业务规则。
    """

    def _with_scores(self, documents):
        """One list projection for browsing/search / 浏览与筛选共用单次评分查询。"""
        scores = self.store.scores_for_documents()
        for document in documents:
            document['scores'] = scores.get(document['id'], {})
        return documents

    def import_enriched_pdf(self, source, metadata, collection_id=None):
        """Import bytes, metadata and membership atomically.
        文件、元数据和集合归属在同一事务中导入。
        """
        with self._lock:
            if self._closed:
                raise ValueError('应用正在关闭。')
            return self.store.import_enriched_pdf(source, metadata, collection_id)

    def arxiv_lookup(self, query, endpoint=None):
        identifiers = arxiv.parse_identifiers(query)
        if not identifiers:
            raise ValueError('未识别到 arXiv 编号，请输入论文链接或编号。')
        if len(identifiers) > arxiv.MAX_ENTRIES:
            raise ValueError(f'一次最多查询 {arxiv.MAX_ENTRIES} 篇。')
        return arxiv.fetch_metadata(identifiers, endpoint or arxiv.API_URL)

    def arxiv_import(self, entries, collection_id=None):
        return arxiv.import_batch(self, entries, collection_id)

    def document(self, document_id):
        document = self.store.document(document_id)
        document['scores'] = {
            kind: (entry or {}).get('score')
            for kind, entry in self.document_scores(document_id).items()
        }
        return document

    def set_scores(self, document_id, entries):
        return self.store.set_scores(document_id, entries)

    def document_scores(self, document_id):
        return self.store.document_scores(document_id)

    def set_score_report(self, document_id, kind, agent_id, data_text):
        return self.store.set_score_report(document_id, kind, agent_id, data_text)

    def score_reports(self, document_id, kind, agent_id=None):
        return self.store.score_reports(document_id, kind, agent_id)

    def export_score_details(self, document_id, destination):
        self.store.require_active(document_id)
        body = json.dumps(self.document_scores(document_id), ensure_ascii=False, indent=2)
        self.store.write_export(destination, body.encode('utf-8'), extra_protected=[self.resources])

    def store_api_key(self, key, mode='os'):
        self.set_session_key(key)
        if mode == 'os':
            secrets.store_secret(key)
        elif mode == 'file':
            secrets.store_secret_file(key)
        else:
            raise ValueError('密钥保存方式无效。')

    def clear_stored_api_key(self):
        secrets.clear_secret()
        secrets.clear_secret_file()

    def api_key_stored(self):
        return bool(secrets.load_secret())

    def stored_key_location(self):
        if self.api_key_stored():
            return 'os'
        return 'file' if secrets.load_secret_file() else None

    def _effective_key(self):
        with self._lock:
            if self._key:
                return self._key
        return secrets.load_secret() or secrets.load_secret_file() or ''

    def read_artifact_html(self, job_id, artifact_index=0):
        """Read bounded managed HTML / 读取有界的受管 HTML 产物。"""
        artifact = self.store.artifact_path(job_id, artifact_index, kinds=('.html',))
        return artifact.read_text(encoding='utf-8')

    def document_translations(self, document_id):
        self.store.require_active(document_id)
        identifiers = set(self.store.document_family_ids(document_id))
        result = []
        for job in self.list_jobs():
            if job['state'] != 'completed' or job['documentId'] not in identifiers:
                continue
            for index, name in enumerate(job.get('artifacts', [])):
                try:
                    artifact = self.store.artifact_path(job['id'], index, kinds=('.pdf', '.html'))
                except ValueError:
                    continue
                result.append(dict(jobId=job['id'], index=index, path=str(artifact),
                                   createdAt=job.get('createdAt', ''), format=Path(name).suffix[1:]))
        return sorted(result, key=lambda value: value['createdAt'], reverse=True)

    def start_html_translation(self, document_id, pages=''):
        """HTML is a whole-paper workflow; never silently discard a PDF page range.
        HTML 按全文翻译，不静默忽略 PDF 页范围。
        """
        from .service import endpoint_url
        self.store.require_active(document_id)
        if pages:
            raise ValueError('HTML 翻译按全文处理，请清空页范围或选择 PDF 引擎。')
        settings = self.get_settings()
        endpoint = endpoint_url(settings['endpoint'])
        if settings.get('targetLanguage') != 'zh' or settings.get('sourceLanguage') != 'en':
            raise ValueError('HTML 引擎目前支持英文转中文，请选择相应语言或使用 PDF 引擎。')
        key = self._effective_key()
        if not key or not settings['model'].strip():
            raise ValueError('请先设置模型名称和 API 密钥。')
        identifier = arxiv.parse_identifier(self.store.document(document_id).get('url') or '')
        if not identifier:
            raise ValueError('HTML 翻译需要文献的 arXiv 链接。')
        with self._lock:
            if self._closed:
                raise ValueError('应用正在关闭。')
            job = self.store.new_job(document_id, 'html-llm')
            cancel = threading.Event()
            self._html_cancellations[job['id']] = cancel
            thread = threading.Thread(target=self._run_html_translation,
                args=(job.copy(), identifier, endpoint, settings['model'], key, cancel),
                daemon=True, name='polyscholar-html')
            self._threads[job['id']] = thread
            thread.start()
            return job

    def _run_html_translation(self, job, identifier, endpoint, model, key, cancel):
        try:
            job['state'] = 'running'
            self.store.put_job(job)
            self._notify_job(job)
            output = self.store.output_path(job)
            output.mkdir(parents=True, exist_ok=True)
            result = self._request_html(job, identifier, endpoint, model, key, cancel)
            html, total, translated = result['html'], result['total'], result['translated']
            if translated <= 0:
                raise ValueError('未获得任何翻译结果。')
            if cancel.is_set():
                raise InterruptedError()
            if not html.strip() or len(html.encode('utf-8')) > 100 * 1024 * 1024:
                raise ValueError('HTML 译文大小无效。')
            # Local managed output only; user exports use the protected atomic writer.
            # 只写托管产物；用户导出通过受保护的原子写入器。
            (output / 'translated.html').write_text(html, encoding='utf-8')
            job.update(state='completed', artifacts=['translated.html'], progress=100,
                       translatedBlocks=translated, totalBlocks=total, fallbackBlocks=total - translated)
        except InterruptedError:
            job.update(state='failed', errorCode='cancelled')
        except TimeoutError:
            job.update(state='failed', errorCode='timeout')
        except Exception:
            # Never persist provider bodies, credentials or raw paper text in errors.
            # 错误记录不保存供应商响应、密钥或论文原文。
            job.update(state='failed', errorCode='engine_failed')
        finally:
            key = ''
            try:
                self.store.put_job(job)
                self.store.audit('translation_finished', 'succeeded' if job['state'] == 'completed' else 'failed')
                self._notify_job(job)
            finally:
                # Storage failure must not leak worker ownership / 存储失败仍释放线程归属。
                with self._lock:
                    self._threads.pop(job['id'], None)
                    self._html_cancellations.pop(job['id'], None)
                    if self._closed and not self._threads:
                        self.store.close()

    def _request_html(self, job, identifier, endpoint, model, key, cancel):
        """Supervise network IO in an owned process with a whole-task deadline.
        网络 IO 置于受管进程，关闭及总超时都能终止尚未完成的请求。
        """
        import os
        import subprocess
        import sys
        from integrations.engines import limited_environment

        python = Path(sys.executable)
        if getattr(sys, 'frozen', False):
            python = self.resources / 'runtime/babeldoc' / ('python.exe' if os.name == 'nt' else 'bin/python3')
        worker = self.resources / 'integrations/html_worker.py'
        if not python.is_file() or not worker.is_file():
            raise ValueError('安装包缺少 HTML 翻译组件。')
        timeout = min(job['timeoutSeconds'], 3600)
        payload = json.dumps(dict(identifier=identifier, endpoint=endpoint, model=model,
                                  token=key, timeout=timeout)).encode('utf-8')
        if len(payload) > 1024 * 1024:
            raise ValueError('HTML 请求过大。')
        from integrations.managed_process import run_captured

        def started(child):
            with self._lock:
                if self._closed or cancel.is_set():
                    raise InterruptedError()
                self._children[job['id']] = child

        def finished(child):
            with self._lock:
                self._children.pop(job['id'], None)

        try:
            raw, _, code, truncated = run_captured(
                [str(python), '-I', str(worker)], env=limited_environment(),
                timeout=timeout, input_data=payload, max_bytes=100 * 1024 * 1024,
                on_spawn=started, on_done=finished)
            if cancel.is_set():
                raise InterruptedError()
            if code or truncated:
                raise ValueError('HTML 响应无效。')
            result = json.loads(raw)
            if (result.get('ok') is not True or not isinstance(result.get('html'), str)
                    or type(result.get('total')) is not int or type(result.get('translated')) is not int
                    or not 0 <= result['translated'] <= result['total']):
                raise ValueError('HTML 响应无效。')
            return result
        except subprocess.TimeoutExpired:
            raise TimeoutError() from None
        finally:
            payload = b''
