# SPDX-License-Identifier: AGPL-3.0-only
"""本地回收站、删除范围预览及受管文件清理。

Local trash, deletion-scope previews and managed-file cleanup.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QTextEdit, QPushButton, QMessageBox)
from .managed_dialog import ManagedIODialog


def plain_question(parent, title, text):
    """用户元数据按纯文本显示，不参与确认框 HTML 解释。

    Render user metadata as plain text, never confirmation-dialog HTML.
    """
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


def preview_text(preview):
    """仅展示明确作用范围；外部来源文件始终保留。

    Display the explicit affected scope; external source files are preserved.
    """
    names = {'pdfs':'PDF 文件', 'notes':'本地笔记', 'claims':'证据 / 摘要',
             'jobs':'翻译任务', 'artifacts':'翻译产物', 'collections':'集合关系'}
    lines = [preview['title'], f"涉及 {len(preview['documentIds'])} 个条目身份"]
    lines.extend(f"{label}：{preview['counts'].get(key, 0)}" for key, label in names.items())
    if preview.get('activeJobs'):
        lines.append('存在活动任务，操作会被阻止。')
    lines.append('外部导入原文件会保留。')
    for path in preview.get('sourceFiles', [])[:10]:
        lines.append(f'保留原文件：{path}')
    if len(preview.get('sourceFiles', [])) > 10:
        lines.append('其余原文件同样保留。')
    return '\n'.join(lines)


def clear_affected_reader(window, identifiers):
    if window.reader_document and window.reader_document['id'] in identifiers:
        window.clear_reader()


class TrashMoveOperation(ManagedIODialog):
    """库中的默认删除使用安全预览，随后移至回收站。

    Default library deletion previews the scope before moving to trash.
    """
    def __init__(self, window, identifier):
        super().__init__(window)
        self.identifier = identifier

    def start(self):
        self.run(lambda:self.window.service.deletion_preview(self.identifier),
                 self.confirm, '正在核对移至回收站的范围…')

    def confirm(self, preview):
        message = preview_text(preview) + '\n\n移至回收站后可恢复，关系与受管文件会保留。继续？'
        if not plain_question(self.window, '移至回收站', message):
            self.close()
            return
        # 预览完成的回调仍占用共享 worker，延后到主窗口释放后再执行写入。
        # Preview callbacks still occupy the shared worker; write only after its owner releases it.
        self.defer_after_io(lambda:self.move(preview))

    def move(self, preview):
        def ready(_):
            clear_affected_reader(self.window, preview['documentIds'])
            self.window.refresh()
            self.window.io_status.setText('已移至回收站，可从回收站恢复。')
            self.close()
        self.run(lambda:self.window.service.trash_document(self.identifier),
                 ready, '正在移至回收站…')

    def failed(self, message):
        super().failed(message)
        self.close()


class TrashDialog(ManagedIODialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle('回收站')
        self.resize(900, 680)
        self._preview = None
        self._completion_message = ''
        layout = QVBoxLayout(self)
        heading = QLabel('回收站')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        hint = QLabel('主条目移入会隐藏其全部附件。恢复主条目不会恢复此前单独移入的附件。永久删除不可恢复，外部原文件会保留。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.items = QListWidget()
        self.items.setAccessibleName('回收站条目')
        self.items.currentItemChanged.connect(self.selection_changed)
        layout.addWidget(self.items, 1)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName('选中条目的删除范围预览')
        layout.addWidget(self.preview, 1)
        controls = QHBoxLayout()
        self.restore_button = QPushButton('恢复选中条目')
        self.restore_button.clicked.connect(self.restore)
        controls.addWidget(self.restore_button)
        self.purge_button = QPushButton('永久删除选中条目')
        self.purge_button.clicked.connect(self.purge)
        controls.addWidget(self.purge_button)
        self.refresh_button = QPushButton('刷新')
        self.refresh_button.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_button)
        layout.addLayout(controls)
        layout.addWidget(QLabel('永久删除后尚未清理的受管文件（无法恢复条目）'))
        self.pending = QListWidget()
        self.pending.setAccessibleName('待清理的受管文件')
        self.pending.setMaximumHeight(100)
        self.pending_empty = QLabel('当前没有待清理文件')
        layout.addWidget(self.pending_empty)
        self.pending.currentItemChanged.connect(self.update_controls)
        layout.addWidget(self.pending)
        self.retry_button = QPushButton('重试选中清理')
        self.retry_button.clicked.connect(self.retry)
        layout.addWidget(self.retry_button)
        self.update_controls()

    def selected(self):
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def update_controls(self, *args):
        if not hasattr(self, 'restore_button'):
            return
        busy = self._busy or self.window.io_worker is not None or self.window._closing or self._closed
        row = self.selected()
        checked = row and self._preview and self._preview['documentId'] == row['documentId']
        self.restore_button.setEnabled(bool(checked and not row.get('restoreBlocked')) and not busy)
        self.purge_button.setEnabled(bool(checked and self._preview.get('purgeAllowed')) and not busy)
        self.refresh_button.setEnabled(not busy)
        self.retry_button.setEnabled(self.pending.currentItem() is not None and not busy)
        self.items.setEnabled(not busy)
        self.pending.setEnabled(not busy)

    def refresh(self):
        def work():
            return self.window.service.list_trash(), self.window.service.list_pending_cleanup()
        self.run(work, self.show_rows, '正在读取本地回收站…')

    def show_rows(self, response):
        previous = self.selected()
        previous_id = previous['documentId'] if previous else None
        rows, pending = response
        self.items.blockSignals(True)
        self.items.clear()
        self._preview = None
        self.preview.clear()
        for row in rows:
            label = ('条目' if row['scope'] == 'root' else '附件') + ' · ' + row['title']
            if row.get('restoreBlocked'):
                label += ' · 请先恢复主条目'
            self.items.addItem(label)
            item = self.items.item(self.items.count()-1)
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setToolTip(label)
            if row['documentId'] == previous_id:
                self.items.setCurrentItem(item)
        if self.items.count() and self.items.currentRow() < 0:
            self.items.setCurrentRow(0)
        self.items.blockSignals(False)
        self.pending.clear()
        for row in pending:
            label = f"{row['title']} · {row['pathCount']} 个受管文件待清理"
            self.pending.addItem(label)
            item = self.pending.item(self.pending.count()-1)
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setToolTip(label)
        self.pending.setVisible(bool(pending))
        self.pending_empty.setVisible(not pending)
        if self.pending.count():
            self.pending.setCurrentRow(0)
        self.show_status(self._completion_message or f'回收站 {len(rows)} 项；待清理 {len(pending)} 项。')
        self.defer_after_io(self.load_preview)

    def selection_changed(self, *args):
        self._completion_message = ''
        self._preview = None
        self.preview.clear()
        self.defer_after_io(self.load_preview)
        self.update_controls()

    def load_preview(self):
        row = self.selected()
        if row is None:
            return
        identifier = row['documentId']
        def ready(preview):
            current = self.selected()
            if current and current['documentId'] == identifier:
                self._preview = preview
                self.preview.setPlainText(preview_text(preview))
                if current.get('restoreBlocked'):
                    self.show_status('该附件的主条目仍在回收站，请先恢复主条目。')
                else:
                    self.show_status(self._completion_message or '已核对选中条目的操作范围。')
                self.update_controls()
        self.run(lambda:self.window.service.deletion_preview(identifier),
                 ready, '正在核对选中条目范围…')

    def restore(self):
        row = self.selected()
        if not row or row.get('restoreBlocked'):
            self.show_status('请先恢复主条目。')
            return
        identifier = row['documentId']
        def ready(_):
            self._completion_message = '已恢复选中条目及其保留关系。'
            self.window.refresh()
            self.defer_after_io(self.refresh)
        self.run(lambda:self.window.service.restore_document(identifier),
                 ready, '正在恢复条目…')

    def purge(self):
        row = self.selected()
        if not row or not self._preview or self._preview['documentId'] != row['documentId']:
            return
        preview = self._preview
        if not preview.get('purgeAllowed'):
            self.show_status('该条目不满足永久删除条件。')
            return
        message = preview_text(preview) + '\n\n永久删除不可恢复。条目及上述关联数据会删除，随后清理受管副本。确认继续？'
        if not plain_question(self, '确认永久删除', message):
            return
        identifier = row['documentId']
        def ready(result):
            # 数据库删除与文件清理是不同状态；失败清理不能误称可恢复或已完成。
            # Database deletion and file cleanup differ; pending cleanup is neither restorable nor complete.
            self._completion_message = self.cleanup_message(result)
            clear_affected_reader(self.window, preview['documentIds'])
            self.window.refresh()
            self.defer_after_io(self.refresh)
        self.run(lambda:self.window.service.purge_document(identifier),
                 ready, '正在永久删除条目…')

    @staticmethod
    def cleanup_message(result):
        if result['cleanupComplete']:
            return '永久删除及受管文件清理已完成，外部原文件已保留。'
        return f"永久删除已开始，条目无法恢复；受管文件清理尚未完成，剩余 {result['remainingCleanup']} 项。请重试清理。"

    def retry(self):
        item = self.pending.currentItem()
        if not item:
            return
        identifier = item.data(Qt.ItemDataRole.UserRole)['id']
        def ready(result):
            self._completion_message = self.cleanup_message(result)
            self.defer_after_io(self.refresh)
        self.run(lambda:self.window.service.retry_cleanup(identifier),
                 ready, '正在重试受管文件清理…')
