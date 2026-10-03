# SPDX-License-Identifier: AGPL-3.0-only
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QListWidget, QLineEdit, QFileDialog, QMessageBox, QFormLayout, QTextEdit, QPlainTextEdit, QSplitter, QInputDialog, QTreeWidget, QTreeWidgetItem, QCheckBox, QAbstractItemView, QDialog, QDialogButtonBox, QComboBox, QScrollArea, QFrame, QGridLayout, QHeaderView)
from .workers import safe_error
from .creators import CreatorsDialog
from .searches import SearchDialog
from .fulltext import FullTextDialog
from .arxiv import ArxivImportDialog
from .scores import ScoreDetailDialog
from copy import deepcopy
from ..metadata import APPLICABLE, legacy_creators

class ScoreTreeWidgetItem(QTreeWidgetItem):
    """Numeric-aware sorting: scored items rank by value, unscored always last."""
    def __lt__(self, other):
        column=self.treeWidget().sortColumn()
        a=self.data(column,Qt.ItemDataRole.UserRole)
        b=other.data(column,Qt.ItemDataRole.UserRole)
        a=-1.0 if a is None else a
        b=-1.0 if b is None else b
        if isinstance(a,(int,float)) and isinstance(b,(int,float)):return a<b
        return str(a)<str(b)

