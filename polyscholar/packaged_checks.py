# SPDX-License-Identifier: AGPL-3.0-only
"""Offline synthetic checks for source and frozen runtimes.

源码及冻结运行环境的离线合成检查；不等同真实资料迁移或安装验收。
"""
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import threading
import time

from .service import LocalService
from .zotero_migration import ZoteroPdfValidator


class LocalImportChecks:
    """Exercise public import APIs with disposable local data.

仅使用可丢弃本地数据，验证公开导入接口及其持久化结果。
"""
    pdf_text = 'PolyScholar offline packaged import check'

    @staticmethod
    def require(condition, message):
        # Explicit failures also work with Python optimization enabled.
        # 显式抛错在 Python 优化模式下也生效。
        if not condition:
            raise RuntimeError(message)

    @staticmethod
    def report(stage):
        # Windowed builds may have no stdout; checks must still execute fully.
        # 无控制台构建可能没有 stdout，检查仍须完整执行。
        if sys.stdout is not None:
            print(stage, flush=True)

    def create_pdf(self, root):
        import pymupdf

        path = root / 'synthetic.pdf'
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), self.pdf_text)
            document.save(path)
        return path

    def import_citations(self, service, root):
        fixtures = (
            ('bibtex', 'synthetic.bib', 'Offline BibTeX title',
             '@article{synthetic, title={Offline BibTeX title}, '
             'author={Doe, Jane}, year={2024}}'),
            ('ris', 'synthetic.ris', 'Offline RIS title',
             'TY  - JOUR\nTI  - Offline RIS title\n'
             'AU  - Doe, Jane\nPY  - 2024\nER  -\n'),
            ('csl-json', 'synthetic.json', 'Offline CSL title',
             json.dumps([{
                 'id': 'source-only-id',
                 'type': 'article-journal',
                 'title': 'Offline CSL title',
                 'author': [{'family': 'Doe', 'given': 'Jane'}],
                 'issued': {'date-parts': [[2024]]},
             }])),
        )
        expected = {}
        for format_name, filename, title, content in fixtures:
            path = root / filename
            path.write_text(content, encoding='utf-8')
            preview = service.preview_metadata_import(path, format_name)
            items = preview['items']
            self.require(len(items) == 1 and items[0]['valid'],
                         'Citation preview did not produce one valid record')
            result = service.import_metadata_preview(
                preview, [items[0]['index']])
            self.require(result['importedCount'] == 1,
                         'Citation import count is incorrect')
            identifiers = result['documentIds']
            self.require(len(identifiers) == 1,
                         'Citation import did not return one local identity')
            expected[identifiers[0]] = title
        return expected

    def validate_pdf(self, root, pdf):
        validator = ZoteroPdfValidator()
        cancelled = threading.Event()
        try:
            valid, reason = validator.validate(
                pdf, cancelled, time.monotonic() + 30)
            self.require(valid and reason is None,
                         'Valid synthetic PDF was rejected')
            corrupt = root / 'corrupt.pdf'
            corrupt.write_bytes(b'This is synthetic invalid PDF content.\n')
            valid, reason = validator.validate(
                corrupt, cancelled, time.monotonic() + 30)
            self.require(not valid and reason == 'pdf-validation-failed',
                         'Corrupt PDF was not semantically rejected')
        finally:
            validator.close()

    def verify_persistence(self, service, pdf_id, expected):
        documents = service.list_documents()
        self.require(len(documents) == 4,
                     'Persisted library count is incorrect')
        self.require(sum(bool(item.get('hasPdf')) for item in documents) == 1,
                     'Citation-only records acquired a fabricated PDF')
        titles = {item['id']: item['title'] for item in documents}
        self.require(all(titles.get(key) == title
                         for key, title in expected.items()),
                     'Persisted citation titles are incorrect')
        revision = service.current_document_ir(pdf_id)
        self.require(revision is not None and revision['pageCount'] == 1
                     and revision['status'] == 'ready',
                     'One-page parsed IR was not persisted')
        blocks = service.document_blocks(pdf_id)
        self.require(any(self.pdf_text in block['text'] for block in blocks),
                     'Persisted PDF text is missing')

    def run(self):
        existing_children = {child.pid
                             for child in multiprocessing.active_children()}
        with tempfile.TemporaryDirectory(prefix='polyscholar-import-check-') as name:
            root = Path(name)
            data = root / 'data'
            pdf = self.create_pdf(root)
            service = LocalService(data_dir=data)
            try:
                document = service.import_pdf(pdf)
                pdf_id = document['id']
                service.parse_document(pdf_id)
                self.report('Local import check: PDF import and parse passed')
                expected = self.import_citations(service, root)
                expected[pdf_id] = document['title']
                self.report('Local import check: BibTeX RIS CSL-JSON passed')
                self.validate_pdf(root, pdf)
                self.report('Local import check: isolated PDF validation passed')
                self.verify_persistence(service, pdf_id, expected)
            finally:
                service.close()

            # Reopen through the same public API; in-memory results are insufficient.
            # 使用同一公开接口重开库，不能仅凭内存中的返回值认定持久化成功。
            service = LocalService(data_dir=data)
            try:
                self.verify_persistence(service, pdf_id, expected)
            finally:
                service.close()
            self.report('Local import check: restart persistence passed')
            self.require(not any(child.pid not in existing_children
                                 for child in multiprocessing.active_children()),
                         'Import validation left a live child process')
            self.report('Local import check: child process cleanup passed')


def run_local_import_checks():
    LocalImportChecks().run()
    LocalImportChecks.report('PolyScholar offline import checks passed')
