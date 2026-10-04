# SPDX-License-Identifier: AGPL-3.0-only
from PySide6.QtWidgets import QLabel, QFormLayout, QLineEdit, QComboBox, QHBoxLayout, QFileDialog, QSpinBox, QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox
from .workers import FetchModels

class SettingsPage:
    def settings(self):
        l=self.page('设置','运行环境随安装包提供；文献与笔记保存在本机。');l.parentWidget().setMinimumHeight(900)
        form=QFormLayout();s=self.service.get_settings();self.endpoint=QLineEdit(s.get('endpoint',''));form.addRow('模型 API 地址',self.endpoint)
        self.model=QComboBox();self.model.setEditable(True);self.model.addItem(s.get('model',''));row=QHBoxLayout();row.addWidget(self.model,1);self.fetch_button=self.button('获取可用模型',self.fetch_models);row.addWidget(self.fetch_button);form.addRow('模型名称',row)
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key_storage=QComboBox();self.key_storage.addItem('仅本次会话（默认）','session');self.key_storage.addItem('保存到系统凭据库','os');form.addRow('密钥保存方式',self.key_storage)
        self.key.setPlaceholderText('仅本次会话保存，退出后清除');form.addRow('API 密钥',self.key)
        self.engine=QComboBox();self.engine.addItem('BabelDOC','babeldoc');self.engine.addItem('PDFMathTranslate','pdfmathtranslate');self.engine.setCurrentIndex(max(0,self.engine.findData(s.get('engine'))));form.addRow('翻译引擎',self.engine)
        self.runtime=QLabel(self.service.discover_engine(self.engine.currentData())['message']);self.runtime.setWordWrap(True);form.addRow('运行环境',self.runtime)
        self.cache=QLineEdit(s.get('cachePath',''));self.cache.setPlaceholderText('使用系统默认缓存目录');r=QHBoxLayout();r.addWidget(self.cache,1);r.addWidget(self.button('选择文件夹',self.choose_cache));form.addRow('缓存目录',r)
        self.timeout=QSpinBox();self.timeout.setRange(1,86400);self.timeout.setSuffix(' 秒');self.timeout.setValue(s.get('timeoutSeconds',600));form.addRow('任务超时',self.timeout)
        self.source=QLineEdit(s.get('sourceLanguage','en'));self.target=QLineEdit(s.get('targetLanguage','zh'));form.addRow('源语言',self.source);form.addRow('目标语言',self.target);l.addLayout(form)
        self.model_status=QLabel('可从服务接口获取模型列表，也可手动输入模型名称。');self.model_status.setObjectName('muted');l.addWidget(self.model_status)
        l.addWidget(self.button('保存设置',self.save_settings,True));l.addWidget(self.button('清除会话密钥',lambda:self.service.set_session_key('')))
        l.addWidget(self.button('删除已保存的密钥',lambda:self.guard(self.service.clear_stored_api_key)))
        l.addWidget(self.button('预览脱敏诊断报告',self.preview_diagnostics))
        note=QLabel('允许模型 API、DOI 元数据查询及模型/字体下载。\n翻译时所选内容会发送至配置的 API；其他私人数据在本机保存。\nAGPL-3.0 · 允许商业使用，适用源码公开与通知义务。');note.setWordWrap(True);l.addWidget(note);l.addStretch()

    def choose_cache(self):
        p=QFileDialog.getExistingDirectory(self,'选择缓存目录')
        if p:self.cache.setText(p)

    def save_settings(self):
        s=self.service.get_settings();s.update(endpoint=self.endpoint.text().strip(),model=self.model.currentText().strip(),engine=self.engine.currentData(),cachePath=self.cache.text().strip(),sourceLanguage=self.source.text().strip(),targetLanguage=self.target.text().strip(),timeoutSeconds=self.timeout.value())
        def save():
            self.service.save_settings(s)
            if self.key.text():
                if self.key_storage.currentData() == 'os':
                    self.service.store_api_key(self.key.text())
                else:
                    self.service.set_session_key(self.key.text())
                self.key.clear()
            status=self.service.discover_engine(s['engine']);self.runtime.setText(status['message']);self.refresh_boundary();self.update_summary_range()
        self.guard(save)

    def fetch_models(self):
        if self.model_worker and self.model_worker.isRunning():return
        self.fetch_button.setEnabled(False);self.model_status.setText('正在获取可用模型…')
        self.model_worker=FetchModels(self.service,self.endpoint.text().strip(),self.key.text())
        self.model_worker.ready.connect(self.models_ready);self.model_worker.failed.connect(self.models_failed);self.model_worker.finished.connect(self.model_finished);self.model_worker.start()

    def model_finished(self):
        self.fetch_button.setEnabled(True)
        if self._closing:self.close()

    def models_ready(self,models):
        previous=self.model.currentText();self.model.clear();self.model.addItems(models)
        if previous in models:self.model.setCurrentText(previous)
        self.model_status.setText(f'已获取 {len(models)} 个模型。' if models else '接口未返回模型，可手动填写。')

    def models_failed(self,message):self.model_status.setText(message)

    def preview_diagnostics(self):
        report=self.guard(self.service.diagnostic_report)
        if report is None:return
        dialog=QDialog(self);dialog.setWindowTitle('脱敏诊断报告预览');dialog.resize(680,480)
        layout=QVBoxLayout(dialog);layout.addWidget(QLabel('请查看报告内容，再选择是否保存到本机。'))
        preview=QTextEdit();preview.setReadOnly(True);preview.setPlainText(report);layout.addWidget(preview)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        path,_=QFileDialog.getSaveFileName(self,'保存脱敏诊断报告','polyscholar-diagnostics.json','JSON (*.json)')
        if path:self.guard(lambda:self.service.export_diagnostics(path))
