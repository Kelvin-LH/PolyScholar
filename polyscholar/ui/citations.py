# SPDX-License-Identifier: AGPL-3.0-only
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QSplitter, QListWidget, QAbstractItemView, QTextEdit,
    QFileDialog, QDialog, QDialogButtonBox, QLabel, QVBoxLayout,
)

class CitationsPage:
    def citations(self):
        # Parent ownership keeps this modeless dialog inside the window lifecycle.
        # 非模态窗口由主窗口持有，刷新共享数据，关闭时随主窗口结束。
        self.citation_dialog = QDialog(self)
        self.citation_dialog.setWindowTitle('引用导出')
        self.citation_dialog.resize(760, 560)
        layout = QVBoxLayout(self.citation_dialog)
        hint = QLabel('从文献库选中的条目开始；可在左侧多选文献，预览并导出元数据。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.citation_format = QComboBox()
        self.citation_format.addItems(['CSL-JSON', 'BibTeX', 'RIS'])
        self.citation_format.currentTextChanged.connect(self.refresh_citation)
        layout.addWidget(self.citation_format)
        split = QSplitter()
        self.citation_items = QListWidget()
        self.citation_items.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.citation_items.itemSelectionChanged.connect(self.refresh_citation)
        split.addWidget(self.citation_items)
        self.citation_preview = QTextEdit()
        self.citation_preview.setReadOnly(True)
        split.addWidget(self.citation_preview)
        layout.addWidget(split, 1)
        self.citation_export_button = self.button('导出选中条目', self.export_citations, True)
        layout.addWidget(self.citation_export_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.citation_dialog.reject)
        layout.addWidget(buttons)

    def open_citations(self):
        if self._closing:
            return
        self.refresh_citation_items()
        selected = {
            item.data(0, Qt.ItemDataRole.UserRole)['id']
            for item in self.document_tree.selectedItems()
        }
        self.citation_items.blockSignals(True)
        for index in range(self.citation_items.count()):
            item = self.citation_items.item(index)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) in selected)
        self.citation_items.blockSignals(False)
        self.refresh_citation()
        self.citation_dialog.show()
        self.citation_dialog.raise_()
        self.citation_dialog.activateWindow()

    def citation_ids(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.citation_items.selectedItems()]

    def refresh_citation_items(self):
        selected = set(self.citation_ids())
        self.citation_items.blockSignals(True)
        self.citation_items.clear()
        for document in self.docs:
            self.citation_items.addItem(document['title'])
            item = self.citation_items.item(self.citation_items.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, document['id'])
            item.setSelected(document['id'] in selected)
        self.citation_items.blockSignals(False)
        self.refresh_citation()

    def citation_kind(self):
        return {'CSL-JSON':'csl-json','BibTeX':'bibtex','RIS':'ris'}[self.citation_format.currentText()]

    def refresh_citation(self, *args):
        identifiers = self.citation_ids()
        self.citation_export_button.setEnabled(bool(identifiers))
        text = (self.service.format_metadata(identifiers, self.citation_kind())
                if identifiers else '请在左侧选择要导出的文献。')
        self.citation_preview.setPlainText(text)

    def export_citations(self):
        identifiers = self.citation_ids()
        if not identifiers:
            return
        fmt = self.citation_kind()
        ext = {'csl-json': 'json', 'bibtex': 'bib', 'ris': 'ris'}[fmt]
        path, _ = QFileDialog.getSaveFileName(self, '导出元数据', 'references.' + ext)
        if path:
            self.guard(lambda: self.service.export_metadata(identifiers, fmt, path))

