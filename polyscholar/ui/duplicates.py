# SPDX-License-Identifier: AGPL-3.0-only
"""人工核对重复书目及合并来源；不会自动合并候选。

Review duplicate bibliographic records and merge sources; candidates never merge automatically.
"""
import json
from html import escape
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QComboBox, QTextEdit, QTabWidget, QWidget, QAbstractItemView,
    QFormLayout, QScrollArea)
from .managed_dialog import ManagedIODialog
from .trash import plain_question

FIELD_LABELS = {
    'title':'标题', 'creators':'作者与编者', 'doi':'DOI', 'year':'年份',
    'publicationTitle':'期刊 / 论文集', 'publisher':'出版者', 'place':'出版地',
    'date':'日期', 'volume':'卷', 'issue':'期', 'pages':'页码', 'isbn':'ISBN',
    'edition':'版次', 'eventTitle':'会议名称', 'institution':'授予机构',
    'thesisType':'学位类型', 'notes':'本地笔记', 'tags':'标签',
}


def safe_tooltip(text):
    # Qt 提示按富文本显示时只允许转义后的元数据。
    # If Qt renders tooltips as rich text, metadata remains escaped literal text.
    return '<p>'+escape(text).replace('\n','<br>')+'</p>'


def readable_value(key, value):
    """显示完整姓名身份；完整姓名不会再猜测拆分。

    Display complete creator identity without guessing how literal names split.
    """
    if key == 'creators':
        lines = []
        for index, creator in enumerate(value, 1):
            role = '编者' if creator['role']=='editor' else '作者'
            kind = '机构' if creator['type']=='organization' else '个人'
            parts = [f'{index}. {role} / {kind}']
            for field, label in [('literal','完整名称'),('family','姓'),('given','名')]:
                if creator.get(field):
                    parts.append(label+'：'+creator[field])
            lines.append('\n'.join(parts))
        return '\n\n'.join(lines) or '（未填写）'
    if isinstance(value, list):
        return '、'.join(str(item) for item in value) or '（未填写）'
    return str(value) if value not in ('',None) else '（未填写）'