class LibraryPage:
    def library(self):
        l=self.page('文献库','本地整理文献、AI 评分与元数据。集合与标签管理将逐步对标 Zotero。',scroll=False)
        r=QHBoxLayout();r.setSpacing(8);self.search=QLineEdit();self.search.setPlaceholderText('搜索标题、作者、DOI、标签');self.search.textChanged.connect(self.filter_docs)
        r.addWidget(self.search,1);self.arxiv_button=self.button('arXiv 导入',self.open_arxiv,True);r.addWidget(self.arxiv_button);self.import_button=self.button('导入 PDF',self.import_pdf);r.addWidget(self.import_button);l.addLayout(r)
        self.advanced_query={'match':'all','conditions':[]};self._saved_query_changed=False
        tools=QHBoxLayout();tools.setSpacing(8);self.advanced_search_button=self.button('高级元数据检索',self.edit_search);tools.addWidget(self.advanced_search_button);self.saved_searches=QComboBox();self.saved_searches.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.saved_searches.setMinimumContentsLength(8);self.saved_searches.addItem('未选择保存搜索',None);self.saved_searches.currentIndexChanged.connect(self.select_saved_search);tools.addWidget(self.saved_searches,1)
        self.save_search_button=self.button('保存新搜索',self.save_new_search);tools.addWidget(self.save_search_button)
        self.update_search_button=self.button('更新保存搜索',self.update_saved_search);tools.addWidget(self.update_search_button)
        self.delete_search_button=self.button('删除搜索',self.delete_saved_search);tools.addWidget(self.delete_search_button);self.fulltext_button=self.button('本地全文检索',self.open_fulltext);tools.addWidget(self.fulltext_button);l.addLayout(tools)
        status=QHBoxLayout();status.setSpacing(12);self.search_scope=QLabel('');self.search_scope.setObjectName('muted');self.search_scope.setWordWrap(True);status.addWidget(self.search_scope,1);self.io_status=QLabel('');self.io_status.setObjectName('muted');status.addWidget(self.io_status);l.addLayout(status)
        self.update_search_controls()
        split=QSplitter();split.setHandleWidth(1)
        organize=QWidget();organize.setMinimumWidth(170);ol=QVBoxLayout(organize);ol.setContentsMargins(0,0,0,0);ol.setSpacing(6)
        self.collection_tree=QTreeWidget();self.collection_tree.setHeaderLabel('集合');self.collection_tree.currentItemChanged.connect(lambda *args:self.filter_docs());ol.addWidget(self.collection_tree,1)
        ol.addWidget(self.button('新建集合',self.new_collection))
        controls=QHBoxLayout();controls.setSpacing(6);self.collection_edit_button=self.button('编辑',self.edit_collection);self.collection_delete_button=self.button('删除',self.remove_collection);controls.addWidget(self.collection_edit_button);controls.addWidget(self.collection_delete_button);ol.addLayout(controls)
        self.include_children=QCheckBox('包含子集合');self.include_children.toggled.connect(self.filter_docs);ol.addWidget(self.include_children)
        ol.addWidget(QLabel('标签（可多选）'));self.tag_filter=QListWidget();self.tag_filter.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection);self.tag_filter.setMaximumHeight(140);self.tag_filter.itemSelectionChanged.connect(self.filter_docs);ol.addWidget(self.tag_filter)
        tag_actions=QHBoxLayout();tag_actions.setSpacing(6);tag_actions.addWidget(self.button('重命名',self.rename_selected_tag));tag_actions.addWidget(self.button('删除标签',self.remove_selected_tag));ol.addLayout(tag_actions);ol.addWidget(self.button('清除筛选',self.clear_filters))
        split.addWidget(organize)
        middle=QWidget();ml=QVBoxLayout(middle);ml.setContentsMargins(0,0,0,0);ml.setSpacing(4)
        self.list_count=QLabel('共 0 篇');self.list_count.setObjectName('muted');ml.addWidget(self.list_count)
        self.document_tree=QTreeWidget();self.document_tree.setObjectName('panel');self.document_tree.setMinimumWidth(220)
        self.document_tree.setHeaderLabels(['标题','论文分','置信度']);self.document_tree.setRootIsDecorated(False);self.document_tree.setSortingEnabled(True)
        self.document_tree.sortItems(1,Qt.SortOrder.DescendingOrder)
        self.document_tree.setAllColumnsShowFocus(True)
        self.document_tree.header().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for column in (1,2):self.document_tree.header().setSectionResizeMode(column,QHeaderView.ResizeMode.ResizeToContents)
        self.document_tree.currentItemChanged.connect(self.select_doc);ml.addWidget(self.document_tree,1)
        self.empty=QLabel('还没有文献，请先导入本地 PDF。');self.empty.setObjectName('muted');self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter);self.empty.setWordWrap(True);ml.addWidget(self.empty)
        split.addWidget(middle)
        inspector=QWidget();inspector.setMinimumWidth(320);il=QVBoxLayout(inspector);il.setContentsMargins(0,0,4,0);il.setSpacing(8)
        self.fields={};self._metadata_creators=[]
        head=QFormLayout();head.setVerticalSpacing(8);head.setHorizontalSpacing(10)
        self.item_type=QComboBox()
        for label,value in [('arXiv 预印本','arxiv-preprint'),('期刊论文','article-journal'),('会议论文','paper-conference'),('图书','book'),('学位论文','thesis')]:self.item_type.addItem(label,value)
        head.addRow('条目类型',self.item_type);self.item_type.currentIndexChanged.connect(self.update_metadata_fields)
        self.fields['title']=QLineEdit();head.addRow('标题',self.fields['title'])
        il.addLayout(head)
        il.addWidget(QLabel('作者 / 编者'));self.creator_list=QListWidget();self.creator_list.setMaximumHeight(96);il.addWidget(self.creator_list)
        il.addWidget(self.button('编辑作者与编者顺序',self.edit_creators))
        il.addWidget(QLabel('摘要'));self.abstract=QTextEdit();self.abstract.setPlaceholderText('arXiv 导入时自动填写');self.abstract.setFixedHeight(96);il.addWidget(self.abstract)
        self.abstract_button=self.button('展开摘要',self.toggle_abstract);il.addWidget(self.abstract_button)
        self.bib_grid=QGridLayout();self.bib_grid.setVerticalSpacing(4);self.bib_grid.setHorizontalSpacing(16);self.bib_grid.setColumnStretch(0,1);self.bib_grid.setColumnStretch(1,1)
        self.bib_labels={}
        self.bib_spec=[('publicationTitle','期刊 / 论文集',2),('date','日期',1),('volume','卷',1),('issue','期',1),('pages','页码',1),('publisher','出版者',1),('place','出版地',1),('edition','版次',1),('isbn','ISBN',1),('eventTitle','会议名称',2),('institution','授予机构',1),('thesisType','学位类型',1)]
        for key,label,span in self.bib_spec:
            editor=QLineEdit();self.fields[key]=editor
            label_widget=QLabel(label);self.bib_labels[key]=label_widget
        self.fields['date'].setPlaceholderText('YYYY-MM-DD')
        for key in ('publicationTitle','publisher','place','date','volume','issue','pages','isbn','edition','eventTitle','institution','thesisType'):self.fields[key].textChanged.connect(self.update_retained_info)
        il.addLayout(self.bib_grid)
        self.retained_fields_info=QLabel('切换类型保留已填写字段。');self.retained_fields_info.setObjectName('muted');self.retained_fields_info.setWordWrap(True);il.addWidget(self.retained_fields_info)
        self.update_metadata_fields()
        tail=QFormLayout();tail.setVerticalSpacing(8);tail.setHorizontalSpacing(10)
        # One scrollable view per score kind: the combined box forced long scrolling
        # to reach the confidence rationale; separate boxes keep each readable.
        score_box=QWidget();score_layout=QVBoxLayout(score_box);score_layout.setContentsMargins(0,0,0,0);score_layout.setSpacing(4)
        self.score_headers={};self.score_views={}
        for kind,name,height in (('paper','论文评分',96),('confidence','置信度',96),('summary','AI 提炼',56)):
            header=QLabel(name+' —');header.setObjectName('muted');sl=QVBoxLayout();sl.setContentsMargins(0,0,0,0);sl.setSpacing(0)
            sl.addWidget(header)
            view=QPlainTextEdit();view.setReadOnly(True);view.setFrameShape(QFrame.Shape.NoFrame);view.setMaximumHeight(height)
            view.setPlaceholderText('暂无;可由 agent 评分后写入。' if kind!='summary' else '暂无;由 agent 经 score set --kind summary 写入。')
            sl.addWidget(view);score_layout.addLayout(sl)
            self.score_headers[kind]=header;self.score_views[kind]=view
        score_layout.addWidget(self.button('查看完整评分明细',self.open_score_detail))
        tail.addRow('AI 评分',score_box)
        self.fields['doi']=QLineEdit();tail.addRow('DOI',self.fields['doi'])
        self.fields['tags']=QLineEdit();tail.addRow('标签',self.fields['tags'])
        self.membership_info=QLabel('');self.membership_info.setWordWrap(True);tail.addRow('所属集合',self.membership_info)
        il.addLayout(tail)
        member_row=QHBoxLayout();self.membership_add_button=self.button('加入集合',self.add_to_collection);self.membership_remove_button=self.button('移出当前集合',self.remove_from_collection);member_row.addWidget(self.membership_add_button);member_row.addWidget(self.membership_remove_button);member_row.addStretch();il.addLayout(member_row)
        il.addWidget(QLabel('PDF 附件'));self.attachment_list=QListWidget();self.attachment_list.setMaximumHeight(110);self.attachment_list.currentRowChanged.connect(self.update_attachment_controls);self.attachment_list.itemDoubleClicked.connect(self.open_attachment_externally);self.attachment_list.setToolTip('双击用系统默认 PDF 程序打开');il.addWidget(self.attachment_list)
        att_row=QHBoxLayout();att_row.setSpacing(6);self.attachment_role=QComboBox();self.attachment_role.addItem('补充材料','supplement');self.attachment_role.addItem('已有译文','translation');att_row.addWidget(self.attachment_role,1);self.attachment_add_button=self.button('添加',self.add_attachment);att_row.addWidget(self.attachment_add_button);self.attachment_read_button=self.button('阅读',self.read_attachment);att_row.addWidget(self.attachment_read_button);self.attachment_delete_button=self.button('删除',self.remove_attachment);att_row.addWidget(self.attachment_delete_button);il.addLayout(att_row)
        action_row=QHBoxLayout();action_row.setSpacing(6);save_button=self.button('保存条目',self.save_doc,True);action_row.addWidget(save_button,1);self.translation_button=self.button('打开译文',self.open_translation);action_row.addWidget(self.translation_button);self.read_button=self.button('阅读文献',self.open_original);action_row.addWidget(self.read_button);action_row.addWidget(self.button('删除条目',self.delete_doc));il.addLayout(action_row)
        inspector_scroll=QScrollArea();inspector_scroll.setWidgetResizable(True);inspector_scroll.setFrameShape(QFrame.Shape.NoFrame);inspector_scroll.setWidget(inspector);inspector_scroll.setMinimumWidth(340)
        split.addWidget(inspector_scroll);split.setSizes([220,640,380]);split.setStretchFactor(0,0);split.setStretchFactor(1,1);split.setStretchFactor(2,0);l.addWidget(split,1)

    def selected(self):
        item=self.document_tree.currentItem()
        return item.data(0,Qt.ItemDataRole.UserRole) if item else None

    @staticmethod
    def score_text(value):
        return '—' if value is None else f'{value:g}'

    def filter_docs(self):
        if not hasattr(self,'document_tree'):return
        self.collection_edit_button.setEnabled(self.current_collection() is not None);self.collection_delete_button.setEnabled(self.current_collection() is not None);self.include_children.setEnabled(self.current_collection() is not None)
        current=self.selected();tree=self.document_tree;tree.blockSignals(True);tree.clear()
        collection=self.current_collection()
        matches=self.service.search_documents(text=self.search.text(),collection_id=collection,
            unfiled=self.collection_view()=='__unfiled__',tags=[item.text() for item in self.tag_filter.selectedItems()],
            include_descendants=self.include_children.isChecked(),query=self.advanced_query if self.advanced_query['conditions'] else None)
        for d in matches:
            scores=d.get('scores') or {}
            paper,confidence=scores.get('paper'),scores.get('confidence')
            item=ScoreTreeWidgetItem([d['title'],self.score_text(paper),self.score_text(confidence)])
            item.setData(0,Qt.ItemDataRole.UserRole,d);item.setToolTip(0,d['title'])
            item.setData(1,Qt.ItemDataRole.UserRole,paper);item.setData(2,Qt.ItemDataRole.UserRole,confidence)
            tree.addTopLevelItem(item)
            if current and current['id']==d['id']:tree.setCurrentItem(item)
        tree.blockSignals(False);self.select_doc(None)
        if hasattr(self,'list_count'):self.list_count.setText(f'共 {tree.topLevelItemCount()} 篇')
        if hasattr(self,'empty'):
            self.empty.setVisible(tree.topLevelItemCount()==0);self.empty.setText('当前集合或筛选下没有文献。' if self.docs else '还没有文献，请先导入本地 PDF。')

    def select_doc(self,_):
        d=self.selected() or {}
        for k,e in self.fields.items():e.setText((', '.join(d.get(k,[])) if k=='tags' else str(d.get(k,'') or '')))
        self._metadata_creators=[dict(c) for c in d.get('creators',legacy_creators(d.get('authors','')))]
        self.refresh_creator_list()
        self.item_type.setCurrentIndex(max(0,self.item_type.findData(d.get('itemType','article-journal'))));self.update_metadata_fields()
        self.abstract.setPlainText(d.get('abstract',''))
        self.update_score_views(d)
        collections={c['id']:c['name'] for c in self.service.list_collections()}
        member_ids=self.service.document_collections(d['id']) if d else []
        self.membership_info.setText('、'.join(collections[i] for i in member_ids) or '未分类')
        self.membership_add_button.setEnabled(bool(d));self.membership_remove_button.setEnabled(bool(d) and self.current_collection() in member_ids)
        self.refresh_attachments()

    def update_score_views(self,document):
        """Fill each kind's header and rationale box from stored data only."""
        if not hasattr(self,'score_views'):return
        scores=(document or {}).get('scores') or {}
        full=self.service.document_scores(document['id']) if (document and document.get('id')) else {}
        names={'paper':'论文评分','confidence':'置信度','summary':'AI 提炼'}
        for kind,name in names.items():
            value=scores.get(kind)
            self.score_headers[kind].setText(name+' —' if value is None else f'{name} {value:g} / 100')
            entry=full.get(kind) or {}
            self.score_views[kind].setPlainText((entry.get('rationale') or '').strip())

    def open_score_detail(self):
        """Show the stored machine-readable detail verbatim; nothing is synthesised."""
        d=self.selected()
        if not d:return
        try:
            scores=self.service.document_scores(d['id'])
        except Exception as error:
            QMessageBox.warning(self,'评分明细',safe_error(error));return
        if not any(scores.values()):
            QMessageBox.information(self,'评分明细','该文献还没有 AI 评分。');return
        ScoreDetailDialog(d.get('title',''),scores,self).exec()

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

    def open_arxiv(self):
        if self.io_worker is not None or self._closing:return
        ArxivImportDialog(self).exec()

    def save_doc(self):
        d=self.selected()
        if not d:return
        data={k:e.text().strip() for k,e in self.fields.items()};data['tags']=[t.strip() for t in data['tags'].split(',') if t.strip()];data['abstract']=self.abstract.toPlainText()
        data['itemType']=self.item_type.currentData()
        # The creator dialog owns ordering; the authors line stays derived.
        data['creators']=[dict(c) for c in self._metadata_creators]
        data.pop('authors',None)
        # Leave invalid drafts visible so a validation error can be corrected.
        if self.guard(lambda:self.service.update_document(d['id'],data)) is not None:self.refresh()

    def update_metadata_fields(self,*args):
        """Reflow only the applicable fields into the grid so rows stay aligned.

        隐藏字段不再占据棋盘格位置：每类字段按声明顺序流动填入，整行字段跨双列。
        """
        visible=[(key,label,span) for key,label,span in self.bib_spec
                 if key in APPLICABLE.get(self.item_type.currentData(),set())]
        grid=self.bib_grid
        while grid.count():
            item=grid.takeAt(0)
            widget=item.widget()
            if widget is not None:widget.hide()
        row=column=0
        for key,label,span in visible:
            grid.addWidget(self.bib_labels[key],row,column,1,span);self.bib_labels[key].show()
            grid.addWidget(self.fields[key],row+1,column,1,span);self.fields[key].show()
            if span==2 or column==1:row+=2;column=0
            else:column=1
        self.update_retained_info()

    def update_retained_info(self,*args):
        visible={key for key,label,span in self.bib_spec
                 if key in APPLICABLE.get(self.item_type.currentData(),set())}
        retained=[label for key,label,span in self.bib_spec
                  if key not in visible and self.fields[key].text().strip()
                  for label in (label,)]
        if hasattr(self,'retained_fields_info'):self.retained_fields_info.setText('切换类型保留已填写字段。'+('当前保留：'+ '、'.join(retained) if retained else ''))

    def refresh_creator_list(self):
        self.creator_list.clear()
        for creator in self._metadata_creators:
            name=creator.get('literal') or ' '.join(filter(None,[creator.get('given'),creator.get('family')]))
            self.creator_list.addItem(('编者：' if creator.get('role')=='editor' else '')+name)
        if not self.creator_list.count():self.creator_list.addItem('（尚未填写，点击下方按钮编辑）')

    def toggle_abstract(self):
        expanded=self.abstract.height()>150
        self.abstract.setFixedHeight(96 if expanded else 300)
        self.abstract_button.setText('收起摘要' if not expanded else '展开摘要')

    def edit_creators(self):
        if not self.selected():return
        dialog=CreatorsDialog([dict(c) for c in self._metadata_creators],self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self._metadata_creators=dialog.creators();self.refresh_creator_list()

    def open_translation(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        d=self.selected()
        if not d or self.io_worker is not None or self._closing:return
        try:
            translations=self.service.document_translations(d['id'])
        except Exception:
            translations=[]
        if not translations:
            QMessageBox.information(self,'打开译文','该文献还没有中文译文；请先在翻译任务页创建翻译。')
            return
        path=translations[0]['path']
        if len(translations)>1:
            names=[f"任务 {t['jobId'][:8]} · {Path(t['path']).name}" for t in translations]
            name,ok=QInputDialog.getItem(self,'选择译文','有多个译文版本',names,0,False)
            if not ok:return
            path=translations[names.index(name)]['path']
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def delete_doc(self):
        d=self.selected()
        if not d or self.io_worker is not None or self._closing:return
        if QMessageBox.question(self,'删除条目','删除本地条目及所有 PDF 附件、翻译任务、摘要和笔记？原始导入文件不受影响。')!=QMessageBox.StandardButton.Yes:return
        identifier=d['id']
        def ready(_):
            if self.reader_document and (self.reader_document.get('parentDocumentId') or self.reader_document['id'])==identifier:self.clear_reader()
            self.refresh()
        self.run_io(lambda:self.service.delete_document(identifier),ready,'正在删除文献及附件…')

    def attachment_label(self,row):
        role={'original':'原文','supplement':'补充材料','translation':'已有译文'}.get(row['role'],row['role'])
        return f"{role} · {row.get('filename',row.get('label','PDF'))}"

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
        self.update_attachment_controls()

    def update_attachment_controls(self,*args):
        if not hasattr(self,'attachment_add_button'):return
        busy=self.io_worker is not None or self._closing
        parent=self.selected();row=self.selected_attachment()
        self.attachment_list.setEnabled(not busy);self.attachment_role.setEnabled(not busy)
        self.attachment_add_button.setEnabled(bool(parent) and not busy)
        self.attachment_read_button.setEnabled(bool(row) and not busy)
        self.attachment_delete_button.setEnabled(bool(row and row['role']!='original') and not busy)

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

    def open_attachment_externally(self,item):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        row=item.data(Qt.ItemDataRole.UserRole)
        if not row or self.io_worker is not None or self._closing:return
        path=self.guard(lambda:self.service.attachment_file_path(row['documentId']))
        if path is not None:QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def remove_attachment(self):
        parent=self.selected();row=self.selected_attachment()
        if not parent or not row or row['role']=='original' or self.io_worker is not None or self._closing:return
        message=f"删除附件“{row['filename']}”及该附件的翻译任务、摘要、证据笔记？原始导入文件和主文献保留。"
        if QMessageBox.question(self,'删除 PDF 附件',message)!=QMessageBox.StandardButton.Yes:return
        identifier=parent['id'];child=row['documentId']
        def ready(_):
            if self.reader_document and self.reader_document['id']==child:self.clear_reader()
            self.refresh()
        self.run_io(lambda:self.service.delete_attachment(identifier,child),ready,'正在删除附件…')
