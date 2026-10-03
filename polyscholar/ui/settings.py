# SPDX-License-Identifier: AGPL-3.0-only
from PySide6.QtWidgets import QLabel, QFormLayout, QLineEdit, QComboBox, QHBoxLayout, QFileDialog, QSpinBox, QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox
from .workers import FetchModels

class SettingsPage:
    def settings(self):
        l=self.page('设置','运行环境随安装包提供；文献与笔记保存在本机。');l.parentWidget().setMinimumHeight(900)
        form=QFormLayout();s=self.service.get_settings();self.endpoint=QLineEdit(s.get('endpoint',''));form.addRow('模型 API 地址',self.endpoint)
        self.model=QComboBox();self.model.setEditable(True);self.model.addItem(s.get('model',''));row=QHBoxLayout();row.addWidget(self.model,1);self.fetch_button=self.button('获取可用模型',self.fetch_models);row.addWidget(self.fetch_button);form.addRow('模型名称',row)
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setPlaceholderText('输入密钥；选择存储方式后保存');form.addRow('API 密钥',self.key)
        self.key_storage=QComboBox()
        self.key_storage.addItem('不保存（仅本次会话，退出清除）', '')
        self.key_storage.addItem('系统凭据管理器（加密，推荐）', 'os')
        self.key_storage.addItem('配置文件 ~/.polyscholar/api_key（明文，与常见 agent 相同）', 'file')
        self.key_storage.setCurrentIndex(max(0,self.key_storage.findData(s.get('keyStorage') or '')))
        form.addRow('密钥存储方式',self.key_storage)
        self.key_status=QLabel('');self.key_status.setObjectName('muted');self.key_status.setWordWrap(True);form.addRow('',self.key_status)
        self._refresh_key_status()
        self.cache=QLineEdit(s.get('cachePath',''));self.cache.setPlaceholderText('使用系统默认缓存目录');r=QHBoxLayout();r.addWidget(self.cache,1);r.addWidget(self.button('选择文件夹',self.choose_cache));form.addRow('缓存目录',r)
        self.timeout=QSpinBox();self.timeout.setRange(1,86400);self.timeout.setSuffix(' 秒');self.timeout.setValue(s.get('timeoutSeconds',600));form.addRow('任务超时',self.timeout)
        self.source=QLineEdit(s.get('sourceLanguage','en'));self.target=QLineEdit(s.get('targetLanguage','zh'));form.addRow('源语言',self.source);form.addRow('目标语言',self.target);l.addLayout(form)
        self.model_status=QLabel('可从服务接口获取模型列表，也可手动输入模型名称。');self.model_status.setObjectName('muted');l.addWidget(self.model_status)
        l.addWidget(self.button('保存设置',self.save_settings,True))
        key_actions=QHBoxLayout();key_actions.addWidget(self.button('清除会话密钥',lambda:self.service.set_session_key('')));key_actions.addWidget(self.button('删除已保存密钥',self.delete_stored_key));l.addLayout(key_actions)
        l.addWidget(self.button('预览脱敏诊断报告',self.preview_diagnostics))
        note=QLabel('允许模型 API、DOI 元数据查询及模型/字体下载。\n翻译时所选内容会发送至配置的 API；其他私人数据在本机保存。\nAGPL-3.0 · 允许商业使用，适用源码公开与通知义务。');note.setWordWrap(True);l.addWidget(note);l.addStretch()

    def choose_cache(self):
        p=QFileDialog.getExistingDirectory(self,'选择缓存目录')
        if p:self.cache.setText(p)

    def save_settings(self):
        s=self.service.get_settings();s.update(endpoint=self.endpoint.text().strip(),model=self.model.currentText().strip(),cachePath=self.cache.text().strip(),sourceLanguage=self.source.text().strip(),targetLanguage=self.target.text().strip(),timeoutSeconds=self.timeout.value(),keyStorage=self.key_storage.currentData() or '')
        def save():
            self.service.save_settings(s)
            key=self.key.text()
            if key:
                mode=self.key_storage.currentData()
                if mode:
                    self.service.store_api_key(key,mode)
                    self.key_status.setText('密钥已保存到系统凭据管理器（加密），重启后自动使用。' if mode=='os'
                                            else '密钥已明文保存到 ~/.polyscholar/api_key（与常见 agent 相同）；请勿将该文件纳入备份同步或公开。')
                    self.key.setPlaceholderText('留空即使用已保存的密钥；更换时重新输入')
                else:
                    self.service.set_session_key(key)
                    self.key_status.setText('本次已选择“不保存”：密钥仅本会话有效，退出即清除；要跨重启请选择存储方式后重新粘贴保存。')
                    self.key.setPlaceholderText('密钥未持久化；下次启动需重新输入')
                self.key.clear()
            self.refresh_boundary();self.update_summary_range()
        self.guard(save)

    def _refresh_key_status(self):
        location=self.service.stored_key_location()
        self.key_status.setText({'os':'系统凭据管理器中已保存密钥（加密），重启后自动使用。',
                                 'file':'~/.polyscholar/api_key 中已保存密钥（明文，agent 风格），重启后自动使用；注意备份与同步泄漏风险。',
                                 None:'尚未保存密钥；"不保存"方式下密钥仅本次会话有效。'}[location])

    def delete_stored_key(self):
        def remove():
            self.service.clear_stored_api_key();self._refresh_key_status();return True
        self.guard(remove)

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
