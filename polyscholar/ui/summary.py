# SPDX-License-Identifier: AGPL-3.0-only
"""Native local evidence notes and explicit selected-text model summaries."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QLabel, QComboBox, QListWidget, QListWidgetItem,
                              QTextEdit, QSplitter, QWidget, QVBoxLayout, QHBoxLayout)

class SummaryPage:
    def summary(self):
        layout=self.page('证据摘要与笔记','选择原文生成带引用的模型摘要，或保存手写笔记。模型输出需自行核对。')
        self.evidence_doc=QComboBox();layout.addWidget(self.evidence_doc)
        actions=QHBoxLayout()
        self.parse_button=self.button('提取本地文本',self.parse_evidence,True)
        self.locate_button=self.button('在原文中定位',self.locate_evidence)
        actions.addWidget(self.parse_button);actions.addWidget(self.locate_button);actions.addStretch();layout.addLayout(actions)
        self.summary_api=QLabel();self.summary_api.setWordWrap(True);layout.addWidget(self.summary_api)
        self.summary_range=QTextEdit();self.summary_range.setReadOnly(True);self.summary_range.setMaximumHeight(110);self.summary_range.setPlaceholderText('勾选下方文本块后，这里显示将发送的全部原文。');layout.addWidget(self.summary_range)
        self.generate_summary_button=self.button('将选中原文发送至 API 并生成摘要',self.generate_evidence_summary,True);layout.addWidget(self.generate_summary_button)
        self.evidence_status=QLabel('选择本地文献后提取文本。');self.evidence_status.setWordWrap(True);layout.addWidget(self.evidence_status)
        split=QSplitter();self.evidence_blocks=QListWidget();split.addWidget(self.evidence_blocks)
        detail=QWidget();column=QVBoxLayout(detail);column.setContentsMargins(0,0,0,0)
        self.evidence_text=QTextEdit();self.evidence_text.setReadOnly(True);self.evidence_text.setPlaceholderText('选择文本块；选中需要引用的原文。');column.addWidget(self.evidence_text,1)
        self.claim_text=QTextEdit();self.claim_text.setPlaceholderText('输入自己的证据笔记（不会自动判断真实性）');self.claim_text.setMaximumHeight(120);column.addWidget(self.claim_text)
        self.save_claim_button=self.button('保存笔记与选中原文',self.save_evidence_claim);column.addWidget(self.save_claim_button)
        split.addWidget(detail);split.setStretchFactor(1,2);layout.addWidget(split,1)
        self.evidence_claims=QListWidget();self.evidence_claims.setMaximumHeight(160);layout.addWidget(QLabel('已保存摘要与笔记 · 关联状态'));layout.addWidget(self.evidence_claims)
        self.saved_evidence=QComboBox();self.saved_evidence.currentIndexChanged.connect(self.update_evidence_controls);layout.addWidget(self.saved_evidence)
        self.saved_quote=QTextEdit();self.saved_quote.setReadOnly(True);self.saved_quote.setMaximumHeight(100);layout.addWidget(self.saved_quote)
        self.saved_locate=self.button('查看笔记保留的原文',self.locate_saved_claim);layout.addWidget(self.saved_locate)
        self.evidence_claims.currentItemChanged.connect(self.show_saved_claim)
        self._evidence_loaded_id=None
        self.evidence_doc.currentIndexChanged.connect(self.load_evidence)
        self.evidence_blocks.currentItemChanged.connect(self.show_evidence_block)
        self.evidence_blocks.itemChanged.connect(self.update_summary_range)
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
            self.evidence_status.setText('请先导入本地 PDF。');self.update_summary_range();return
        def load():
            revision=self.service.current_document_ir(identifier)
            blocks=self.service.document_blocks(identifier)
            for block in blocks:
                item=QListWidgetItem(f"第 {block['pageNumber']} 页 · {block['text'][:100].replace(chr(10),' ')}")
                item.setData(Qt.ItemDataRole.UserRole,block);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Unchecked);self.evidence_blocks.addItem(item)
            if revision and revision.get('status',revision.get('state'))=='no_text':
                self.evidence_status.setText('此 PDF 没有可提取文本，可能为扫描件。本功能不执行 OCR，无法建立文本证据。')
            elif blocks:self.evidence_status.setText(f'已提取 {len(blocks)} 个文本块。选中原文并输入笔记后保存；数据保存在本机。')
            else:self.evidence_status.setText('尚未提取文本。点击“提取本地文本”。')
            self.refresh_evidence_claims(identifier)
        self.guard(load)
        if self.evidence_blocks.count():self.evidence_blocks.setCurrentRow(0)
        self.update_summary_range()

    def refresh_evidence_claims(self,identifier):
        self.evidence_claims.clear()
        for claim in self.service.list_claims(identifier):
            stale=claim.get('stale') or claim.get('status')=='stale'
            label='引用已失效，请核对' if stale else ('缺少原文证据' if claim.get('status')=='insufficient_evidence' else '原文已关联 · 未验证结论')
            provenance=claim.get('provenance',{})
            origin=f"模型摘要 · {provenance.get('model','')} · " if provenance.get('source')=='model' else '手写笔记 · '
            if provenance.get('source')=='model':
                categories={'question':'研究问题','method':'方法','data':'数据','result':'结果','limitation':'局限','reproducibility':'复现信息','other':'其他'}
                attribution={'author_report':'作者报告','model_inference':'模型推断'}
                origin+=categories.get(claim.get('category',provenance.get('category')),'')+' · '+attribution.get(claim.get('attribution',provenance.get('attribution')),'')+' · '
            item=QListWidgetItem(f"{origin}{label}\n{claim['text']}");item.setData(Qt.ItemDataRole.UserRole,claim)
            self.evidence_claims.addItem(item)

    def show_evidence_block(self,*args):
        item=self.evidence_blocks.currentItem();block=item.data(Qt.ItemDataRole.UserRole) if item else None
        self.evidence_text.setPlainText(block['text'] if block else '');self.update_evidence_controls()

    def show_saved_claim(self,*args):
        item=self.evidence_claims.currentItem();claim=item.data(Qt.ItemDataRole.UserRole) if item else None
        excerpts=[];self.saved_evidence.blockSignals(True);self.saved_evidence.clear()
        if claim:
            if claim.get('stale'):excerpts.append('旧解析版本的引用，未自动更新。')
            excerpts.extend(f"第 {e['pageNumber']} 页原文：\n{e['quote']}" for e in claim['evidence'])
        for evidence in claim['evidence'] if claim else []:
            self.saved_evidence.addItem(f"第 {evidence['pageNumber']} 页 · {evidence['quote'][:60].replace(chr(10),' ')}",evidence)
        self.saved_evidence.blockSignals(False)
        self.saved_quote.setPlainText('\n\n'.join(excerpts));self.update_evidence_controls()

    def locate_saved_claim(self):
        item=self.evidence_claims.currentItem()
        if self.io_worker is not None or not item:return
        claim=item.data(Qt.ItemDataRole.UserRole)
        evidence=self.saved_evidence.currentData()
        if not evidence:return
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
        self.saved_locate.setEnabled(bool(item and self.saved_evidence.currentData()) and not busy)
        self.saved_evidence.setEnabled(not busy)
        self.claim_text.setReadOnly(busy)
        self.evidence_blocks.setEnabled(not busy)
        self.evidence_text.setEnabled(not busy)
        self.claim_text.setEnabled(not busy)
        self.parse_button.setEnabled(has_document and not busy)
        self.locate_button.setEnabled(bool(block) and not busy)
        selected=self.selected_summary_blocks()
        configured=bool(self.service.get_settings().get('model','').strip())
        valid_range=0<len(selected)<=64 and sum(len(b['text'].encode('utf-8')) for b in selected)<=128*1024
        self.generate_summary_button.setEnabled(configured and valid_range and not busy)
        self.save_claim_button.setEnabled(bool(block) and bool(self.claim_text.toPlainText().strip()) and self.evidence_text.textCursor().hasSelection() and not busy)

    def selected_summary_blocks(self):
        return [self.evidence_blocks.item(index).data(Qt.ItemDataRole.UserRole)
                for index in range(self.evidence_blocks.count())
                if self.evidence_blocks.item(index).checkState()==Qt.CheckState.Checked]

    def update_summary_range(self,*args):
        settings=self.service.get_settings()
        blocks=self.selected_summary_blocks()
        pages=sorted({block['pageNumber'] for block in blocks})
        text='\n\n'.join(f"第 {block['pageNumber']} 页 · 文本块 {block['order']+1}\n{block['text']}" for block in blocks)
        self.summary_range.setPlainText(text)
        page_range='第 '+'、'.join(map(str,pages))+' 页' if pages else '未选择'
        self.summary_api.setText(f"API：{settings.get('endpoint','')}\n模型：{settings.get('model','') or '未配置，请到设置填写'} · 摘要语言：{settings.get('targetLanguage','zh')}\n发送范围：{page_range}，{len(blocks)} 个文本块；只发送勾选的原文。模型请求可能计费。\n最多 64 块 / 128 KiB 原文；输出上限 4096 tokens。单次请求失败不自动重试。")
        self.update_evidence_controls()

    def generate_evidence_summary(self):
        if self.io_worker is not None or self._closing:return
        identifier=self.evidence_doc.currentData();blocks=self.selected_summary_blocks()
        if not blocks or identifier!=self._evidence_loaded_id:return
        revision=blocks[0]['revisionId'];block_ids=[block['id'] for block in blocks]
        settings=self.service.get_settings();expected_settings={name:settings[name] for name in ('endpoint','model','targetLanguage')}
        def saved(result):
            self.refresh_evidence_claims(identifier)
            self.evidence_status.setText(f'已保存 {len(result)} 条模型摘要。请结合原文核对；引用关联不代表结论已验证。')
        self.run_io(lambda:self.service.summarize_document(identifier,block_ids,revision_id=revision,expected_settings=expected_settings),saved,'正在生成所选原文的模型摘要…')

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
