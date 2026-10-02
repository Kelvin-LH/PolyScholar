# SPDX-License-Identifier: AGPL-3.0-only
"""Local text extraction and manually authored, traceable evidence notes."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QLabel, QComboBox, QListWidget, QListWidgetItem,
                              QTextEdit, QSplitter, QWidget, QVBoxLayout, QHBoxLayout)

class SummaryPage:
    def summary(self):
        layout=self.page('本地证据笔记','提取 PDF 自带文本并保存带原文引用的手写笔记。引用关联不代表结论已验证；不生成模型摘要。')
        self.evidence_doc=QComboBox();layout.addWidget(self.evidence_doc)
        actions=QHBoxLayout()
        self.parse_button=self.button('提取本地文本',self.parse_evidence,True)
        self.locate_button=self.button('在原文中定位',self.locate_evidence)
        actions.addWidget(self.parse_button);actions.addWidget(self.locate_button);actions.addStretch();layout.addLayout(actions)
        self.evidence_status=QLabel('选择本地文献后提取文本。');self.evidence_status.setWordWrap(True);layout.addWidget(self.evidence_status)
        split=QSplitter();self.evidence_blocks=QListWidget();split.addWidget(self.evidence_blocks)
        detail=QWidget();column=QVBoxLayout(detail);column.setContentsMargins(0,0,0,0)
        self.evidence_text=QTextEdit();self.evidence_text.setReadOnly(True);self.evidence_text.setPlaceholderText('选择文本块；选中需要引用的原文。');column.addWidget(self.evidence_text,1)
        self.claim_text=QTextEdit();self.claim_text.setPlaceholderText('输入自己的证据笔记（不会自动判断真实性）');self.claim_text.setMaximumHeight(120);column.addWidget(self.claim_text)
        self.save_claim_button=self.button('保存笔记与选中原文',self.save_evidence_claim);column.addWidget(self.save_claim_button)
        split.addWidget(detail);split.setStretchFactor(1,2);layout.addWidget(split,1)
        self.evidence_claims=QListWidget();self.evidence_claims.setMaximumHeight(160);layout.addWidget(QLabel('已保存笔记 · 关联状态'));layout.addWidget(self.evidence_claims)
        self.saved_quote=QTextEdit();self.saved_quote.setReadOnly(True);self.saved_quote.setMaximumHeight(100);layout.addWidget(self.saved_quote)
        self.saved_locate=self.button('查看笔记保留的原文',self.locate_saved_claim);layout.addWidget(self.saved_locate)
        self.evidence_claims.currentItemChanged.connect(self.show_saved_claim)
        self._evidence_loaded_id=None
        self.evidence_doc.currentIndexChanged.connect(self.load_evidence)
        self.evidence_blocks.currentItemChanged.connect(self.show_evidence_block)
        self.evidence_text.selectionChanged.connect(self.update_evidence_controls)
        self.claim_text.textChanged.connect(self.update_evidence_controls)
        self.update_evidence_controls()

    def refresh_summary_items(self):
        previous=self.evidence_doc.currentData();self.evidence_doc.blockSignals(True);self.evidence_doc.clear()
        for document in self.docs:self.evidence_doc.addItem(document['title'],document['id'])
        index=self.evidence_doc.findData(previous)
        if index>=0:self.evidence_doc.setCurrentIndex(index)
        self.evidence_doc.blockSignals(False);self.load_evidence()

    def load_evidence(self,*args):
        identifier=self.evidence_doc.currentData()
        if identifier!=self._evidence_loaded_id:self.claim_text.clear()
        self._evidence_loaded_id=identifier;self.evidence_blocks.clear();self.evidence_text.clear();self.evidence_claims.clear()
        if not identifier:
            self.evidence_status.setText('请先导入本地 PDF。');self.update_evidence_controls();return
        def load():
            revision=self.service.current_document_ir(identifier)
            blocks=self.service.document_blocks(identifier)
            for block in blocks:
                item=QListWidgetItem(f"第 {block['pageNumber']} 页 · {block['text'][:100].replace(chr(10),' ')}")
                item.setData(Qt.ItemDataRole.UserRole,block);self.evidence_blocks.addItem(item)
            if revision and revision.get('status',revision.get('state'))=='no_text':
                self.evidence_status.setText('此 PDF 没有可提取文本，可能为扫描件。本功能不执行 OCR，无法建立文本证据。')
            elif blocks:self.evidence_status.setText(f'已提取 {len(blocks)} 个文本块。选中原文并输入笔记后保存；数据保存在本机。')
            else:self.evidence_status.setText('尚未提取文本。点击“提取本地文本”。')
            self.refresh_evidence_claims(identifier)
        self.guard(load)
        if self.evidence_blocks.count():self.evidence_blocks.setCurrentRow(0)
        self.update_evidence_controls()

    def refresh_evidence_claims(self,identifier):
        self.evidence_claims.clear()
        for claim in self.service.list_claims(identifier):
            stale=claim.get('stale') or claim.get('status')=='stale'
            label='引用已失效，请核对' if stale else ('缺少原文证据' if claim.get('status')=='insufficient_evidence' else '原文已关联 · 未验证结论')
            item=QListWidgetItem(f"{label}\n{claim['text']}");item.setData(Qt.ItemDataRole.UserRole,claim)
            self.evidence_claims.addItem(item)

    def show_evidence_block(self,*args):
        item=self.evidence_blocks.currentItem();block=item.data(Qt.ItemDataRole.UserRole) if item else None
        self.evidence_text.setPlainText(block['text'] if block else '');self.update_evidence_controls()

    def show_saved_claim(self,*args):
        item=self.evidence_claims.currentItem();claim=item.data(Qt.ItemDataRole.UserRole) if item else None
        excerpts=[]
        if claim:
            if claim.get('stale'):excerpts.append('旧解析版本的引用，未自动更新。')
            excerpts.extend(f"第 {e['pageNumber']} 页原文：\n{e['quote']}" for e in claim['evidence'])
        self.saved_quote.setPlainText('\n\n'.join(excerpts));self.update_evidence_controls()

    def locate_saved_claim(self):
        item=self.evidence_claims.currentItem()
        if self.io_worker is not None or not item:return
        claim=item.data(Qt.ItemDataRole.UserRole)
        if not claim['evidence']:return
        evidence=claim['evidence'][0]
        def locate():
            blocks=self.service.document_blocks(claim['documentId'],evidence['revisionId'])
            block=next((b for b in blocks if b['id']==evidence['blockId']),None)
            if not block:raise ValueError('保留的原文证据已不存在。')
            self.open_evidence(block)
        self.guard(locate)

    def update_evidence_controls(self):
        busy=self.io_worker is not None or self._closing
        has_document=bool(self.evidence_doc.currentData())
        block=self.evidence_blocks.currentItem()
        self.evidence_doc.setEnabled(not busy)
        item=self.evidence_claims.currentItem()
        self.saved_locate.setEnabled(bool(item and item.data(Qt.ItemDataRole.UserRole)['evidence']) and not busy)
        self.claim_text.setReadOnly(busy)
        self.evidence_blocks.setEnabled(not busy)
        self.evidence_text.setEnabled(not busy)
        self.claim_text.setEnabled(not busy)
        self.parse_button.setEnabled(has_document and not busy)
        self.locate_button.setEnabled(bool(block) and not busy)
        self.save_claim_button.setEnabled(bool(block) and bool(self.claim_text.toPlainText().strip()) and self.evidence_text.textCursor().hasSelection() and not busy)

    def parse_evidence(self):
        if self.io_worker is not None or self._closing:return
        identifier=self.evidence_doc.currentData()
        if not identifier:return
        self.run_io(lambda:self.service.parse_document(identifier),lambda result:self.load_evidence(),'正在提取本地 PDF 文本…')

    def save_evidence_claim(self):
        if self.io_worker is not None or self._closing:return
        item=self.evidence_blocks.currentItem();identifier=self.evidence_doc.currentData()
        if not item or identifier!=self._evidence_loaded_id:return
        block=item.data(Qt.ItemDataRole.UserRole)
        quote=self.evidence_text.textCursor().selectedText().replace('\u2029','\n').replace('\u2028','\n')
        text=self.claim_text.toPlainText().strip()
        if not quote or not text:return
        def saved(result):
            if self.claim_text.toPlainText().strip()==text:
                self.claim_text.clear()
            self.refresh_evidence_claims(identifier)
            self.evidence_status.setText('笔记与原文引用已保存在本机。引用关联不代表结论已验证。')
        self.run_io(lambda:self.service.save_claim(identifier,text,[{'blockId':block['id'],'quote':quote}]),saved,'正在保存本地证据笔记…')

    def locate_evidence(self):
        if self.io_worker is not None:return
        item=self.evidence_blocks.currentItem()
        if item:self.open_evidence(item.data(Qt.ItemDataRole.UserRole))
