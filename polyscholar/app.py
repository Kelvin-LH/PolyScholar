# SPDX-License-Identifier: AGPL-3.0-only
"""Native personal desktop UI. No browser, webview or HTTP server."""
from pathlib import Path
import sys
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
    QVBoxLayout, QLabel, QPushButton, QListWidget, QStackedWidget, QLineEdit,
    QComboBox, QFileDialog, QMessageBox, QFormLayout, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QSplitter, QScrollArea, QFrame, QInputDialog)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView

STYLE = """
QWidget { background:#ffffff; color:#1c2532; font-size:15px; }
QWidget#sidebar { background:#f1f4f5; }
QLabel#brand { color:#23614f; font-size:25px; font-weight:600; }
QLabel#heading { font-size:32px; font-weight:600; }
QLabel#muted { color:#718096; }
QPushButton { min-height:24px; padding:10px 18px; border:1px solid #d8dfe5; border-radius:5px; }
QPushButton:hover { background:#edf4f1; }
QPushButton#primary { background:#23614f; color:white; border:0; }
QPushButton:disabled { color:#99a4af; background:#f4f5f6; }
QLineEdit,QComboBox { min-height:24px; padding:9px; border:1px solid #d8dfe5; border-radius:4px; }
QTextEdit { padding:9px; border:1px solid #d8dfe5; border-radius:4px; }
QListWidget { border:0; background:transparent; outline:0; }
QListWidget::item { padding:16px; margin:3px 0; }
QListWidget::item:selected { color:white; background:#23614f; border-radius:5px; }
QTableWidget { border:0; gridline-color:#edf0f2; }
QHeaderView::section { background:#f2f4f6; padding:12px; border:0; }
"""

class FetchModels(QThread):
    ready = Signal(list)
    failed = Signal(str)
    def __init__(self, service, endpoint, key):
        super().__init__(); self.service, self.endpoint, self.key = service, endpoint, key
    def run(self):
        try: self.ready.emit(self.service.list_models(self.endpoint, self.key or None))
        except Exception: self.failed.emit('无法获取模型列表，请检查地址、密钥或手动填写模型名称。')
        finally: self.key = ''

