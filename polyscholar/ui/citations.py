# SPDX-License-Identifier: AGPL-3.0-only
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QSplitter, QListWidget, QAbstractItemView, QTextEdit, QFileDialog

class CitationsPage:
    def citations(self):
        l=self.page('引用导出','选择本机条目，预览并导出文献元数据；完整 CSL 样式排版仍在研发。')
        self.citation_format=QComboBox();self.citation_format.addItems(['CSL-JSON','BibTeX','RIS']);self.citation_format.currentTextChanged.connect(self.refresh_citation);l.addWidget(self.citation_format)
        split=QSplitter();self.citation_items=QListWidget();self.citation_items.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);self.citation_items.itemSelectionChanged.connect(self.refresh_citation);split.addWidget(self.citation_items)
        self.citation_preview=QTextEdit();self.citation_preview.setReadOnly(True);split.addWidget(self.citation_preview);l.addWidget(split,1)
        self.citation_export_button=self.button('导出选中条目',self.export_citations,True);l.addWidget(self.citation_export_button)

    def citation_ids(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.citation_items.selectedItems()]

    def refresh_citation_items(self):
        selected=set(self.citation_ids());self.citation_items.blockSignals(True);self.citation_items.clear()
        for document in self.docs:
            self.citation_items.addItem(document['title']);item=self.citation_items.item(self.citation_items.count()-1);item.setData(Qt.ItemDataRole.UserRole,document['id']);item.setSelected(document['id'] in selected)
        self.citation_items.blockSignals(False);self.refresh_citation()

    def citation_kind(self):
        return {'CSL-JSON':'csl-json','BibTeX':'bibtex','RIS':'ris'}[self.citation_format.currentText()]

    def refresh_citation(self,*args):
        identifiers=self.citation_ids();self.citation_export_button.setEnabled(bool(identifiers))
        self.citation_preview.setPlainText(self.service.format_metadata(identifiers,self.citation_kind()) if identifiers else '请在左侧选择要导出的文献。')

    def export_citations(self):
        identifiers=self.citation_ids()
        if not identifiers:return
        fmt=self.citation_kind();ext={'csl-json':'json','bibtex':'bib','ris':'ris'}[fmt]
        path,_=QFileDialog.getSaveFileName(self,'导出元数据','references.'+ext)
        if path:self.guard(lambda:self.service.export_metadata(identifiers,fmt,path))

