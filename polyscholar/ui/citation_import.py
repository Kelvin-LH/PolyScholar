# SPDX-License-Identifier: AGPL-3.0-only
"""本地引文文件先预览再显式导入；共享主窗口管理 IO 生命周期。

Preview local citation files before explicit import; the window owns IO lifecycle.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QPushButton, QFileDialog, QListWidget, QListWidgetItem,
    QTextEdit, QSplitter)
from .managed_dialog import ManagedIODialog
from .duplicates import FIELD_LABELS, readable_value
from .searches import TYPES

FORMATS = [('BibTeX','bibtex'), ('RIS','ris'), ('CSL-JSON','csl-json')]


def record_details(record):
    """完整字段使用已有姓名显示规则，不重做导入校验。

    Display all fields using shared creator presentation, without revalidating imports.
    """
    lines = [f"来源记录：{record['sourceId']}", '记录标题：'+record['title'],
             '有效记录' if record['valid'] else '无效记录，不能导入']
    metadata = record.get('metadata') or {}
    for key,value in metadata.items():
        if key == 'itemType':
            value = dict((value,label) for label,value in TYPES).get(value,value)
        lines.append(FIELD_LABELS.get(key,{'itemType':'条目类型','authors':'兼容姓名'}.get(key,key))+'：\n'+readable_value(key,value))
    lines.append('转换警告：\n'+('\n'.join(record['warnings']) or '无'))
    lines.append('错误：\n'+('\n'.join(record['errors']) or '无'))
    return '\n\n'.join(lines)


class CitationImportDialog(ManagedIODialog):
    def __init__(self,window,collection_id=None):
        super().__init__(window)
        self._preview = None
        self._preview_valid = False
        self._preview_source = None
        self._imported = False
        self._building = False
        self.setWindowTitle('导入本地引文')
        self.resize(980,700)
        layout = QVBoxLayout(self)
        heading = QLabel('导入本地引文')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        hint = QLabel('选择 BibTeX、RIS 或 CSL-JSON 本地文件，核对完整字段、作者和转换警告后勾选导入。仅创建无文件书目；不下载附件、不自动合并重复条目。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.format = QComboBox()
        self.format.setAccessibleName('引文文件格式')
        for label,value in FORMATS:
            self.format.addItem(label,value)
        form.addRow('显式文件格式',self.format)
        path_row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setAccessibleName('本地引文文件路径')
        self.path.setPlaceholderText('选择本机引文文件')
        self.browse_button = QPushButton('选择文件')
        self.browse_button.clicked.connect(self.browse)
        path_row.addWidget(self.path,1)
        path_row.addWidget(self.browse_button)
        form.addRow('本地文件',path_row)
        self.collection = QComboBox()
        self.collection.setAccessibleName('导入目标集合')
        self.collection.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.collection.setMinimumContentsLength(12)
        self.collection.addItem('未分类（不加入集合）',None)
        for item in self.window.service.list_collections():
            self.collection.addItem(item['name'],item['id'])
        self.collection.setCurrentIndex(max(0,self.collection.findData(collection_id)))
        form.addRow('目标集合',self.collection)
        layout.addLayout(form)
        self.preview_button = QPushButton('解析并预览')
        self.preview_button.clicked.connect(self.load)
        layout.addWidget(self.preview_button)
        self.status = QLabel('尚未预览；选择文件与格式后解析。')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        split = QSplitter()
        self.records = QListWidget()
        self.records.setAccessibleName('引文记录，勾选有效记录后导入')
        self.records.setMinimumWidth(220)
        self.records.currentRowChanged.connect(self.show_record)
        self.records.itemChanged.connect(self.selection_changed)
        split.addWidget(self.records)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName('完整元数据、作者身份、转换警告和错误')
        split.addWidget(self.details)
        split.setSizes([320,620])
        layout.addWidget(split,1)
        self.selection_info = QLabel('')
        self.selection_info.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_info.setWordWrap(True)
        layout.addWidget(self.selection_info)
        actions = QHBoxLayout()
        self.select_all_button = QPushButton('勾选全部有效记录')
        self.select_all_button.clicked.connect(self.select_valid)
        self.clear_button = QPushButton('取消全部勾选')
        self.clear_button.clicked.connect(self.clear_selection)
        self.import_button = QPushButton('导入勾选记录')
        self.import_button.clicked.connect(self.import_selected)
        close = QPushButton('关闭')
        close.clicked.connect(self.close)
        for button in (self.select_all_button,self.clear_button,self.import_button,close):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.path.textChanged.connect(self.source_changed)
        self.format.currentIndexChanged.connect(self.source_changed)
        self.collection.currentIndexChanged.connect(self.selection_changed)
        self.update_controls()

    def source(self):
        return self.path.text().strip(),self.format.currentData()

    def preview_current(self):
        return self._preview_valid and self._preview is not None and self._preview_source == self.source()

    def selected_indices(self):
        if not self._preview:
            return []
        return [self.records.item(row).data(Qt.ItemDataRole.UserRole)['index']
                for row in range(self.records.count())
                if self.records.item(row).data(Qt.ItemDataRole.UserRole)['valid']
                and self.records.item(row).checkState()==Qt.CheckState.Checked]

    def update_controls(self):
        if not hasattr(self,'import_button'):
            return
        busy = self._busy or self.window.io_worker is not None or self.window._closing or self._closed
        editable = not busy and not self._imported
        self.path.setReadOnly(not editable)
        for control in (self.format,self.browse_button,self.collection):
            control.setEnabled(editable)
        self.records.setEnabled(not busy)
        self.preview_button.setEnabled(editable and bool(self.source()[0]))
        for button in (self.select_all_button,self.clear_button):
            button.setEnabled(editable and self.preview_current())
        self.import_button.setEnabled(editable and self.preview_current() and bool(self.selected_indices()))
        self.selection_info.setText(f"勾选 {len(self.selected_indices())} 条；目标集合：{self.collection.currentText()}。"+('已完成导入，不能重复提交。' if self._imported else '导入前请逐条核对预览。'))

    def source_changed(self,*args):
        # 路径或格式变化保留草稿，但旧预览不能用于新来源提交。
        # Retain the draft on source changes, but never submit an old preview for a new source.
        if self._preview and not self.preview_current():
            self.show_status('文件路径或格式已改变，或上次解析失败；原预览保留，请重新解析后导入。')
        elif self.preview_current():
            self.show_status('当前路径与格式对应已解析预览；请核对字段与转换警告后导入。')
        self.update_controls()

    def browse(self):
        path,_ = QFileDialog.getOpenFileName(self,'选择本地引文文件','','引文文件 (*.bib *.ris *.json);;所有文件 (*)')
        if path:
            self.path.setText(path)

    def load(self):
        if self._busy or self.window.io_worker is not None or self._imported:
            return
        self._preview_valid = False
        source = self.source()
        def ready(preview):
            self._preview = preview
            self._preview_valid = True
            self._preview_source = source
            self._building = True
            self.records.clear()
            for record in preview['items']:
                prefix = '可导入' if record['valid'] else '错误'
                item = QListWidgetItem(f"{record['index']+1}. [{prefix}] {record['title'][:240]}")
                item.setData(Qt.ItemDataRole.UserRole,record)
                # Qt 的默认条目含可勾选标志；无效记录必须明确移除。
                # Qt's default item flags include checkability; remove it explicitly for invalid records.
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                if record['valid']:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Unchecked)
                self.records.addItem(item)
            self._building = False
            self.records.setCurrentRow(0)
            self.show_status(f"{preview['sourceName']}：共 {preview['total']} 条，有效 {preview['validCount']} 条，无效 {preview['invalidCount']} 条；文件级警告 {len(preview['warnings'])} 条，完整内容见右侧预览。")
            self.update_controls()
        self.run(lambda:self.window.service.preview_metadata_import(*source),ready,'正在本地解析引文文件…')

    def show_record(self,*args):
        item = self.records.currentItem()
        if not item:
            self.details.setPlainText('请在左侧选择记录查看完整字段。')
            return
        warnings = self._preview['warnings'] if self._preview else []
        prefix = '文件级警告：\n'+'\n'.join(warnings)+'\n\n' if warnings else ''
        self.details.setPlainText(prefix+record_details(item.data(Qt.ItemDataRole.UserRole)))

    def selection_changed(self,*args):
        if not self._building:
            self.update_controls()

    def set_selection(self,checked):
        self._building = True
        for row in range(self.records.count()):
            item = self.records.item(row)
            if item.data(Qt.ItemDataRole.UserRole)['valid']:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._building = False
        self.update_controls()

    def select_valid(self):
        self.set_selection(True)

    def clear_selection(self):
        self.set_selection(False)

    def import_selected(self):
        if not self.preview_current() or self._imported:
            return
        # 可信快照与选中身份由后端核验；写入是单事务，界面不重新解释作者或格式。
        # The backend verifies the trusted snapshot and selections, then writes one atomic transaction.
        preview = self._preview
        indices = self.selected_indices()
        collection_id = self.collection.currentData()
        def ready(result):
            self._imported = True
            for row in range(self.records.count()):
                item = self.records.item(row)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.window.refresh()
            self.show_status(f"已导入 {result['importedCount']} 条无文件书目。可在文献库编辑或添加真实 PDF。")
            self.update_controls()
        self.run(lambda:self.window.service.import_metadata_preview(preview,indices,collection_id),ready,'正在导入勾选书目…')
