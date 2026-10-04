# SPDX-License-Identifier: AGPL-3.0-only
"""Native arXiv import with shared worker ownership / 原生 arXiv 导入共用工作线程。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)
from .managed_dialog import ManagedIODialog
from .workers import safe_error


class ArxivImportDialog(ManagedIODialog):
    def __init__(self, window):
        super().__init__(window)
        self.entries = []
        self.collection_id = window.current_collection()
        self.setWindowTitle('从 arXiv 导入')
        self.resize(760, 640)
        self.setMinimumSize(560, 460)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        hint = QLabel('粘贴论文编号或链接，先预览，再导入所选正文。只向 arXiv 发送编号与下载请求。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        label = QLabel('论文编号或链接（每行一条）')
        self.input = QPlainTextEdit()
        self.input.setAccessibleName('arXiv 论文编号或链接')
        self.input.setPlaceholderText('2312.04567\nhttps://arxiv.org/abs/2312.04567v2\ncs/0301012')
        self.input.setMaximumHeight(150)
        label.setBuddy(self.input)
        layout.addWidget(label)
        layout.addWidget(self.input)
        self.fetch_button = QPushButton('预览元数据')
        self.fetch_button.setShortcut('Ctrl+Return')
        self.fetch_button.clicked.connect(self.start_fetch)
        layout.addWidget(self.fetch_button)
        self.preview = QListWidget()
        self.preview.setObjectName('panel')
        self.preview.setAccessibleName('arXiv 预览条目，空格键切换是否导入')
        self.preview.setWordWrap(True)
        self.preview.itemChanged.connect(self.update_controls)
        layout.addWidget(self.preview, 1)
        self.status = QLabel('尚未查询。每次最多预览 50 篇，获取结果后可取消勾选不需要的条目。')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.errors = QPlainTextEdit()
        self.errors.setReadOnly(True)
        self.errors.setAccessibleName('导入失败条目')
        self.errors.setMaximumHeight(120)
        self.errors.hide()
        layout.addWidget(self.errors)
        row = QHBoxLayout()
        self.import_button = QPushButton('导入所选并下载 PDF')
        self.import_button.setObjectName('primary')
        self.import_button.clicked.connect(self.start_import)
        row.addWidget(self.import_button)
        row.addStretch()
        self.cancel_button = QPushButton('关闭')
        self.cancel_button.clicked.connect(self.close)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.update_controls()

    def preview_items(self):
        return [self.preview.item(index) for index in range(self.preview.count())]

    def checked_entries(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.preview_items()
                if item.checkState() == Qt.CheckState.Checked]

    def update_controls(self, *_):
        if not hasattr(self, 'import_button'):
            return
        busy = self._busy or self.window.io_worker is not None or self.window._closing or self._closed
        self.input.setEnabled(not busy)
        self.fetch_button.setEnabled(not busy)
        self.preview.setEnabled(not busy)
        self.import_button.setEnabled(not busy and bool(self.checked_entries()))
        self.cancel_button.setText('关闭')

    def run_inline(self, work, ready, message):
        # Keep expected network failures inline; the main window still owns IO.
        # 网络失败在当前页反馈，线程与关窗等待仍由主窗口统一管理。
        def operation():
            try:
                return True, work()
            except Exception as error:
                return False, safe_error(error)

        def completed(result):
            succeeded, value = result
            if succeeded:
                ready(value)
            else:
                self.show_status(value)

        self.run(operation, completed, message)

    def start_fetch(self):
        if self._busy or self.window.io_worker is not None or self.window._closing:
            return
        text = self.input.toPlainText().strip()
        if not text:
            self.show_status('请先粘贴至少一个 arXiv 编号或链接。')
            self.input.setFocus()
            return
        # A failed refresh must not leave stale entries available for import.
        # 新查询先清空旧预览，防止失败后误导入上一批条目。
        self.entries = []
        self.preview.clear()
        self.errors.hide()
        self.run_inline(lambda: self.window.service.arxiv_lookup(text), self.fetched,
                        '正在查询 arXiv 元数据…')

    def fetched(self, entries):
        self.entries = entries
        self.preview.clear()
        for entry in entries:
            authors = '；'.join(entry.get('authors', [])[:4]) or '作者未记录'
            title = entry.get('title') or '标题未记录'
            date = entry.get('date') or '日期未记录'
            item = QListWidgetItem(f"{title}\n{authors} · {date} · {entry['identifier']}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(entry.get('abstract', '')[:800])
            self.preview.addItem(item)
        self.show_status(f'找到 {len(entries)} 篇；勾选要导入的条目。'
                         if entries else '没有找到匹配的论文。请检查编号后重试。')
        self.update_controls()

    def start_import(self):
        checked = self.checked_entries()
        if not checked:
            self.show_status('请先勾选要导入的条目。')
            return
        self.errors.hide()
        self.run_inline(
            lambda: self.window.service.arxiv_import(checked, self.collection_id),
            self.imported, f'正在下载并导入 {len(checked)} 篇 PDF…',
        )

    def imported(self, result):
        imported = result.get('imported', [])
        errors = result.get('errors', [])
        self.window.refresh()
        self.show_status(f'已导入 {len(imported)} 篇。'
                         + (f'另有 {len(errors)} 篇未完成，可检查后重试。' if errors else '可关闭窗口继续整理。'))
        self.errors.setPlainText('\n'.join(dict.fromkeys(errors)))
        self.errors.setVisible(bool(errors))
        if not errors:
            for item in self.preview_items():
                item.setCheckState(Qt.CheckState.Unchecked)
        self.update_controls()

    def reject(self):
        # Escape and the title-bar close use the same managed lifecycle.
        # Esc 与标题栏关闭共用生命周期，晚回调不再改动已关闭窗口。
        self.close()
