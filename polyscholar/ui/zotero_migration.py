# SPDX-License-Identifier: AGPL-3.0-only
"""本机 Zotero 迁移预览与持久收据；不执行源笔记 HTML。

Preview local Zotero migrations and durable receipts without executing source HTML.
"""
import json
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog,
    QListWidget, QListWidgetItem, QTextEdit, QSplitter, QTabWidget, QComboBox, QWidget,
)
from .managed_dialog import ManagedIODialog
from .duplicates import FIELD_LABELS, readable_value
from .searches import TYPES


def plain_json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def migration_details(record):
    status = '可作为原生书目使用' if record['status'] == 'native' else '以原始档案保留'
    lines = [f"标题：{record.get('title') or '未填写'}", f"来源类型：{record['itemType']}",
             f"迁移状态：{status}", '原回收状态：'+('在回收站' if record.get('deleted') else '活动条目')]
    type_names = {value: label for label, value in TYPES}
    for key, value in (record.get('metadata') or {}).items():
        if key == 'itemType':
            value = type_names.get(value, value)
        label = FIELD_LABELS.get(key, {'itemType': '条目类型', 'authors': '姓名', 'tags': '标签', 'notes': '笔记'}.get(key, key))
        lines.append(label + '：\n' + readable_value(key, value))
    lines.append('核对事项：\n' + ('\n'.join(record.get('warnings', [])) or '无'))
    lines.append(f"来源身份：{record['sourceId']} · {record.get('key', '')}")
    return '\n\n'.join(lines)