class Window(QMainWindow):
    def __init__(self, service):
        super().__init__(); self.service=service; self.docs=[]; self.jobs=[]; self.model_worker=None
        self.setWindowTitle('PolyScholar · 研译'); self.resize(1440,960); self.setMinimumSize(1024,700)
        root=QWidget(); row=QHBoxLayout(root); row.setContentsMargins(0,0,0,0); row.setSpacing(0)
        side=QWidget(); side.setObjectName('sidebar'); side.setFixedWidth(240); sl=QVBoxLayout(side);sl.setContentsMargins(20,30,20,25)
        brand=QLabel('PolyScholar 研译');brand.setObjectName('brand');sl.addWidget(brand);sl.addSpacing(24)
        self.nav=QListWidget();self.nav.addItems(['文献库','双语阅读','翻译任务','证据摘要','引用导出','设置']);sl.addWidget(self.nav)
        self.stack=QStackedWidget(); row.addWidget(side); row.addWidget(self.stack,1);self.setCentralWidget(root)
        self.library();self.reader();self.tasks();self.summary();self.citations();self.settings()
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex);self.nav.setCurrentRow(0)
        self.refresh();self.timer=QTimer(self);self.timer.timeout.connect(self.refresh_jobs);self.timer.start(2000)
    def page(self,title,subtitle):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(38,34,38,30);l.setSpacing(20)
        h=QLabel(title);h.setObjectName('heading');l.addWidget(h)
        sub=QLabel(subtitle);sub.setObjectName('muted');sub.setWordWrap(True);l.addWidget(sub);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame);scroll.setWidget(w);self.stack.addWidget(scroll);return l
    def button(self,text,callback,primary=False):
        b=QPushButton(text);b.clicked.connect(callback)
        if primary:b.setObjectName('primary')
        return b
    def guard(self,fn):
        try:return fn()
        except Exception as exc: QMessageBox.warning(self,'操作未完成',str(exc));return None
    def library(self):
        l=self.page('文献库','本地整理文献、元数据与笔记。集合与标签管理将逐步对标 Zotero。')
        r=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('搜索标题、作者、DOI、标签');self.search.textChanged.connect(self.filter_docs)
        r.addWidget(self.search);r.addWidget(self.button('导入 PDF',self.import_pdf,True));l.addLayout(r)
        split=QSplitter();self.document_list=QListWidget();self.document_list.currentRowChanged.connect(self.select_doc);split.addWidget(self.document_list)
        inspector=QWidget();f=QFormLayout(inspector);self.fields={}
        for k,name in [('title','标题'),('authors','作者'),('doi','DOI'),('year','年份'),('tags','标签')]:
            e=QLineEdit();self.fields[k]=e;f.addRow(name,e)
        self.notes=QTextEdit();self.notes.setPlaceholderText('本地笔记');f.addRow('笔记',self.notes)
        f.addRow(self.button('保存条目',self.save_doc,True));f.addRow(self.button('阅读文献',self.open_original));f.addRow(self.button('删除条目',self.delete_doc))
        split.addWidget(inspector);split.setSizes([740,320]);l.addWidget(split,1)
        self.empty=QLabel('还没有文献，请先导入本地 PDF。');self.empty.setObjectName('muted');l.addWidget(self.empty)
    def selected(self):
        item=self.document_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None
    def filter_docs(self):
        needle=self.search.text().lower();current=self.selected();self.document_list.clear()
        for d in self.docs:
            if needle and needle not in ' '.join(str(d.get(k,'')) for k in ['title','authors','doi','tags']).lower():continue
            self.document_list.addItem(d['title']);item=self.document_list.item(self.document_list.count()-1);item.setData(Qt.ItemDataRole.UserRole,d)
            if current and current['id']==d['id']:self.document_list.setCurrentItem(item)
    def select_doc(self,_):
        d=self.selected() or {}
        for k,e in self.fields.items():e.setText((', '.join(d.get(k,[])) if k=='tags' else str(d.get(k,'') or '')))
        self.notes.setPlainText(d.get('notes',''))
    def refresh(self):
        self.docs=self.service.list_documents();self.filter_docs();self.empty.setVisible(not self.docs)
        self.task_doc.clear()
        for d in self.docs:self.task_doc.addItem(d['title'],d['id'])
        self.refresh_jobs();self.refresh_citation()
    def import_pdf(self):
        paths,_=QFileDialog.getOpenFileNames(self,'导入本地文献','','PDF (*.pdf)')
        for p in paths:self.guard(lambda p=p:self.service.import_pdf(p))
        self.refresh()
    def save_doc(self):
        d=self.selected()
        if not d:return
        data={k:e.text().strip() for k,e in self.fields.items()};data['tags']=[t.strip() for t in data['tags'].split(',') if t.strip()];data['notes']=self.notes.toPlainText()
        self.guard(lambda:self.service.update_document(d['id'],data));self.refresh()
    def delete_doc(self):
        d=self.selected()
        if d and QMessageBox.question(self,'删除条目','删除本地条目及关联数据？原始导入文件不受影响。')==QMessageBox.StandardButton.Yes:
            self.guard(lambda:self.service.delete_document(d['id']));self.refresh()
    def reader(self):
        l=self.page('双语阅读','原文与翻译结果并排阅读；段落对齐和图中文字注释仍在研发。')
        self.pdf_title=QLabel('请在文献库中选择文献并点击“阅读文献”。');l.addWidget(self.pdf_title)
        split=QSplitter();self.pdf_docs=[];self.pdf_views=[]
        for _ in range(2):
            pdf=QPdfDocument(self);view=QPdfView();view.setDocument(pdf);view.setPageMode(QPdfView.PageMode.MultiPage);view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            self.pdf_docs.append(pdf);self.pdf_views.append(view);split.addWidget(view)
        l.addWidget(split,1)
    def open_original(self):
        d=self.selected()
        if not d:return
        # QtPdf requires a QIODevice or path; owned buffer keeps source local and alive.
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        data=self.guard(lambda:self.service.read_pdf(d['id']))
        if data is None:return
        for pdf in self.pdf_docs:pdf.close()
        self.buffers=[]
        def load(index,raw):
            buf=QBuffer(self);buf.setData(QByteArray(raw));buf.open(QIODevice.OpenModeFlag.ReadOnly);self.buffers.append(buf);self.pdf_docs[index].load(buf)
        load(0,data)
        completed=[j for j in self.jobs if j.get('documentId')==d['id'] and j.get('state')=='completed' and j.get('artifacts')]
        if completed:
            raw=self.guard(lambda:self.service.read_artifact_pdf(completed[0]['id'],0))
            if raw:load(1,raw)
        self.pdf_title.setText(d['title']);self.nav.setCurrentRow(1)
    def tasks(self):
        l=self.page('翻译任务','选择文献，开始翻译。所选内容将发送至配置的模型 API。')
        r=QHBoxLayout();self.task_doc=QComboBox();r.addWidget(self.task_doc,1);self.pages=QLineEdit();self.pages.setPlaceholderText('页范围，留空全部');r.addWidget(self.pages)
        r.addWidget(self.button('创建翻译任务',self.start_job,True));l.addLayout(r)
        l.addWidget(QLabel('默认允许模型 API 请求及必要模型/字体下载。远程请求可能计费。'))
        self.job_table=QTableWidget(0,4);self.job_table.setHorizontalHeaderLabels(['文献','引擎','状态','操作']);self.job_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);l.addWidget(self.job_table,1)
    def start_job(self):
        doc_id=self.task_doc.currentData()
        if doc_id:self.guard(lambda:self.service.start_translation(doc_id,self.pages.text().strip()));self.refresh_jobs()
    def refresh_jobs(self):
        self.jobs=self.service.list_jobs();self.job_table.setRowCount(len(self.jobs))
        names={d['id']:d['title'] for d in self.docs};states={'queued':'等待中','running':'翻译中','completed':'已完成','failed':'失败'}
        for i,j in enumerate(self.jobs):
            for c,value in enumerate([names.get(j.get('documentId'),'文献'),j.get('engine',''),states.get(j.get('state'),j.get('state',''))]):self.job_table.setItem(i,c,QTableWidgetItem(value))
            if j.get('state')=='completed' and j.get('artifacts'):
                self.job_table.setCellWidget(i,3,self.button('导出译文',lambda checked=False,j=j:self.export_translation(j)))
            elif j.get('state')=='failed':self.job_table.setItem(i,3,QTableWidgetItem(j.get('errorCode') or '请检查模型设置'))
    def export_translation(self,j):
        index=0
        if len(j['artifacts'])>1:
            name,ok=QInputDialog.getItem(self,'选择导出文件','翻译结果',j['artifacts'],0,False)
            if not ok:return
            index=j['artifacts'].index(name)
        p,_=QFileDialog.getSaveFileName(self,'导出翻译结果',j['artifacts'][index],'PDF (*.pdf)')
        if p:self.guard(lambda:self.service.export_translation(j['id'],index,p))
    def summary(self):
        l=self.page('证据摘要','摘要应关联原文页码和段落；此功能尚未实现。')
        l.addStretch();label=QLabel('证据定位与模型摘要正在研发\n暂不提供自动生成结果');label.setAlignment(Qt.AlignmentFlag.AlignCenter);l.addWidget(label);l.addStretch()
    def citations(self):
        l=self.page('引用导出','导出文献元数据；完整 CSL 样式排版仍在研发。')
        self.citation_format=QComboBox();self.citation_format.addItems(['CSL-JSON','BibTeX','RIS']);self.citation_format.currentTextChanged.connect(self.refresh_citation);l.addWidget(self.citation_format)
        self.citation_preview=QTextEdit();self.citation_preview.setReadOnly(True);l.addWidget(self.citation_preview,1);l.addWidget(self.button('导出元数据',self.export_citations,True))
    def refresh_citation(self):
        import json
        self.citation_preview.setPlainText(json.dumps([{'title':d['title'],'DOI':d.get('doi'),'author':d.get('authors')} for d in self.docs],ensure_ascii=False,indent=2))
    def export_citations(self):
        fmt={'CSL-JSON':'csl-json','BibTeX':'bibtex','RIS':'ris'}[self.citation_format.currentText()];ext={'csl-json':'json','bibtex':'bib','ris':'ris'}[fmt]
        p,_=QFileDialog.getSaveFileName(self,'导出元数据','references.'+ext)
        if p:self.guard(lambda:self.service.export_metadata([d['id'] for d in self.docs],fmt,p))
    def settings(self):
        l=self.page('设置','运行环境随安装包提供；文献与笔记保存在本机。');l.parentWidget().setMinimumHeight(900)
        form=QFormLayout();s=self.service.get_settings();self.endpoint=QLineEdit(s.get('endpoint',''));form.addRow('模型 API 地址',self.endpoint)
        self.model=QComboBox();self.model.setEditable(True);self.model.addItem(s.get('model',''));row=QHBoxLayout();row.addWidget(self.model,1);self.fetch_button=self.button('获取可用模型',self.fetch_models);row.addWidget(self.fetch_button);form.addRow('模型名称',row)
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setPlaceholderText('仅本次会话保存，退出后清除');form.addRow('API 密钥',self.key)
        self.engine=QComboBox();self.engine.addItem('BabelDOC','babeldoc');self.engine.addItem('PDFMathTranslate','pdfmathtranslate');self.engine.setCurrentIndex(max(0,self.engine.findData(s.get('engine'))));form.addRow('翻译引擎',self.engine)
        self.runtime=QLabel(self.service.discover_engine(self.engine.currentData())['message']);self.runtime.setWordWrap(True);form.addRow('运行环境',self.runtime)
        self.cache=QLineEdit(s.get('cachePath',''));self.cache.setPlaceholderText('使用系统默认缓存目录');r=QHBoxLayout();r.addWidget(self.cache,1);r.addWidget(self.button('选择文件夹',self.choose_cache));form.addRow('缓存目录',r)
        self.source=QLineEdit(s.get('sourceLanguage','en'));self.target=QLineEdit(s.get('targetLanguage','zh'));form.addRow('源语言',self.source);form.addRow('目标语言',self.target);l.addLayout(form)
        self.model_status=QLabel('可从服务接口获取模型列表，也可手动输入模型名称。');self.model_status.setObjectName('muted');l.addWidget(self.model_status)
        l.addWidget(self.button('保存设置',self.save_settings,True));l.addWidget(self.button('清除会话密钥',lambda:self.service.set_session_key('')))
        note=QLabel('允许模型 API、DOI 元数据查询及模型/字体下载。\n翻译时所选内容会发送至配置的 API；其他私人数据在本机保存。\nAGPL-3.0 · 允许商业使用，适用源码公开与通知义务。');note.setWordWrap(True);l.addWidget(note);l.addStretch()
    def choose_cache(self):
        p=QFileDialog.getExistingDirectory(self,'选择缓存目录')
        if p:self.cache.setText(p)
    def save_settings(self):
        s=self.service.get_settings();s.update(endpoint=self.endpoint.text().strip(),model=self.model.currentText().strip(),engine=self.engine.currentData(),cachePath=self.cache.text().strip(),sourceLanguage=self.source.text().strip(),targetLanguage=self.target.text().strip())
        def save():
            self.service.save_settings(s)
            if self.key.text():self.service.set_session_key(self.key.text());self.key.clear()
            status=self.service.discover_engine(s['engine']);self.runtime.setText(status['message'])
        self.guard(save)
    def fetch_models(self):
        if self.model_worker and self.model_worker.isRunning():return
        self.fetch_button.setEnabled(False);self.model_status.setText('正在获取可用模型…')
        self.model_worker=FetchModels(self.service,self.endpoint.text().strip(),self.key.text())
        self.model_worker.ready.connect(self.models_ready);self.model_worker.failed.connect(self.models_failed);self.model_worker.finished.connect(lambda:self.fetch_button.setEnabled(True));self.model_worker.start()
    def models_ready(self,models):
        previous=self.model.currentText();self.model.clear();self.model.addItems(models)
        if previous in models:self.model.setCurrentText(previous)
        self.model_status.setText(f'已获取 {len(models)} 个模型。' if models else '接口未返回模型，可手动填写。')
    def models_failed(self,message):self.model_status.setText(message)
    def closeEvent(self,event:QCloseEvent):
        if self.model_worker and self.model_worker.isRunning():
            QMessageBox.information(self,'请求进行中','模型列表请求正在结束，请稍后关闭。');event.ignore();return
        self.timer.stop();self.service.close();event.accept()

def main():
    from .service import LocalService
    app=QApplication(sys.argv);app.setStyleSheet(STYLE)
    if getattr(sys,'frozen',False):
        base=Path(sys.executable).parents[1]/'Resources'/'resources' if sys.platform=='darwin' else Path(sys._MEIPASS)/'resources'
    else:base=Path(__file__).resolve().parents[1]
    if '--smoke-test' in sys.argv:
        import tempfile
        with tempfile.TemporaryDirectory(prefix='polyscholar-smoke-') as tmp:
            service=LocalService(data_dir=tmp,resources_dir=base)
            w=Window(service);w.show()
            def finish():
                print('PolyScholar native GUI smoke passed',flush=True)
                w.close();app.quit()
            QTimer.singleShot(250,finish)
            return app.exec()
    service=LocalService(resources_dir=base)
    w=Window(service);w.show();sys.exit(app.exec())