class MergeDialog(ManagedIODialog):
    def __init__(self, window, identifiers, candidates=None):
        super().__init__(window)
        self.identifiers = list(identifiers)
        self.candidates = candidates
        self._preview = None
        self._preview_valid = False
        self._merged_master_id = None
        self._draft_sources = {}
        self._building = False
        self.field_sources = {}
        self.setWindowTitle('核对并合并文献')
        self.resize(980,700)
        layout = QVBoxLayout(self)
        heading = QLabel('人工合并文献')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        hint = QLabel('仅合并同类型主条目。主记录原 PDF 保持；其他已有原 PDF 变为补充附件，其附件一并归入主记录。PDF 身份、证据和任务保留；标签与集合合并，本地笔记保留来源。没有撤销功能，原始文件不删除。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.master = QComboBox()
        self.master.setAccessibleName('保留的主记录')
        self.master.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.master.setMinimumContentsLength(10)
        self.master.currentIndexChanged.connect(self.master_changed)
        form = QFormLayout()
        form.addRow('保留主记录',self.master)
        layout.addLayout(form)
        self.sources_area = QScrollArea()
        self.sources_area.setWidgetResizable(True)
        self.sources_area.setMaximumHeight(190)
        layout.addWidget(self.sources_area)
        self.detail_field = QComboBox()
        self.detail_field.setAccessibleName('查看完整字段差异')
        self.detail_field.currentIndexChanged.connect(self.show_difference)
        layout.addWidget(self.detail_field)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName('完整字段差异与作者身份')
        layout.addWidget(self.details,1)
        self.merge_button = QPushButton('确认合并')
        self.merge_button.clicked.connect(self.merge)
        actions = QHBoxLayout()
        actions.addWidget(self.merge_button)
        self.reload_button = QPushButton('重新核对预览')
        self.reload_button.clicked.connect(self.load)
        actions.addWidget(self.reload_button)
        self.history_button = QPushButton('查看合并记录')
        self.history_button.clicked.connect(self.open_history)
        actions.addWidget(self.history_button)
        layout.addLayout(actions)
        self.update_controls()

    def update_controls(self,*args):
        if not hasattr(self,'merge_button'):
            return
        busy = self._busy or self.window.io_worker is not None or self.window._closing or self._closed
        finished = self._merged_master_id is not None
        self.master.setEnabled(not busy and not finished)
        self.sources_area.setEnabled(not busy and not finished)
        self.merge_button.setEnabled(not busy and self._preview_valid and self._preview is not None and self._preview['masterId']==self.master.currentData() and not self._preview.get('activeJobs'))
        self.reload_button.setEnabled(not busy and not finished)
        self.history_button.setEnabled(not busy and (self._merged_master_id is not None or self.master.currentData() is not None))

    def load(self):
        if self._merged_master_id is not None:
            return
        self._preview_valid = False
        master_id = self.master.currentData()
        self.run(lambda:self.window.service.merge_preview(self.identifiers,master_id=master_id),
                 self.show_preview,'正在核对合并范围与来源…')

    def show_preview(self, preview):
        self._preview = preview
        self._preview_valid = True
        self._building = True
        previous_field = self.detail_field.currentData()
        self.master.blockSignals(True)
        self.master.clear()
        for document in preview['documents']:
            self.master.addItem(document['title'],document['id'])
            self.master.setItemData(self.master.count()-1,safe_tooltip(document['title']),Qt.ItemDataRole.ToolTipRole)
        self.master.setCurrentIndex(self.master.findData(preview['masterId']))
        self.master.blockSignals(False)
        docs = {document['id']:document for document in preview['documents']}
        fields_widget = QWidget()
        form = QFormLayout(fields_widget)
        self.field_sources = {}
        self.detail_field.blockSignals(True)
        self.detail_field.clear()
        for key, values in preview['fields'].items():
            label = FIELD_LABELS.get(key,key)
            combo = QComboBox()
            combo.setAccessibleName(label+'的取值来源')
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
            for value in values:
                identifier = value['documentId']
                combo.addItem(docs[identifier]['title'],identifier)
                combo.setItemData(combo.count()-1,safe_tooltip(readable_value(key,value['value'])),Qt.ItemDataRole.ToolTipRole)
            chosen = self._draft_sources.get(key,preview['masterId'])
            combo.setCurrentIndex(max(0,combo.findData(chosen)))
            combo.currentIndexChanged.connect(lambda *_,field=key:self.source_changed(field))
            self.field_sources[key] = combo
            conflict = any(value['value'] != values[0]['value'] for value in values[1:])
            form.addRow(label+('（有差异）' if conflict else ''),combo)
            self.detail_field.addItem(label,key)
        self.detail_field.addItem('本地笔记（自动保留来源）','notes')
        self.detail_field.addItem('标签（自动合并）','tags')
        self.sources_area.setWidget(fields_widget)
        selected_index = self.detail_field.findData(previous_field)
        self.detail_field.setCurrentIndex(max(0,selected_index))
        self.detail_field.blockSignals(False)
        self._building = False
        self.show_difference()
        counts = preview['counts']
        self.show_status(f"涉及 {counts['pdfs']} 个 PDF、{counts['jobs']} 个任务、{counts['claims']} 条证据、{counts['artifacts']} 个产物、{counts['collections']} 个集合关系；独立回收站附件 {counts['trashedChildren']} 个保持原状态。"+('存在活动任务，请等待结束。' if preview['activeJobs'] else '请核对各字段来源。'))
        self.update_controls()

    def open_history(self):
        identifier = self._merged_master_id or self.master.currentData()
        if identifier is not None:
            self.history_dialog = MergeHistoryDialog(self.window,identifier)
            self.history_dialog.show()
            self.history_dialog.load()

    def failed(self,message):
        self._preview_valid = False
        super().failed(message)
        self.update_controls()

    def master_changed(self,*args):
        if not self._building:
            self.load()

    def source_changed(self,key):
        if self._building:
            return
        self._draft_sources[key] = self.field_sources[key].currentData()
        # 来源草稿保留，但重新获取可信版本，防止拿旧预览确认新数据。
        # Retain source drafts while refreshing the trusted revision before confirming new data.
        self.load()

    def show_difference(self,*args):
        if not self._preview:
            return
        key = self.detail_field.currentData()
        if key is None:
            return
        lines = []
        for document in self._preview['documents']:
            lines.append(document['title']+'\n'+readable_value(key,document.get(key,'')))
        # 不截断字段；独立滚动预览避免把长姓名或笔记隐藏成省略号。
        # Never truncate field values; scrolling reveals complete names and notes.
        self.details.setPlainText('\n\n────────\n\n'.join(lines))

    def merge(self):
        if self._preview is None or not self._preview_valid or self._preview['masterId']!=self.master.currentData():
            return
        preview = self._preview
        sources = {key:combo.currentData() for key,combo in self.field_sources.items()}
        master_id = self.master.currentData()
        message = f"主记录：{self.master.currentText()}\n合并 {len(preview['documentIds'])} 条文献，保留 {preview['counts']['pdfs']} 个 PDF 身份及其证据、任务。其他已有主 PDF 成为补充附件，无文件来源保留本地合并记录。没有撤销功能。\n\n确认使用已核对的字段来源进行合并？"
        if not plain_question(self,'确认人工合并',message):
            return
        def ready(result):
            reader = self.window.reader_document
            if reader and (reader['id'] in self.identifiers or reader.get('parentDocumentId') in self.identifiers):
                self.window.clear_reader()
            self.window.refresh()
            for index in range(self.window.document_list.count()):
                if self.window.document_list.item(index).data(Qt.ItemDataRole.UserRole)['id']==result['masterId']:
                    self.window.document_list.setCurrentRow(index)
                    break
            self.show_status('已合并，主记录及来源 PDF 身份保留。')
            # 合并后的源主条目已成为附件，旧预览仅供只读核对。
            # Source roots are now attachments; the old preview is review-only.
            self._merged_master_id = result['masterId']
            self._preview_valid = False
            self.update_controls()
            if self.candidates and not self.candidates._closed:
                self.defer_after_io(self.candidates.refresh)
        self.run(lambda:self.window.service.merge_documents(preview['documentIds'],master_id,
                    sources,expected_revision=preview['revision']),ready,'正在本地原子合并…')


class DuplicatesDialog(ManagedIODialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle('重复候选与人工合并')
        self.resize(940,700)
        layout = QVBoxLayout(self)
        heading = QLabel('重复候选与人工合并')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        hint = QLabel('候选仅供人工核对。预印本与正式版本不会自动合并，也可手动选择 2–20 个同类型主条目。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        self.pairs = QListWidget()
        self.pairs.setAccessibleName('重复候选列表')
        self.pairs.currentItemChanged.connect(self.update_controls)
        self.tabs.addTab(self.pairs,'检测候选')
        self.manual = QListWidget()
        self.manual.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.manual.setAccessibleName('手动多选待合并文献')
        self.manual.itemSelectionChanged.connect(self.update_controls)
        self.tabs.addTab(self.manual,'手动多选')
        self.tabs.currentChanged.connect(self.update_controls)
        layout.addWidget(self.tabs,1)
        actions = QHBoxLayout()
        self.preview_button = QPushButton('预览选中文献')
        self.preview_button.clicked.connect(self.open_preview)
        actions.addWidget(self.preview_button)
        self.history_button = QPushButton('查看选中条目的合并记录')
        self.history_button.clicked.connect(self.open_history)
        actions.addWidget(self.history_button)
        self.refresh_button = QPushButton('重新检测')
        self.refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_button)
        layout.addLayout(actions)
        self.update_controls()

    def update_controls(self,*args):
        if not hasattr(self,'preview_button'):
            return
        busy = self._busy or self.window.io_worker is not None or self.window._closing or self._closed
        allowed = self.pairs.currentItem() is not None if self.tabs.currentIndex()==0 else 2<=len(self.manual.selectedItems())<=20
        self.preview_button.setEnabled(not busy and allowed)
        self.history_button.setEnabled(not busy and self.tabs.currentIndex()==1 and len(self.manual.selectedItems())==1)
        self.refresh_button.setEnabled(not busy)
        self.tabs.setEnabled(not busy)

    def refresh(self):
        def work():
            return self.window.service.list_duplicate_candidates(),self.window.service.list_documents()
        self.run(work,self.show_rows,'正在本地核查重复候选…')

    def show_rows(self,result):
        candidates, documents = result
        docs = {document['id']:document for document in documents}
        self.pairs.clear()
        for row in candidates['items']:
            reasons = '、'.join({'doi':'DOI 相同','isbn':'ISBN 相同','title_creator_year':'标题与作者相同，年份相同或相邻'}.get(reason,reason) for reason in row['reasons'])
            label = ' / '.join(document['title'] for document in row['documents'])+' · '+reasons
            self.pairs.addItem(label)
            item = self.pairs.item(self.pairs.count()-1)
            item.setData(Qt.ItemDataRole.UserRole,row['documentIds'])
            item.setToolTip(safe_tooltip(label))
        selected_ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.manual.selectedItems()}
        self.manual.clear()
        for document in documents:
            self.manual.addItem(document['title'][:512])
            item = self.manual.item(self.manual.count()-1)
            item.setData(Qt.ItemDataRole.UserRole,document['id'])
            item.setToolTip(safe_tooltip(document['title']))
            item.setSelected(document['id'] in selected_ids)
        if self.pairs.count():
            self.pairs.setCurrentRow(0)
        self.show_status(f"发现 {candidates['total']} 对候选。"+('仅显示前部候选，请使用手动选择核对其余文献。' if candidates['truncated'] else '')+f"可手动选择 {len(documents)} 个本地主条目。")
        self.update_controls()

    def open_history(self):
        selected = self.manual.selectedItems()
        if len(selected)!=1:
            return
        identifier = selected[0].data(Qt.ItemDataRole.UserRole)
        self.history_dialog = MergeHistoryDialog(self.window,identifier)
        self.history_dialog.show()
        self.history_dialog.load()

    def open_preview(self):
        if self.tabs.currentIndex()==0:
            item = self.pairs.currentItem()
            identifiers = item.data(Qt.ItemDataRole.UserRole) if item else []
        else:
            identifiers = [item.data(Qt.ItemDataRole.UserRole) for item in self.manual.selectedItems()]
        if not 2<=len(identifiers)<=20:
            return
        self.merge_dialog = MergeDialog(self.window,identifiers,self)
        self.merge_dialog.show()
        self.merge_dialog.load()


class MergeHistoryDialog(ManagedIODialog):
    """按记录查看合并前本地快照，提供核对但不提供撤销。

    Inspect local pre-merge snapshots by record; this is review, not undo.
    """
    def __init__(self,window,identifier):
        super().__init__(window)
        self.identifier = identifier
        self.setWindowTitle('本地合并记录')
        self.resize(960,700)
        layout = QVBoxLayout(self)
        hint = QLabel('以下为合并前的本地元数据和关系快照，只供核对，没有撤销功能。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.records = QListWidget()
        self.records.setMaximumHeight(150)
        self.records.setAccessibleName('本地合并记录')
        self.records.currentItemChanged.connect(self.show_record)
        layout.addWidget(self.records)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName('合并前完整快照')
        layout.addWidget(self.details,1)
        self.update_controls()

    def update_controls(self,*args):
        if hasattr(self,'records'):
            self.records.setEnabled(not self._busy and self.window.io_worker is None and not self._closed)

    def load(self):
        self.run(lambda:self.window.service.list_merge_history(self.identifier),
                 self.show_rows,'正在读取本地合并记录…')

    def show_rows(self,rows):
        self.records.clear()
        self.details.clear()
        for row in rows:
            self.records.addItem(row['createdAt']+f" · {len(row['documentIds'])} 个原主条目")
            self.records.item(self.records.count()-1).setData(Qt.ItemDataRole.UserRole,row)
        self.show_status(f'本地合并记录 {len(rows)} 条。')
        if self.records.count():
            self.records.setCurrentRow(0)

    def show_record(self,*args):
        item = self.records.currentItem()
        self.details.setPlainText(json.dumps(item.data(Qt.ItemDataRole.UserRole),ensure_ascii=False,indent=2) if item else '')