class ZoteroMigrationDialog(ManagedIODialog):
    PAGE_SIZE = 100

    def __init__(self, window):
        super().__init__(window)
        self._preview = None
        self._preview_source = None
        self._preview_valid = False
        self._selected = set()
        self._imported = False
        self._building = False
        self._page = 0
        self._archive = None
        self.setWindowTitle('从 Zotero 迁移')
        self.resize(1000, 720)
        layout = QVBoxLayout(self)
        heading = QLabel('从本机 Zotero 迁移')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        hint = QLabel('先退出 Zotero，再选择资料目录（包含 zotero.sqlite 和 storage）。核对后勾选迁移；关联子项随父条目纳入。暂不支持的内容另存为本地原始档案。原目录不改写；不下载链接附件、不自动合并重复条目。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        source = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText('选择本机 Zotero 资料目录')
        self.path.setAccessibleName('Zotero 资料目录')
        self.browse_button = QPushButton('选择目录')
        self.browse_button.clicked.connect(self.browse)
        self.preview_button = QPushButton('检查并预览')
        self.preview_button.clicked.connect(self.load)
        for widget in (self.path, self.browse_button, self.preview_button):
            source.addWidget(widget)
        layout.addLayout(source)
        self.status = QLabel('尚未检查资料目录。')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        records_page = QSplitter()
        self.records = QListWidget()
        self.records.setAccessibleName('迁移条目，默认不勾选')
        self.records.itemChanged.connect(self.selection_changed)
        self.records.currentItemChanged.connect(self.show_record)
        records_page.addWidget(self.records)
        self.details = self.text_area('来源完整字段与迁移状态')
        records_page.addWidget(self.details)
        records_page.setSizes([350, 620])
        self.tabs.addTab(records_page, '条目')
        self.overview = self.text_area('集合、附件及迁移警告')
        self.tabs.addTab(self.overview, '集合与附件')
        receipt_page = QVBoxLayout()
        receipt_widget = QWidget()
        receipt_widget.setLayout(receipt_page)
        receipt_actions = QHBoxLayout()
        self.receipts = QComboBox()
        self.receipts.setAccessibleName('已完成的迁移收据')
        self.receipts.addItem('选择迁移收据', None)
        self.receipts.currentIndexChanged.connect(self.load_receipt)
        self.refresh_receipts_button = QPushButton('刷新收据')
        self.refresh_receipts_button.clicked.connect(self.refresh_receipts)
        receipt_actions.addWidget(self.receipts, 1)
        receipt_actions.addWidget(self.refresh_receipts_button)
        receipt_page.addLayout(receipt_actions)
        resources = QHBoxLayout()
        self.archive_resources = QComboBox()
        self.archive_resources.setAccessibleName('可导出的本地档案资源')
        self.archive_resources.addItem('暂无可导出的档案资源', None)
        self.export_resource_button = QPushButton('导出档案文件')
        self.export_resource_button.clicked.connect(self.export_resource)
        self.archive_resources.currentIndexChanged.connect(self.update_controls)
        resources.addWidget(self.archive_resources, 1)
        resources.addWidget(self.export_resource_button)
        receipt_page.addLayout(resources)
        self.archive_sections = QComboBox()
        self.archive_sections.setAccessibleName('本地原始档案内容分组')
        self.archive_sections.currentIndexChanged.connect(self.show_archive)
        receipt_page.addWidget(self.archive_sections)
        self.archive_text = self.text_area('原始来源档案，纯文本查看')
        receipt_page.addWidget(self.archive_text, 1)
        self.tabs.addTab(receipt_widget, '迁移收据与原始档案')
        layout.addWidget(self.tabs, 1)
        pagination = QHBoxLayout()
        self.previous_button = QPushButton('上一页')
        self.previous_button.clicked.connect(lambda: self.change_page(-1))
        self.next_button = QPushButton('下一页')
        self.next_button.clicked.connect(lambda: self.change_page(1))
        self.page_info = QLabel('')
        for widget in (self.previous_button, self.page_info, self.next_button):
            pagination.addWidget(widget)
        pagination.addStretch()
        layout.addLayout(pagination)
        self.selection_info = QLabel('')
        self.selection_info.setWordWrap(True)
        layout.addWidget(self.selection_info)
        actions = QHBoxLayout()
        self.select_all_button = QPushButton('勾选全部可迁移条目')
        self.select_all_button.clicked.connect(self.select_all)
        self.clear_button = QPushButton('取消全部勾选')
        self.clear_button.clicked.connect(self.clear_selection)
        self.import_button = QPushButton('迁移勾选条目')
        self.import_button.clicked.connect(self.import_selected)
        self.cancel_button = QPushButton('取消当前迁移操作')
        self.cancel_button.clicked.connect(self.cancel)
        close_button = QPushButton('关闭')
        close_button.clicked.connect(self.close)
        for widget in (self.select_all_button, self.clear_button, self.import_button, self.cancel_button, close_button):
            actions.addWidget(widget)
        layout.addLayout(actions)
        self.path.textChanged.connect(self.source_changed)
        self.update_controls()

    @staticmethod
    def text_area(name):
        widget = QTextEdit()
        widget.setReadOnly(True)
        widget.setAccessibleName(name)
        return widget

    def preview_current(self):
        return self._preview_valid and self._preview is not None and self.path.text().strip() == self._preview_source

    def update_controls(self):
        if not hasattr(self, 'import_button'):
            return
        busy = self._busy or self.window.io_worker is not None or self.window._closing or self._closed
        editable = not busy and not self._imported
        self.path.setReadOnly(not editable)
        self.browse_button.setEnabled(editable)
        self.preview_button.setEnabled(editable and bool(self.path.text().strip()))
        self.records.setEnabled(not busy and not self._imported)
        current = editable and self.preview_current()
        self.select_all_button.setEnabled(current)
        self.clear_button.setEnabled(current)
        self.import_button.setEnabled(current and bool(self._selected))
        self.cancel_button.setEnabled(self._busy and not self._closed)
        self.refresh_receipts_button.setEnabled(not busy)
        self.receipts.setEnabled(not busy)
        self.archive_resources.setEnabled(not busy)
        self.export_resource_button.setEnabled(not busy and self.archive_resources.currentData() is not None)
        pages = max(1, math.ceil(len(self._preview['items']) / self.PAGE_SIZE)) if self._preview else 1
        self.previous_button.setEnabled(not busy and self._page > 0)
        self.next_button.setEnabled(not busy and self._page + 1 < pages)
        self.page_info.setText(f'第 {self._page + 1} / {pages} 页，每页 {self.PAGE_SIZE} 条')
        self.selection_info.setText(f'勾选 {len(self._selected)} 条，关联子项随父条目纳入。'+('本次迁移已完成。' if self._imported else '暂不支持的内容以原始档案保留，不能当作已恢复的原生功能。'))

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, '选择 Zotero 资料目录')
        if path:
            self.path.setText(path)

    def source_changed(self):
        if self._preview and not self.preview_current():
            self.show_status('目录已改变或上次检查失败；请重新检查后迁移。原预览保留。')
        self.update_controls()

    def load(self):
        if self._busy or self.window.io_worker is not None or self._imported:
            return
        self._preview_valid = False
        source = self.path.text().strip()
        def ready(preview):
            self._preview = preview
            self._preview_source = source
            self._preview_valid = True
            self._selected.clear()
            self._page = 0
            self.show_page()
            self.overview.setPlainText(self.overview_text(preview))
            self.show_status('目录检查完成。请查看条目状态、集合及附件报告后勾选迁移。')
            self.update_controls()
        self.run(lambda: self.window.service.preview_zotero_migration(source), ready, '正在复制并检查本地资料副本…')

    def show_page(self):
        self._building = True
        self.records.clear()
        start = self._page * self.PAGE_SIZE
        for record in self._preview['items'][start:start + self.PAGE_SIZE]:
            status = '原生书目' if record['status'] == 'native' else '原始档案'
            item = QListWidgetItem(f"[{status}] {record.get('title') or record.get('itemType', '')}")
            item.setData(Qt.ItemDataRole.UserRole, record)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            if record.get('selectable', True):
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if record['sourceId'] in self._selected else Qt.CheckState.Unchecked)
            self.records.addItem(item)
        self._building = False
        self.records.setCurrentRow(0)
        self.update_controls()

    def change_page(self, delta):
        self._page += delta
        self.show_page()

    def show_record(self, *args):
        item = self.records.currentItem()
        self.details.setPlainText(migration_details(item.data(Qt.ItemDataRole.UserRole)) if item else '请选择条目。')

    @staticmethod
    def overview_text(preview):
        names = {'items': '来源条目', 'native': '可用书目', 'archive': '原始档案条目',
                 'pdfs': '已验证 PDF', 'resources': '来源附件', 'missing': '文件缺失',
                 'unsupported': '尚未复制的资源'}
        lines = [preview['sourceName'], '目录检查统计：']
        lines.extend(f'{names.get(key, key)}：{value}' for key, value in preview['counts'].items())
        lines.extend(['', '核对事项：', *preview['warnings'], '', '集合：'])
        for item in preview.get('collections', []):
            lines.append(f"{item.get('collectionName', '')}（来源 {item['collectionID']}，父集合 {item.get('parentCollectionID') or '无'}）")
        lines.extend(['', '附件：'])
        for item in preview.get('resources', []):
            lines.append(f"{item.get('filename') or item.get('path') or '未命名'} · {item.get('reason', '')}")
        return '\n'.join(lines)

    def selection_changed(self, item):
        if self._building:
            return
        identity = item.data(Qt.ItemDataRole.UserRole)['sourceId']
        if item.checkState() == Qt.CheckState.Checked:
            self._selected.add(identity)
        else:
            self._selected.discard(identity)
        self.update_controls()

    def select_all(self):
        self._selected = {record['sourceId'] for record in self._preview['items'] if record.get('selectable', True)}
        self.show_page()

    def clear_selection(self):
        self._selected.clear()
        self.show_page()

    def import_selected(self):
        if not self.preview_current() or self._imported or not self._selected:
            return
        preview, selected = self._preview, sorted(self._selected)
        def ready(receipt):
            self._imported = True
            self.window.refresh()
            self.display_archive(receipt)
            self.tabs.setCurrentIndex(2)
            self.show_status('迁移已保存。请核对收据中的原生条目、附件及原始档案数量。')
            self.update_controls()
            self.defer_after_io(lambda: self.open_receipt(receipt['id']))
        self.run(lambda: self.window.service.import_zotero_preview(preview, selected), ready, '正在迁移选中条目并保存本地收据…')

    def cancel(self):
        # 取消由后端检查提交边界；提交后显示真实收据，不能谎报回滚。
        # The backend checks cancellation before commit; a completed commit remains a real success.
        self.window.service.cancel_zotero_migration()
        self.show_status('已请求取消，正在等待操作安全结束；已提交的迁移会保留收据。')

    def refresh_receipts(self):
        def ready(receipts):
            self.receipts.blockSignals(True)
            self.receipts.clear()
            self.receipts.addItem('选择迁移收据', None)
            for receipt in receipts:
                self.receipts.addItem(str(receipt.get('sourceName', receipt['id'])) + ' · ' + str(receipt['id']), receipt['id'])
            self.receipts.blockSignals(False)
            self.show_status(f'已读取 {len(receipts)} 份本地迁移收据。')
        self.run(self.window.service.list_zotero_migrations, ready, '正在读取本地迁移收据…')

    def load_receipt(self):
        identity = self.receipts.currentData()
        if identity is not None:
            self.open_receipt(identity)

    def open_receipt(self, identity):
        self.run(lambda: self.window.service.read_zotero_migration(identity), self.display_archive, '正在读取本地原始档案…')

    def display_archive(self, archive):
        # 逐组分页展示完整档案，避免一次渲染巨大 HTML 或悄悄截断内容。
        # Page complete archive groups without rendering huge HTML or silently truncating it.
        self._archive = {}
        self.archive_resources.blockSignals(True)
        self.archive_resources.clear()
        self.archive_resources.addItem('选择本地档案资源导出', None)
        receipt = archive.get('receipt', {})
        for resource in archive.get('archive', {}).get('resources', []):
            if resource.get('archivedPath'):
                self.archive_resources.addItem(resource.get('filename', resource['sourceId']),
                                               (receipt['id'], resource['sourceId'], resource.get('filename', 'resource.bin')))
        self.archive_resources.blockSignals(False)
        def add_groups(prefix, value):
            if isinstance(value, dict):
                for key, child in value.items():
                    add_groups(f'{prefix}/{key}' if prefix else key, child)
            elif isinstance(value, list) and len(value) > self.PAGE_SIZE:
                for start in range(0, len(value), self.PAGE_SIZE):
                    add_groups(f'{prefix} [{start + 1}–{min(len(value), start + self.PAGE_SIZE)}/{len(value)}]', value[start:start + self.PAGE_SIZE])
            else:
                content = plain_json(value)
                # 大单字段也分段，标明总量；不丢失来源全文。
                # Split even one large field with explicit offsets; retain every source character.
                limit = 65536
                if len(content) > limit:
                    for start in range(0, len(content), limit):
                        self._archive[f'{prefix} 字符[{start + 1}–{min(len(content), start + limit)}/{len(content)}]'] = content[start:start + limit]
                else:
                    self._archive[prefix] = content
        add_groups('', archive)
        self.archive_sections.blockSignals(True)
        self.archive_sections.clear()
        for key in self._archive:
            self.archive_sections.addItem(key, key)
        self.archive_sections.blockSignals(False)
        self.show_archive()
        self.update_controls()

    def export_resource(self):
        resource = self.archive_resources.currentData()
        if resource is None or self._busy or self.window.io_worker is not None:
            return
        receipt_id, source_id, filename = resource
        destination, _ = QFileDialog.getSaveFileName(self, '导出本地档案资源', filename)
        if destination:
            self.run(lambda: self.window.service.export_zotero_resource(receipt_id, source_id, destination),
                     lambda _: self.show_status('档案文件已导出；文件内容未执行。'), '正在校验并导出本地档案文件…')

    def show_archive(self):
        key = self.archive_sections.currentData()
        self.archive_text.setPlainText(self._archive[key] if self._archive and key in self._archive else '')
