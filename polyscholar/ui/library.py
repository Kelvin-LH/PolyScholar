# SPDX-License-Identifier: AGPL-3.0-only
from PySide6.QtCore import Qt, Signal, QItemSelectionModel
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QListWidget, QLineEdit, QFileDialog, QMessageBox, QFormLayout, QTextEdit, QSplitter, QInputDialog, QTreeWidget, QTreeWidgetItem, QCheckBox, QAbstractItemView, QDialog, QDialogButtonBox, QComboBox, QScrollArea, QHeaderView, QTabWidget, QPlainTextEdit, QFrame)
from .sections import DisclosureSection, ScoreCard
from .verification import VerificationDialog
from .workers import safe_error
from .arxiv import ArxivImportDialog
from .scores import ScoreDetailDialog, KIND_NAMES, score_text
from .creators import CreatorsDialog
from .searches import SearchDialog
from .fulltext import FullTextDialog
from .trash import TrashDialog, TrashMoveOperation
from .duplicates import DuplicatesDialog
from .bibliographic import BibliographicDialog
from .citation_import import CitationImportDialog
from .zotero_migration import ZoteroMigrationDialog
from .import_export import ImportExportDialog
from copy import deepcopy
from ..metadata import APPLICABLE, ITEM_TYPE_LABELS, legacy_creators, creator_display

class ScoreTreeWidgetItem(QTreeWidgetItem):
    """Column items keep the existing library-selection API stable.

    多列视图兼容现有合并、选择与原生检查入口，避免复制文献业务逻辑。
    """
    def data(self, *args):
        return super().data(0, args[0]) if len(args) == 1 else super().data(*args)

    def text(self, column=0):
        return super().text(column)

    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn()
        if column in (1, 2):
            left = self.data(column, Qt.ItemDataRole.UserRole)
            right = other.data(column, Qt.ItemDataRole.UserRole)
            if left is None or right is None:
                if left is None and right is None:
                    return self.text().casefold() < other.text().casefold()
                descending = tree.header().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
                return descending if left is None else not descending
            return left < right
        return self.text(column).casefold() < other.text(column).casefold()


class DocumentTree(QTreeWidget):
    """Native columns plus the main library's established row-selection contract."""
    currentRowChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.currentItemChanged.connect(lambda *_: self.currentRowChanged.emit(self.currentRow()))

    def count(self):
        return self.topLevelItemCount()

    def item(self, row):
        return self.topLevelItem(row)

    def currentRow(self):
        return self.indexOfTopLevelItem(self.currentItem())

    def setCurrentRow(self, row):
        self.setCurrentItem(self.topLevelItem(row))


