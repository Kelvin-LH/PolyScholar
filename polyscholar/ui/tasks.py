# SPDX-License-Identifier: AGPL-3.0-only
"""Native translation queue / 原生翻译队列，复用服务校验与导出。"""
from PySide6.QtWidgets import (
    QHBoxLayout, QComboBox, QLineEdit, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QInputDialog, QFileDialog,
)
from ..arxiv import parse_identifier
from .workers import valid_progress, valid_usage


class TasksPage:
    def tasks(self):
        layout = self.page('翻译任务', '选择文献和翻译方式。内容将发送至配置的模型 API。')
        row = QHBoxLayout()
        self.task_doc = QComboBox()
        self.task_doc.setAccessibleName('待翻译文献')
        row.addWidget(self.task_doc, 2)
        self.task_mode = QComboBox()
        self.task_mode.addItem('PDF · 设置中的引擎', 'pdf')
        self.task_mode.addItem('arXiv HTML · 英文转中文', 'html-llm')
        self.task_mode.setAccessibleName('翻译方式')
        row.addWidget(self.task_mode, 1)
        layout.addLayout(row)
        controls = QHBoxLayout()
        self.pages = QLineEdit()
        self.pages.setPlaceholderText('页范围，如 1-5,8；留空为全部页')
        self.pages.setAccessibleName('PDF 翻译页范围')
        controls.addWidget(self.pages, 1)
        self.create_translation_button = self.button('开始翻译', self.start_job, True)
        controls.addWidget(self.create_translation_button)
        layout.addLayout(controls)
        self.task_empty = QLabel()
        self.task_empty.setWordWrap(True)
        layout.addWidget(self.task_empty)
        self.send_boundary = QLabel()
        self.send_boundary.setWordWrap(True)
        layout.addWidget(self.send_boundary)
        self.pages.textChanged.connect(self.refresh_boundary)
        self.task_mode.currentIndexChanged.connect(self.refresh_boundary)
        self.task_mode.currentIndexChanged.connect(self.refresh_task_items)
        self.refresh_boundary()
        self.job_table = QTableWidget(0, 6)
        self.job_table.setHorizontalHeaderLabels(['文献', '引擎', '状态', '进度', '用量', '操作'])
        self.job_table.setAccessibleName('翻译任务列表')
        header = self.job_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.job_table, 1)

    def refresh_task_items(self):
        previous = self.task_doc.currentData()
        self.task_doc.blockSignals(True)
        self.task_doc.clear()
        choices = self.pdf_choices()
        if self.task_mode.currentData() == 'html-llm':
            choices = [(document['title'], document['id']) for document in self.docs
                       if parse_identifier(document.get('url') or '')]
        for label, identifier in choices:
            self.task_doc.addItem(label, identifier)
        index = self.task_doc.findData(previous)
        if index >= 0:
            self.task_doc.setCurrentIndex(index)
        self.task_doc.blockSignals(False)
        self.update_task_controls()

    def update_task_controls(self):
        available = self.task_doc.count() > 0
        self.create_translation_button.setEnabled(available and self.io_worker is None and not self._closing)
        message = ('没有带 arXiv 链接的文献，请导入或填写文献链接。'
                   if self.task_mode.currentData() == 'html-llm'
                   else '没有可用 PDF，请在文献库添加 PDF 后再翻译。')
        self.task_empty.setText(message)
        self.task_empty.setVisible(not available)

    def start_job(self):
        document_id = self.task_doc.currentData()
        if not document_id:
            return
        operation = (self.service.start_html_translation if self.task_mode.currentData() == 'html-llm'
                     else self.service.start_translation)
        self.guard(lambda: operation(document_id, self.pages.text().strip()))
        self.refresh_jobs()

    def refresh_jobs(self):
        self.jobs = self.service.list_jobs()
        self.job_table.clearContents()
        self.job_table.setRowCount(len(self.jobs))
        names = {identifier: label for label, identifier in self.pdf_choices()}
        names.update({document['id']: document['title'] for document in self.docs})
        states = {'queued': '等待中', 'running': '翻译中', 'completed': '已完成', 'failed': '失败'}
        for row, job in enumerate(self.jobs):
            state = states.get(job.get('state'), job.get('state', ''))
            if job.get('fallbackBlocks'):
                state = f"部分完成 · {job['fallbackBlocks']} 段保留原文"
            values = [names.get(job.get('documentId'), '文献'), job.get('engine', ''), state,
                      valid_progress(job.get('progress')), valid_usage(job.get('usage'))]
            for column, value in enumerate(values):
                self.job_table.setItem(row, column, QTableWidgetItem(value))
            if job.get('state') == 'completed' and job.get('artifacts'):
                button = self.button('导出译文', lambda checked=False, job=job: self.export_translation(job))
                self.job_table.setCellWidget(row, 5, button)
            elif job.get('state') == 'failed':
                self.job_table.setItem(row, 5, QTableWidgetItem(job.get('errorCode') or '请检查模型设置'))
        self.refresh_reader_choices()

    def refresh_boundary(self, *args):
        settings = self.service.get_settings()
        html_mode = self.task_mode.currentData() == 'html-llm'
        self.pages.setEnabled(not html_mode)
        if html_mode:
            # A disabled stale page range must not reach the whole-paper engine.
            # 禁用后的旧页范围不能传给按全文工作的 HTML 引擎。
            self.pages.blockSignals(True)
            self.pages.clear()
            self.pages.blockSignals(False)
            message = 'HTML 按全文翻译（英文转中文），保留公式。未翻译段落会标明保留原文。'
        else:
            pages = self.pages.text().strip() or '全部页'
            message = f'请求范围：{pages}。上游引擎可能解析整篇 PDF，选页不保证其他页不会发送。'
        self.send_boundary.setText(f"模型 API：{settings.get('endpoint', '')}\n{message}\n允许必要模型/字体下载；远程请求可能计费。")

    def export_translation(self, job):
        index = 0
        if len(job['artifacts']) > 1:
            name, accepted = QInputDialog.getItem(self, '选择导出文件', '翻译结果', job['artifacts'], 0, False)
            if not accepted:
                return
            index = job['artifacts'].index(name)
        name = job['artifacts'][index]
        file_filter = 'HTML (*.html)' if name.lower().endswith('.html') else 'PDF (*.pdf)'
        destination, _ = QFileDialog.getSaveFileName(self, '导出翻译结果', name, file_filter)
        if destination:
            self.guard(lambda: self.service.export_translation(job['id'], index, destination))
