# SPDX-License-Identifier: AGPL-3.0-only
from datetime import datetime, timezone
from PySide6.QtWidgets import QHBoxLayout, QComboBox, QLineEdit, QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QInputDialog, QFileDialog, QWidget, QMessageBox
from .workers import valid_progress, valid_usage

class TasksPage:
    def tasks(self):
        l=self.page('翻译任务','选择文献，开始翻译。所选内容将发送至配置的模型 API。')
        r=QHBoxLayout();self.task_doc=QComboBox();r.addWidget(self.task_doc,1)
        r.addWidget(self.button('创建翻译任务',self.start_job,True));l.addLayout(r)
        self.send_boundary=QLabel();self.send_boundary.setWordWrap(True);l.addWidget(self.send_boundary)
        self.send_boundary=QLabel();self.send_boundary.setWordWrap(True);l.addWidget(self.send_boundary)
        self.refresh_boundary()
        self.job_table=QTableWidget(0,6);self.job_table.setHorizontalHeaderLabels(['文献','引擎','状态','进度 / 耗时','用量','操作'])
        header=self.job_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # The document column absorbs all spare width; action buttons size to content.
        header.setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for column in (1,2,3,4,5):header.setSectionResizeMode(column,QHeaderView.ResizeMode.ResizeToContents)
        l.addWidget(self.job_table,1)

    def refresh_task_items(self):
        previous=self.task_doc.currentData();self.task_doc.blockSignals(True);self.task_doc.clear()
        for label,identifier in self.pdf_choices():self.task_doc.addItem(label,identifier)
        index=self.task_doc.findData(previous)
        if index>=0:self.task_doc.setCurrentIndex(index)
        self.task_doc.blockSignals(False)

    def start_job(self):
        doc_id=self.task_doc.currentData()
        if doc_id:self.guard(lambda:self.service.start_translation(doc_id));self.refresh_jobs()

    @staticmethod
    def elapsed_label(job):
        """Running engines expose no percent; elapsed time is the honest signal."""
        try:
            started=datetime.fromisoformat(job.get('createdAt',''))
            seconds=max(0,int((datetime.now(timezone.utc)-started).total_seconds()))
            return f'已运行 {seconds//60}:{seconds%60:02d}'
        except (TypeError, ValueError):
            return '处理中…'

    def refresh_jobs(self):
        self.jobs=self.service.list_jobs();self.job_table.clearContents();self.job_table.setRowCount(len(self.jobs))
        names={identifier:label for label,identifier in self.pdf_choices()};states={'queued':'等待中','running':'翻译中','completed':'已完成','failed':'失败'}
        for i,j in enumerate(self.jobs):
            for c,value in enumerate([names.get(j.get('documentId'),'文献'),j.get('engine',''),states.get(j.get('state'),j.get('state',''))]):self.job_table.setItem(i,c,QTableWidgetItem(value))
            if j.get('state')=='running':
                item=QTableWidgetItem(j.get('progress') and valid_progress(j.get('progress')) or self.elapsed_label(j))
                item.setToolTip('引擎未提供百分比进度；此处显示任务已运行时间。')
                self.job_table.setItem(i,3,item)
            else:
                self.job_table.setItem(i,3,QTableWidgetItem(valid_progress(j.get('progress'))))
            self.job_table.setItem(i,4,QTableWidgetItem(valid_usage(j.get('usage'))))
            actions=self._job_actions(j)
            if actions is not None:self.job_table.setCellWidget(i,5,actions)
            elif j.get('state')=='failed':self.job_table.setItem(i,5,QTableWidgetItem(j.get('errorCode') or '请检查模型设置'))
        self.refresh_reader_choices()
        # Cell widgets are not measured by ResizeToContents: pin the action column.
        self.job_table.setColumnWidth(5, 250)

    def _job_actions(self,j):
        state=j.get('state')
        row=QHBoxLayout();row.setContentsMargins(0,0,0,0);row.setSpacing(6)
        if state=='running':
            preview=self.button('预览',lambda checked=False,j=j:self.preview_running(j))
            preview.setToolTip('打开正在生成的双语译文;在浏览器中按 F5 刷新查看新翻段落')
            row.addWidget(preview)
            wrap=QWidget();wrap.setLayout(row);return wrap
        if state=='completed':
            if j.get('artifacts'):
                export=self.button('导出',lambda checked=False,j=j:self.export_translation(j))
                export.setToolTip('导出译文 PDF');row.addWidget(export)
            folder=self.button('目录',lambda checked=False,j=j:self.open_job_folder(j))
            folder.setToolTip('打开译文所在文件夹');row.addWidget(folder)
        delete=self.button('删除',lambda checked=False,j=j:self.delete_job(j))
        delete.setToolTip('删除该任务记录及产物文件');row.addWidget(delete)
        wrap=QWidget();wrap.setLayout(row);return wrap

    def preview_running(self,j):
        from pathlib import Path
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        output=Path(j['outputDir'])
        # Same UUID guard as the store: never open an arbitrary stored directory.
        if output.name!=j['id'] or output.parent.name!='jobs':return
        artifact=output/'translated.html'
        if artifact.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(artifact)))
        else:
            QMessageBox.information(self,'预览','译文文件尚未生成,请稍后再试。')

    def open_job_folder(self,j):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        folder=self.guard(lambda:self.service.job_output_dir(j['id']))
        if folder is not None:QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def delete_job(self,j):
        if QMessageBox.question(self,'删除任务记录','删除该翻译任务记录及其产物文件？已导出的副本不受影响。')!=QMessageBox.StandardButton.Yes:return
        identifier=j['id']
        def remove():
            self.service.delete_job(identifier);return True
        if self.guard(remove):self.refresh_jobs()

    def refresh_boundary(self,*args):
        settings=self.service.get_settings()
        self.send_boundary.setText(f"实际模型 API：{settings.get('endpoint','')}\n请求翻译范围：全文（arXiv HTML 路线不支持选页）。\n逐段发送至模型 API；远程请求可能计费。")

    def export_translation(self,j):
        index=0
        if len(j['artifacts'])>1:
            name,ok=QInputDialog.getItem(self,'选择导出文件','翻译结果',j['artifacts'],0,False)
            if not ok:return
            index=j['artifacts'].index(name)
        name=j['artifacts'][index]
        if name.lower().endswith('.html'):
            p,_=QFileDialog.getSaveFileName(self,'导出翻译结果',name,'HTML (*.html)')
        else:
            p,_=QFileDialog.getSaveFileName(self,'导出翻译结果',name,'PDF (*.pdf)')
        if p:self.guard(lambda:self.service.export_translation(j['id'],index,p))