class LibraryPage:
    def library(self):
        l=self.page('文献库','整理本地文献、附件、笔记与 Agent 评阅记录。',scroll=False)
        r=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('搜索标题、作者、DOI、标签');self.search.textChanged.connect(self.filter_docs);self.search.setAccessibleName('搜索文献')
        r.addWidget(self.search);self.new_bibliographic_button=self.button('新建书目',self.new_bibliographic);r.addWidget(self.new_bibliographic_button);self.import_button=self.button('导入 PDF',self.import_pdf,True);r.addWidget(self.import_button);self.arxiv_button=self.button('arXiv 导入',self.open_arxiv);r.addWidget(self.arxiv_button);l.addLayout(r)
        self.advanced_query={'match':'all','conditions':[]};self._saved_query_changed=False
        advanced=QHBoxLayout();self.advanced_search_button=self.button('高级元数据检索',self.edit_search);advanced.addWidget(self.advanced_search_button);self.saved_searches=QComboBox();self.saved_searches.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.saved_searches.setMinimumContentsLength(8);self.saved_searches.addItem('未选择保存搜索',None);self.saved_searches.currentIndexChanged.connect(self.select_saved_search);advanced.addWidget(self.saved_searches,1);l.addLayout(advanced)
        search_actions=QHBoxLayout();self.save_search_button=self.button('保存新搜索',self.save_new_search);search_actions.addWidget(self.save_search_button)
        self.update_search_button=self.button('更新保存搜索',self.update_saved_search);search_actions.addWidget(self.update_search_button)
        self.delete_search_button=self.button('删除搜索',self.delete_saved_search);search_actions.addWidget(self.delete_search_button);self.fulltext_button=self.button('本地全文检索',self.open_fulltext);search_actions.addWidget(self.fulltext_button);self.citations_button=self.button('引用导出',self.open_citations);search_actions.addWidget(self.citations_button);l.addLayout(search_actions)
        self.search_scope=QLabel('');self.search_scope.setWordWrap(True);l.addWidget(self.search_scope);self.update_search_controls()
        self.io_status=QLabel('');self.io_status.setWordWrap(True);l.addWidget(self.io_status)
        split=QSplitter();split.setChildrenCollapsible(False);split.setHandleWidth(5)
        organize=QWidget();organize.setMinimumWidth(160);ol=QVBoxLayout(organize);ol.setContentsMargins(0,0,12,0)
        self.collection_tree=QTreeWidget();self.collection_tree.setMinimumHeight(120);self.collection_tree.setHeaderLabel('集合');self.collection_tree.currentItemChanged.connect(lambda *args:self.filter_docs());ol.addWidget(self.collection_tree,1)
        ol.addWidget(self.button('新建集合',self.new_collection))
        controls=QHBoxLayout();self.collection_edit_button=self.button('编辑',self.edit_collection);self.collection_delete_button=self.button('删除',self.remove_collection);controls.addWidget(self.collection_edit_button);controls.addWidget(self.collection_delete_button);ol.addLayout(controls)
        self.include_children=QCheckBox('包含子集合');self.include_children.toggled.connect(self.filter_docs);ol.addWidget(self.include_children)
        ol.addWidget(QLabel('标签（可多选）'));self.tag_filter=QListWidget();self.tag_filter.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection);self.tag_filter.setMaximumHeight(160);self.tag_filter.itemSelectionChanged.connect(self.filter_docs);ol.addWidget(self.tag_filter)
        tag_actions = QHBoxLayout()
        tag_actions.addWidget(self.button('重命名',self.rename_selected_tag))
        tag_actions.addWidget(self.button('删除标签',self.remove_selected_tag))
        ol.addLayout(tag_actions)
        ol.addWidget(self.button('清除筛选',self.clear_filters))
        self.trash_button = self.button('回收站',self.open_trash)
        ol.addWidget(self.trash_button)
        self.duplicates_button = self.button('重复候选 / 合并',self.open_duplicates)
        ol.addWidget(self.duplicates_button)
        self.import_export_button = self.button('导入与导出', self.open_import_export)
        ol.addWidget(self.import_export_button)
        organize_scroll = QScrollArea()
        organize_scroll.setWidgetResizable(True)
        organize_scroll.setFrameShape(QFrame.Shape.NoFrame)
        organize_scroll.setWidget(organize)
        organize_scroll.setMinimumWidth(170)
        split.addWidget(organize_scroll)
        self.document_tree = DocumentTree()
        self.document_list = self.document_tree
        self.document_tree.setAccessibleName('文献列表，可按标题或 Agent 评阅分数排序')
        self.document_tree.setHeaderLabels(['标题', '论文分', '置信度'])
        self.document_tree.header().setStretchLastSection(False)
        self.document_tree.setRootIsDecorated(False)
        self.document_tree.setAllColumnsShowFocus(True)
        self.document_tree.setMinimumWidth(230)
        self.document_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.document_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.document_tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.document_tree.setColumnWidth(column,54)
            self.document_tree.headerItem().setToolTip(column, '已保存的 Agent 报告评分，不代表科学结论真伪概率。')
        self.document_tree.setSortingEnabled(True)
        self.document_tree.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.document_tree.currentRowChanged.connect(self.select_doc)
        self.document_tree.itemActivated.connect(lambda *_: self.open_original())
        split.addWidget(self.document_tree)
        inspector = QWidget()
        inspector.setMinimumWidth(280)
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        self.inspector_title = QLabel('选择文献后查看 AI 结论。')
        self.inspector_title.setTextFormat(Qt.TextFormat.PlainText)
        self.inspector_title.setWordWrap(True)
        inspector_layout.addWidget(self.inspector_title)
        self._score_document_id = None
        self.score_cards = {}
        self.score_headers = {}
        self.score_views = {}
        for kind, name in (('paper', '论文分'), ('confidence', '文本内置信度'), ('summary', 'AI 提炼')):
            card = ScoreCard(name, '暂无结果；通过 MCP agent 分析后写入。')
            self.score_cards[kind] = card
            self.score_headers[kind] = card.header
            self.score_views[kind] = card.full_text
            inspector_layout.addWidget(card)
        self.score_status = QLabel('分数为 Agent 评阅记录，供阅读核查。')
        self.score_status.setWordWrap(True)
        inspector_layout.addWidget(self.score_status)
        self.score_detail_button = self.button('评阅明细', self.open_score_detail)
        inspector_layout.addWidget(self.score_detail_button)
        self.verification_button = self.button('外部核验（独立报告）', self.open_verification)
        inspector_layout.addWidget(self.verification_button)
        self.read_button = self.button('阅读主要 PDF', self.open_original)
        inspector_layout.addWidget(self.read_button)
        self.metadata_section = DisclosureSection('书目、作者、摘要与笔记')
        inspector_layout.addWidget(self.metadata_section)
        metadata = QWidget()
        self.metadata_section.content_layout.addWidget(metadata)
        f = QFormLayout(metadata)
        self.metadata_form = f
        self.fields = {}
        self._metadata_creators = []
        self._authors_loaded = ''
        f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.item_type=QComboBox()
        for value,label in ITEM_TYPE_LABELS.items():self.item_type.addItem(label,value)
        f.addRow('条目类型',self.item_type);self.item_type.currentIndexChanged.connect(self.update_metadata_fields)
        for k,name in [('title','标题'),('authors','作者姓名'),('doi','DOI'),('url','网址'),('year','年份'),('tags','标签')]:
            e=QLineEdit();self.fields[k]=e;f.addRow(name,e)
        self.fields['authors'].setPlaceholderText('按分号分隔；修改将替换作者与编者')
        self.creators_button=self.button('编辑作者与编者顺序',self.edit_creators);f.addRow(self.creators_button)
        self.creator_info=QLabel('');self.creator_info.setWordWrap(True);f.addRow(self.creator_info)
        for key,label in [('publicationTitle','期刊 / 论文集'),('publisher','出版者'),('place','出版地'),('date','日期'),('volume','卷'),('issue','期'),('pages','页码'),('isbn','ISBN'),('edition','版次'),('eventTitle','会议名称'),('institution','授予机构'),('thesisType','学位类型')]:
            editor=QLineEdit();self.fields[key]=editor;f.addRow(label,editor)
        self.fields['date'].setPlaceholderText('YYYY / YYYY-MM / YYYY-MM-DD')
        for key in ('publicationTitle','publisher','place','date','volume','issue','pages','isbn','edition','eventTitle','institution','thesisType'):self.fields[key].textChanged.connect(self.update_metadata_fields)
        self.retained_fields_info=QLabel('切换类型保留已填写字段。');self.retained_fields_info.setWordWrap(True);f.addRow(self.retained_fields_info)
        self.update_metadata_fields()
        self.abstract = QTextEdit()
        self.abstract.setAccessibleName('文献摘要')
        self.abstract.setPlaceholderText('导入时保存的摘要，可在本机编辑。')
        self.abstract.setMaximumHeight(130)
        f.addRow('摘要', self.abstract)
        self.notes=QTextEdit();self.notes.setPlaceholderText('本地笔记');self.notes.setMaximumHeight(150);f.addRow('笔记',self.notes)
        f.addRow(self.button('保存条目',self.save_doc,True))
        self.resources_section=DisclosureSection('所属集合与 PDF 附件')
        inspector_layout.addWidget(self.resources_section)
        resources=QWidget();self.resources_section.content_layout.addWidget(resources)
        f=QFormLayout(resources);f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.membership_info=QLabel('');self.membership_info.setWordWrap(True);f.addRow('所属集合',self.membership_info);self.membership_add_button=self.button('加入集合',self.add_to_collection);self.membership_remove_button=self.button('移出当前集合',self.remove_from_collection);f.addRow(self.membership_add_button);f.addRow(self.membership_remove_button);
        self.attachment_list=QListWidget();self.attachment_list.setMaximumHeight(130);self.attachment_list.currentRowChanged.connect(self.update_attachment_controls);f.addRow('PDF 附件',self.attachment_list)
        self.attachment_empty=QLabel('尚无 PDF：添加后可阅读、解析与翻译 PDF。带 arXiv 链接的条目可使用 HTML 翻译。');self.attachment_empty.setWordWrap(True);self.attachment_empty.setMinimumHeight(100);f.addRow(self.attachment_empty)
        self.attachment_role=QComboBox();self.attachment_role.addItem('补充材料','supplement');self.attachment_role.addItem('已有译文','translation');f.addRow('导入附件类型',self.attachment_role)
        self.attachment_add_button=self.button('添加 PDF 附件',self.add_attachment);self.attachment_read_button=self.button('阅读选中附件',self.read_attachment);self.attachment_delete_button=self.button('附件移至回收站',self.remove_attachment)
        self.primary_pdf_button=self.button('设为主要 PDF',self.set_primary_pdf);f.addRow(self.primary_pdf_button)
        f.addRow(self.attachment_add_button);f.addRow(self.attachment_read_button);f.addRow(self.attachment_delete_button)
        inspector_layout.addWidget(self.button('移至回收站',self.delete_doc));inspector_layout.addStretch()
        inspector_scroll=QScrollArea();self.inspector_scroll=inspector_scroll;inspector_scroll.setWidgetResizable(True);inspector_scroll.setWidget(inspector);inspector_scroll.setMinimumWidth(300)
        split.addWidget(inspector_scroll);split.setSizes([180,460,310]);split.setStretchFactor(1,1);l.addWidget(split,1)
        self.empty=QLabel('还没有文献，可新建书目或导入本地 PDF。');self.empty.setObjectName('muted');l.addWidget(self.empty)

    def selected(self):
        item=self.document_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    @staticmethod
    def score_text(value):
        return score_text(value)

    def filter_docs(self):
        if not hasattr(self, 'document_list'):
            return
        collection = self.current_collection()
        self.collection_edit_button.setEnabled(collection is not None)
        self.collection_delete_button.setEnabled(collection is not None)
        self.include_children.setEnabled(collection is not None)
        current = self.selected()
        selected_ids={item.data(0,Qt.ItemDataRole.UserRole)['id'] for item in self.document_tree.selectedItems()}
        matches = self.service.search_documents(
            text=self.search.text(), collection_id=collection,
            unfiled=self.collection_view() == '__unfiled__',
            tags=[item.text() for item in self.tag_filter.selectedItems()],
            include_descendants=self.include_children.isChecked(),
            query=self.advanced_query if self.advanced_query['conditions'] else None,
        )
        tree = self.document_tree
        tree.blockSignals(True)
        tree.setSortingEnabled(False)
        tree.clear()
        for document in matches:
            scores = document.get('scores') or {}
            paper, confidence = scores.get('paper'), scores.get('confidence')
            item = ScoreTreeWidgetItem([document['title'], score_text(paper), score_text(confidence)])
            item.setData(0, Qt.ItemDataRole.UserRole, document)
            item.setToolTip(0, document['title'])
            item.setData(1, Qt.ItemDataRole.UserRole, paper)
            item.setData(2, Qt.ItemDataRole.UserRole, confidence)
            tree.addTopLevelItem(item)
            item.setSelected(document['id'] in selected_ids)
            if current and current['id'] == document['id']:
                tree.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        tree.setSortingEnabled(True)
        tree.blockSignals(False)
        self.select_doc(tree.currentRow())
        if hasattr(self, 'empty'):
            self.empty.setVisible(tree.count() == 0)
            self.empty.setText('当前集合或筛选下没有文献。' if self.docs
                               else '还没有文献，可新建书目、导入本地 PDF 或添加 arXiv 文献。')

    def select_doc(self,_):
        d=self.selected() or {}
        for k,e in self.fields.items():
            e.setText(', '.join(d.get(k,[])) if k=='tags' else str(d.get(k,'') or ''))
            e.setCursorPosition(0)
        self._authors_loaded=self.fields['authors'].text();self._metadata_creators=[dict(c) for c in d.get('creators',legacy_creators(d.get('authors','')))]
        self.item_type.setCurrentIndex(max(0,self.item_type.findData(d.get('itemType','article-journal'))));self.update_metadata_fields();self.update_creator_info()
        self.creators_button.setEnabled(bool(d));self.notes.setPlainText(d.get('notes',''))
        self.abstract.setPlainText(d.get('abstract',''));self.update_score_views(d)
        collections={c['id']:c['name'] for c in self.service.list_collections()}
        member_ids=self.service.document_collections(d['id']) if d else []
        self.membership_info.setText('、'.join(collections[i] for i in member_ids) or '未分类')
        self.membership_add_button.setEnabled(bool(d));self.membership_remove_button.setEnabled(bool(d) and self.current_collection() in member_ids)
        self.refresh_attachments()

    def update_score_views(self, document):
        """Preserve same-paper disclosure; reset when identity changes.

        同篇刷新保留展开状态，切换文献收起旧内容；折叠不重建编辑草稿。
        """
        identifier = (document or {}).get('id')
        reset = identifier != self._score_document_id
        self._score_document_id = identifier
        self.inspector_title.setText((document or {}).get('title') or '选择文献后查看 AI 结论。')
        scores = self.service.document_scores(identifier) if identifier else {}
        for kind, card in self.score_cards.items():
            card.set_entry(scores.get(kind), reset=reset)
        self.score_detail_button.setEnabled(any(scores.values()) and self.io_worker is None)
        self.verification_button.setEnabled(bool(document) and self.io_worker is None)
        self.metadata_section.setEnabled(bool(document))
        self.resources_section.setEnabled(bool(document))
        if reset:
            self.metadata_section.set_expanded(False)
            self.resources_section.set_expanded(False)

    def open_score_detail(self):
        document = self.selected()
        if not document or self.io_worker is not None or self._closing:
            return
        try:
            scores = self.service.document_scores(document['id'])
        except Exception as error:
            self.score_status.setText(safe_error(error))
            return
        self.score_detail_dialog = ScoreDetailDialog(
            self, document['id'], document.get('title', ''), scores)
        self.score_detail_dialog.show()

    def open_arxiv(self):
        if self.io_worker is not None or self._closing:
            return
        if hasattr(self, 'arxiv_dialog') and not self.arxiv_dialog._closed:
            self.arxiv_dialog.show()
            self.arxiv_dialog.raise_()
            self.arxiv_dialog.activateWindow()
            return
        self.arxiv_dialog = ArxivImportDialog(self)
        self.arxiv_dialog.show()

    def collection_view(self):
        item=self.collection_tree.currentItem()
        return item.data(0,Qt.ItemDataRole.UserRole) if item else '__all__'

    def current_collection(self):
        value=self.collection_view()
        return None if value in ('__all__','__unfiled__') else value

    def refresh_organization(self):
        previous=self.collection_view();selected_tags={item.text() for item in self.tag_filter.selectedItems()}
        self.collection_tree.blockSignals(True);self.collection_tree.clear()
        items={}
        for label,identifier in [('全部文献','__all__'),('未分类','__unfiled__')]:
            item=QTreeWidgetItem([label]);item.setData(0,Qt.ItemDataRole.UserRole,identifier);self.collection_tree.addTopLevelItem(item);items[identifier]=item
        collections=self.service.list_collections()
        for c in collections:
            item=QTreeWidgetItem([f"{c['name']} ({c['count']})"]);item.setData(0,Qt.ItemDataRole.UserRole,c['id']);item.setToolTip(0,c['name']);items[c['id']]=item
        for c in collections:
            item=items[c['id']]
            if c['parentId']:items[c['parentId']].addChild(item)
            else:self.collection_tree.addTopLevelItem(item)
        self.collection_tree.expandAll();self.collection_tree.setCurrentItem(items.get(previous,items['__all__']));self.collection_tree.blockSignals(False)
        self.tag_filter.blockSignals(True);self.tag_filter.clear()
        for tag in self.service.list_tags():
            self.tag_filter.addItem(tag);item=self.tag_filter.item(self.tag_filter.count()-1);item.setSelected(tag in selected_tags)
        self.tag_filter.blockSignals(False)
        self.refresh_saved_searches()

    def collection_picker(self,title,allow_root=False,exclude=None,current=None):
        collections=self.service.list_collections();by_id={c['id']:c for c in collections}
        dialog=QDialog(self);dialog.setWindowTitle(title);layout=QVBoxLayout(dialog);combo=QComboBox()
        if allow_root:combo.addItem('顶层',None)
        excluded={exclude} if exclude else set()
        while True:
            expanded=excluded|{c['id'] for c in collections if c['parentId'] in excluded}
            if expanded==excluded:break
            excluded=expanded
        for c in collections:
            if c['id'] in excluded:continue
            names=[c['name']];parent=c['parentId']
            while parent:
                names.insert(0,by_id[parent]['name']);parent=by_id[parent]['parentId']
            combo.addItem(' / '.join(names),c['id'])
        if current is not None or allow_root:
            index=combo.findData(current)
            if index>=0:combo.setCurrentIndex(index)
        if combo.count()==0:
            QMessageBox.information(self,'没有集合','请先新建集合。');return False,None
        layout.addWidget(combo);buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        return dialog.exec()==QDialog.DialogCode.Accepted,combo.currentData()

    def new_collection(self):
        parent=self.current_collection();name,ok=QInputDialog.getText(self,'新建子集合' if parent else '新建集合','集合名称')
        if ok:self.guard(lambda:self.service.create_collection(name,parent));self.refresh()

    def edit_collection(self):
        identifier=self.current_collection()
        if not identifier:return
        collection=next(c for c in self.service.list_collections() if c['id']==identifier)
        name,ok=QInputDialog.getText(self,'编辑集合','集合名称',text=collection['name'])
        if not ok:return
        accepted,parent=self.collection_picker('选择集合所在层级',allow_root=True,exclude=identifier,current=collection['parentId'])
        if accepted:self.guard(lambda:self.service.update_collection(identifier,name,parent));self.refresh()

    def remove_collection(self):
        identifier=self.current_collection()
        if identifier and QMessageBox.question(self,'删除集合','删除当前集合及其子集合？文献、附件和笔记会保留。')==QMessageBox.StandardButton.Yes:
            self.guard(lambda:self.service.delete_collection(identifier));self.refresh()

    def add_to_collection(self):
        document=self.selected()
        if not document:return
        ok,identifier=self.collection_picker('加入集合')
        if ok:self.guard(lambda:self.service.set_membership(document['id'],identifier));self.refresh()

    def remove_from_collection(self):
        document=self.selected();identifier=self.current_collection()
        if document and identifier:self.guard(lambda:self.service.set_membership(document['id'],identifier,False));self.refresh()

    def rename_selected_tag(self):
        selected=self.tag_filter.selectedItems()
        if len(selected)!=1:
            QMessageBox.information(self,'重命名标签','请只选择一个标签。');return
        old=selected[0].text();new,ok=QInputDialog.getText(self,'重命名标签','新标签名称',text=old)
        if ok:self.guard(lambda:self.service.rename_tag(old,new));self.refresh()

    def remove_selected_tag(self):
        selected=self.tag_filter.selectedItems()
        if len(selected)!=1:
            QMessageBox.information(self,'删除标签','请只选择一个标签。');return
        tag=selected[0].text()
        if QMessageBox.question(self,'删除标签',f'从所有文献中移除“{tag}”标签？文献与笔记会保留。')==QMessageBox.StandardButton.Yes:
            self.guard(lambda:self.service.rename_tag(tag,None));self.refresh()

    def clear_filters(self):
        self.advanced_query={'match':'all','conditions':[]};self._saved_query_changed=False;self.saved_searches.setCurrentIndex(0)
        self.search.clear();self.tag_filter.clearSelection();self.include_children.setChecked(False);self.collection_tree.setCurrentItem(self.collection_tree.topLevelItem(0));self.update_search_controls();self.filter_docs()

    def open_fulltext(self):
        if self.io_worker is not None or self._closing:return
        if hasattr(self,'fulltext_dialog') and not self.fulltext_dialog._closed:
            self.fulltext_dialog.show();self.fulltext_dialog.raise_();self.fulltext_dialog.activateWindow();return
        criteria={'metadata_text':self.search.text(),'collection_id':self.current_collection(),'unfiled':self.collection_view()=='__unfiled__','tags':[item.text() for item in self.tag_filter.selectedItems()],'include_descendants':self.include_children.isChecked(),'query':deepcopy(self.advanced_query) if self.advanced_query['conditions'] else None}
        scope='打开时的范围：'+self.collection_tree.currentItem().text(0)+'；快速搜索 '+(self.search.text() or '不限')+'；标签 '+('、'.join(criteria['tags']) or '不限')+f"；高级条件 {len(self.advanced_query['conditions'])} 条，满足{'全部' if self.advanced_query['match']=='all' else '任一'}"+('；包含子集合' if criteria['include_descendants'] else '')
        self.fulltext_dialog=FullTextDialog(self,criteria,scope);self.fulltext_dialog.show();self.fulltext_dialog.search()

    def update_search_controls(self):
        count=len(self.advanced_query['conditions']);selected=self.saved_searches.currentData() is not None
        self.save_search_button.setEnabled(bool(count));self.update_search_button.setEnabled(selected and bool(count));self.delete_search_button.setEnabled(selected)
        mode='全部' if self.advanced_query['match']=='all' else '任一'
        self.search_scope.setText((f'高级条件：{count} 条，满足{mode}。' if count else '未应用高级条件。')+('修改尚未保存。' if self._saved_query_changed else '')+' 与快速搜索、当前集合及标签筛选相交；保存搜索只记录高级规则。')

    def refresh_saved_searches(self):
        identifier=self.saved_searches.currentData();self.saved_searches.blockSignals(True);self.saved_searches.clear();self.saved_searches.addItem('未选择保存搜索',None)
        for row in self.service.list_saved_searches():self.saved_searches.addItem(row['name'],row['id'])
        index=self.saved_searches.findData(identifier);self.saved_searches.setCurrentIndex(max(0,index));self.saved_searches.blockSignals(False);self.update_search_controls()

    def select_saved_search(self,*args):
        identifier=self.saved_searches.currentData();row=next((row for row in self.service.list_saved_searches() if row['id']==identifier),None)
        self.advanced_query=deepcopy(row['query']) if row else {'match':'all','conditions':[]};self._saved_query_changed=False;self.update_search_controls();self.filter_docs()

    def edit_search(self):
        dialog=SearchDialog(deepcopy(self.advanced_query),self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        query=dialog.query()
        if self.guard(lambda:self.service.search_documents(query=query if query['conditions'] else None)) is None:return
        self.advanced_query=query;self._saved_query_changed=self.saved_searches.currentData() is not None;self.update_search_controls();self.filter_docs()

    def save_new_search(self):
        name,ok=QInputDialog.getText(self,'保存搜索','搜索名称')
        if not ok:return
        row=self.guard(lambda:self.service.save_saved_search(name,deepcopy(self.advanced_query)))
        if row is not None:
            self.refresh_saved_searches();self.saved_searches.setCurrentIndex(self.saved_searches.findData(row['id']));self._saved_query_changed=False;self.update_search_controls()

    def update_saved_search(self):
        identifier=self.saved_searches.currentData()
        if identifier is None:return
        name,ok=QInputDialog.getText(self,'更新保存搜索','搜索名称',text=self.saved_searches.currentText())
        if not ok:return
        row=self.guard(lambda:self.service.save_saved_search(name,deepcopy(self.advanced_query),search_id=identifier))
        if row is not None:self._saved_query_changed=False;self.refresh_saved_searches();self.filter_docs()

    def delete_saved_search(self):
        identifier=self.saved_searches.currentData()
        if identifier is None:return
        if QMessageBox.question(self,'删除保存搜索','删除该搜索规则？文献与附件会保留。')!=QMessageBox.StandardButton.Yes:return
        def remove():self.service.delete_saved_search(identifier);return True
        if self.guard(remove):self.refresh_saved_searches();self.select_saved_search()


    def import_pdf(self):
        if self.io_worker is not None:return
        paths,_=QFileDialog.getOpenFileNames(self,'导入本地文献','','PDF (*.pdf)')
        if not paths:return
        collection=self.current_collection()
        def work():
            errors=[]
            for path in paths:
                try:
                    document=self.service.import_pdf(path)
                    if collection:self.service.set_membership(document['id'],collection)
                except Exception as exc:errors.append(safe_error(exc))
            return errors
        def ready(errors):
            self.refresh()
            if errors:QMessageBox.warning(self,'部分文献未导入','\n'.join(dict.fromkeys(errors)))
        self.run_io(work,ready,'正在导入 PDF…')

    def save_doc(self):
        d=self.selected()
        if not d:return
        data={k:e.text().strip() for k,e in self.fields.items()};data['tags']=[t.strip() for t in data['tags'].split(',') if t.strip()];data['notes']=self.notes.toPlainText();data['abstract']=self.abstract.toPlainText()
        data['itemType']=self.item_type.currentData()
        if data['authors']==self._authors_loaded:
            data.pop('authors');data['creators']=[dict(c) for c in self._metadata_creators]
        # Leave invalid drafts visible so a validation error can be corrected.
        if self.guard(lambda:self.service.update_document(d['id'],data)) is not None:self.refresh()

    def update_metadata_fields(self,*args):
        visible=APPLICABLE.get(self.item_type.currentData(),set())
        retained=[]
        for key in ('publicationTitle','publisher','place','date','volume','issue','pages','isbn','edition','eventTitle','institution','thesisType'):
            if key not in self.fields:continue
            editor=self.fields[key];label=self.metadata_form.labelForField(editor);editor.setVisible(key in visible)
            if label:label.setVisible(key in visible)
            if key not in visible and editor.text().strip():retained.append(label.text() if label else key)
        if hasattr(self,'retained_fields_info'):self.retained_fields_info.setText('切换类型保留已填写字段。'+('当前保留：'+ '、'.join(retained) if retained else ''))

    def update_creator_info(self):
        labels=[]
        for creator in self._metadata_creators:
            name=creator.get('literal') or ' '.join(filter(None,[creator.get('given'),creator.get('family')]))
            labels.append(('编者：' if creator.get('role')=='editor' else '')+name)
        self.creator_info.setText('；'.join(labels) or '尚未填写作者或编者。')

    def edit_creators(self):
        if not self.selected():return
        creators=self._metadata_creators
        if self.fields['authors'].text().strip()!=self._authors_loaded:
            # A legacy line is one literal name, never split or guess personal names.
            text=self.fields['authors'].text().strip()
            creators=legacy_creators(text)
        dialog=CreatorsDialog(creators,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self._metadata_creators=dialog.creators();self._authors_loaded='; '.join(creator_display(c) for c in self._metadata_creators if c['role']=='author');self.fields['authors'].setText(self._authors_loaded);self.update_creator_info()

    def open_citation_import(self):
        if self.io_worker is not None or self._closing:
            return
        self.citation_import_dialog = CitationImportDialog(self,self.current_collection())
        self.citation_import_dialog.show()

    def open_import_export(self):
        if self.io_worker is not None or self._closing:
            return
        self.import_export_dialog = ImportExportDialog(self)
        self.import_export_dialog.show()

    def open_zotero_migration(self):
        if self.io_worker is not None or self._closing:
            return
        self.zotero_migration_dialog = ZoteroMigrationDialog(self)
        self.zotero_migration_dialog.show()

    def new_bibliographic(self):
        if self.io_worker is not None or self._closing:return
        self.bibliographic_dialog=BibliographicDialog(self,self.current_collection())
        self.bibliographic_dialog.show()

    def set_primary_pdf(self):
        parent=self.selected();row=self.selected_attachment()
        if not parent or not row or self.io_worker is not None or self._closing:return
        self.run_io(lambda:self.service.set_primary_pdf(parent['id'],row['documentId']),
                    lambda _:self.refresh(),'正在设置主要 PDF…')

    def open_duplicates(self):
        if self.io_worker is not None or self._closing:
            return
        if hasattr(self,'duplicates_dialog') and not self.duplicates_dialog._closed:
            self.duplicates_dialog.show()
            self.duplicates_dialog.raise_()
            self.duplicates_dialog.activateWindow()
            self.duplicates_dialog.refresh()
            return
        self.duplicates_dialog=DuplicatesDialog(self)
        self.duplicates_dialog.show()
        self.duplicates_dialog.refresh()

    def open_trash(self):
        if self.io_worker is not None or self._closing:
            return
        if hasattr(self,'trash_dialog') and not self.trash_dialog._closed:
            self.trash_dialog.show()
            self.trash_dialog.raise_()
            self.trash_dialog.activateWindow()
            self.trash_dialog.refresh()
            return
        self.trash_dialog=TrashDialog(self)
        self.trash_dialog.show()
        self.trash_dialog.refresh()

    def move_to_trash(self,identifier):
        # 确认和写入共享窗口 IO；预览不是删除成功的证据。
        # Confirmation and mutation share window IO; a preview never proves deletion.
        self._trash_operation=TrashMoveOperation(self,identifier)
        self._trash_operation.start()

    def delete_doc(self):
        document=self.selected()
        if document and self.io_worker is None and not self._closing:
            self.move_to_trash(document['id'])

    def attachment_label(self,row):
        role={'original':'原文','supplement':'补充材料','translation':'已有译文'}.get(row['role'],row['role'])
        return f"{role} · {row.get('filename',row.get('label','PDF'))}"+('（主要 PDF）' if row.get('isPrimary') else '')

    def pdf_choices(self):
        choices=[]
        for parent in self.docs:
            for row in self.service.list_attachments(parent['id']):
                choices.append((f"{parent['title']} · {self.attachment_label(row)}",row['documentId']))
        return choices

    def selected_attachment(self):
        item=self.attachment_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_attachments(self):
        if not hasattr(self,'attachment_list'):return
        previous=self.selected_attachment();parent=self.selected();self.attachment_list.clear()
        if parent:
            for row in self.service.list_attachments(parent['id']):
                self.attachment_list.addItem(self.attachment_label(row));item=self.attachment_list.item(self.attachment_list.count()-1);item.setData(Qt.ItemDataRole.UserRole,row);item.setToolTip(row['filename'])
                if previous and previous['documentId']==row['documentId']:self.attachment_list.setCurrentItem(item)
            if self.attachment_list.currentRow()<0:self.attachment_list.setCurrentRow(0)
        self.attachment_empty.setVisible(bool(parent) and self.attachment_list.count()==0)
        self.update_attachment_controls()

    def update_read_controls(self):
        if not hasattr(self, 'read_button'):
            return
        parent = self.selected()
        if not parent or self.io_worker is not None or self._closing:
            self.read_button.setEnabled(False)
            return
        # A fileless record may still have an actual completed HTML artifact.
        # 无 PDF 书目也可能有已完成的 HTML 产物；只查询当前选中条目。
        primary = self.service.primary_pdf_id(parent['id'])
        has_html = False
        self.read_button.setToolTip('')
        if primary is None:
            try:
                has_html = any(translation['format'] == 'html'
                               for translation in self.service.document_translations(parent['id']))
            except Exception as error:
                self.read_button.setToolTip(safe_error(error))
        self.read_button.setText('阅读HTML译文' if primary is None and has_html else '阅读主要 PDF')
        self.read_button.setEnabled(primary is not None or has_html)

    def update_attachment_controls(self,*args):
        if not hasattr(self,'attachment_add_button'):return
        busy=self.io_worker is not None or self._closing
        parent=self.selected();row=self.selected_attachment()
        self.attachment_list.setEnabled(not busy);self.attachment_role.setEnabled(not busy)
        self.attachment_add_button.setEnabled(bool(parent) and not busy)
        self.attachment_read_button.setEnabled(bool(row) and not busy)
        self.attachment_delete_button.setEnabled(bool(row and row['role']!='original') and not busy)
        self.primary_pdf_button.setEnabled(bool(row and not row.get('isPrimary')) and not busy)
        self.new_bibliographic_button.setEnabled(not busy)
        self.import_export_button.setEnabled(not busy)
        self.update_read_controls()
        self.arxiv_button.setEnabled(not busy)
        self.score_detail_button.setEnabled(bool(parent) and any(self.service.document_scores(parent['id']).values()) and not busy)
        self.verification_button.setEnabled(bool(parent) and not busy)

    def add_attachment(self):
        parent=self.selected()
        if not parent or self.io_worker is not None or self._closing:return
        path,_=QFileDialog.getOpenFileName(self,'添加本地 PDF 附件','','PDF (*.pdf)')
        if not path:return
        role=self.attachment_role.currentData();identifier=parent['id']
        self.run_io(lambda:self.service.import_attachment(identifier,path,role),lambda row:self.refresh(),'正在导入附件…')

    def read_attachment(self):
        row=self.selected_attachment()
        if row and self.io_worker is None and not self._closing:self.open_document(row['documentId'])

    def remove_attachment(self):
        row=self.selected_attachment()
        if row and row['role']!='original' and self.io_worker is None and not self._closing:
            self.move_to_trash(row['documentId'])

    def open_verification(self):
        document = self.selected()
        if not document or self.io_worker is not None or self._closing:
            return
        document = self.guard(lambda: self.service.verification_document(document['id']))
        if not document:
            return
        for dialog in self.findChildren(VerificationDialog):
            if dialog.document_id == document['id']:
                dialog._closed = False
                dialog.timer.start()
                dialog._poll_shared_io()
                dialog.refresh()
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()
                return
        dialog = VerificationDialog(self, document)
        dialog.show()
