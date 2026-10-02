# SPDX-License-Identifier: AGPL-3.0-only
"""无文件书目的原生创建；元数据校验与集合事务由后端复用。

Native fileless bibliography creation; backend validation and collection transactions stay shared.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout, QFormLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QHBoxLayout)
from .managed_dialog import ManagedIODialog
from .searches import TYPES


class BibliographicDialog(ManagedIODialog):
    def __init__(self,window,collection_id=None):
        super().__init__(window)
        self.collection_id = collection_id
        self.created = None
        self.setWindowTitle('新建无文件书目')
        self.resize(650,400)
        layout = QVBoxLayout(self)
        heading = QLabel('新建无文件书目')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        hint = QLabel('先整理书目信息，可用于检索与引用。PDF 可稍后添加；没有 PDF 时不能阅读、解析或翻译。作者、出版信息、集合与标签可在文献库继续编辑。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.item_type = QComboBox()
        self.item_type.setAccessibleName('新书目类型')
        for label,value in TYPES:
            self.item_type.addItem(label,value)
        self.fields = {}
        form = QFormLayout()
        form.addRow('条目类型',self.item_type)
        for key,label in [('title','标题'),('doi','DOI（可选）'),('year','年份（可选）')]:
            editor = QLineEdit()
            editor.setAccessibleName(label)
            self.fields[key] = editor
            form.addRow(label,editor)
        layout.addLayout(form)
        self.create_button = QPushButton('创建书目')
        self.create_button.clicked.connect(self.create)
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.close)
        actions = QHBoxLayout()
        actions.addWidget(self.create_button)
        actions.addWidget(cancel)
        layout.addLayout(actions)
        self.update_controls()

    def update_controls(self):
        if not hasattr(self,'create_button'):
            return
        busy = self._busy or self.window.io_worker is not None or self.window._closing or self._closed
        self.create_button.setEnabled(not busy and self.created is None)
        self.item_type.setEnabled(not busy)
        for editor in self.fields.values():
            editor.setReadOnly(busy)

    def create(self):
        metadata = {key:editor.text().strip() for key,editor in self.fields.items()}
        metadata['itemType'] = self.item_type.currentData()
        def ready(document):
            self.created = document
            # 清除临时筛选以显示新书目；集合归属在同一后端事务中保留。
            # Clear transient filters to reveal the new item; collection membership remains atomic.
            self.window.refresh()
            self.window.clear_filters()
            for index in range(self.window.document_list.count()):
                if self.window.document_list.item(index).data(Qt.ItemDataRole.UserRole)['id']==document['id']:
                    self.window.document_list.setCurrentRow(index)
                    break
            self.show_status('已创建无文件书目；可继续编辑或添加真实 PDF。')
            self.update_controls()
        self.run(lambda:self.window.service.create_bibliographic_item(metadata,collection_id=self.collection_id),
                 ready,'正在本地创建书目…')
