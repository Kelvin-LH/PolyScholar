# SPDX-License-Identifier: AGPL-3.0-only
from PySide6.QtWidgets import QHBoxLayout, QComboBox, QLineEdit, QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QInputDialog, QFileDialog
from .workers import valid_progress, valid_usage

class TasksPage:
    def tasks(self):
        l=self.page('翻译任务','选择文献，开始翻译。所选内容将发送至配置的模型 API。')
        r=QHBoxLayout();self.task_doc=QComboBox();r.addWidget(self.task_doc,1);self.pages=QLineEdit();self.pages.setPlaceholderText('页范围，留空全部');r.addWidget(self.pages)
        r.addWidget(self.button('创建翻译任务',self.start_job,True));l.addLayout(r)
        self.send_boundary=QLabel();self.send_boundary.setWordWrap(True);l.addWidget(self.send_boundary)
        self.pages.textChanged.connect(self.refresh_boundary);self.refresh_boundary()
        self.job_table=QTableWidget(0,6);self.job_table.setHorizontalHeaderLabels(['文献','引擎','状态','进度','用量','操作']);self.job_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);l.addWidget(self.job_table,1)

    def start_job(self):
        doc_id=self.task_doc.currentData()
        if doc_id:self.guard(lambda:self.service.start_translation(doc_id,self.pages.text().strip()));self.refresh_jobs()

    def refresh_jobs(self):
        self.jobs=self.service.list_jobs();self.job_table.clearContents();self.job_table.setRowCount(len(self.jobs))
        names={d['id']:d['title'] for d in self.docs};states={'queued':'等待中','running':'翻译中','completed':'已完成','failed':'失败'}
        for i,j in enumerate(self.jobs):
            for c,value in enumerate([names.get(j.get('documentId'),'文献'),j.get('engine',''),states.get(j.get('state'),j.get('state',''))]):self.job_table.setItem(i,c,QTableWidgetItem(value))
            self.job_table.setItem(i,3,QTableWidgetItem(valid_progress(j.get('progress'))))
            self.job_table.setItem(i,4,QTableWidgetItem(valid_usage(j.get('usage'))))
            if j.get('state')=='completed' and j.get('artifacts'):
                self.job_table.setCellWidget(i,5,self.button('导出译文',lambda checked=False,j=j:self.export_translation(j)))
            elif j.get('state')=='failed':self.job_table.setItem(i,5,QTableWidgetItem(j.get('errorCode') or '请检查模型设置'))
        self.refresh_reader_choices()

    def refresh_boundary(self,*args):
        settings=self.service.get_settings();pages=self.pages.text().strip() or '全部页'
        self.send_boundary.setText(f"实际模型 API：{settings.get('endpoint','')}\n请求翻译范围：{pages}。上游引擎可能解析整篇 PDF；选页不保证其他页不会发送。\n允许必要模型/字体下载；远程请求可能计费。")

    def export_translation(self,j):
        index=0
        if len(j['artifacts'])>1:
            name,ok=QInputDialog.getItem(self,'选择导出文件','翻译结果',j['artifacts'],0,False)
            if not ok:return
            index=j['artifacts'].index(name)
        p,_=QFileDialog.getSaveFileName(self,'导出翻译结果',j['artifacts'][index],'PDF (*.pdf)')
        if p:self.guard(lambda:self.service.export_translation(j['id'],index,p))

