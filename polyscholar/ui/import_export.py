# SPDX-License-Identifier: AGPL-3.0-only
"""复用已有业务流程的原生导入导出入口。

Native import/export entry points reuse existing validated workflows.
"""
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox
from .managed_dialog import ManagedIODialog


class ImportExportDialog(ManagedIODialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle('导入与导出')
        self.resize(740, 580)
        layout = QVBoxLayout(self)
        heading = QLabel('导入与导出')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        self.actions = []
        imports = QGroupBox('导入本机资料')
        imports_layout = QVBoxLayout(imports)
        self.add_route(imports_layout, 'PDF 文件', '选择一个或多个本地 PDF，加入当前集合。', window.import_pdf)
        self.add_route(imports_layout, 'BibTeX / RIS / CSL-JSON', '预览书目字段与转换警告，勾选后导入。', window.open_citation_import)
        self.add_route(imports_layout, 'Zotero 资料目录', '迁移集合、书目与受管文件，查看原始档案及收据。', window.open_zotero_migration)
        layout.addWidget(imports)
        exports = QGroupBox('导出本机资料')
        exports_layout = QVBoxLayout(exports)
        self.add_route(exports_layout, '引文与书目', '选择条目，导出 BibTeX、RIS 或 CSL-JSON。', window.open_citations)
        self.add_route(exports_layout, '译文 PDF', '在翻译任务中选择实际译文产物并导出。', lambda: window.nav.setCurrentRow(2))
        self.add_route(exports_layout, '迁移档案文件', '通过本地收据选择受管文件并导出原始字节。', window.open_zotero_migration)
        layout.addWidget(exports)
        hint = QLabel('引文文件只交换书目信息。完整资料备份与恢复、CSV／EndNote 格式仍待适配。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.status = QLabel('所有导入导出在本机进行。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        close = QPushButton('关闭')
        close.clicked.connect(self.close)
        layout.addWidget(close)
        self.update_controls()

    def add_route(self, layout, title, description, callback):
        row = QHBoxLayout()
        button = QPushButton(title)
        button.setMinimumWidth(215)
        button.clicked.connect(lambda: self.open_route(callback))
        label = QLabel(description)
        label.setWordWrap(True)
        row.addWidget(button)
        row.addWidget(label, 1)
        layout.addLayout(row)
        self.actions.append(button)

    def open_route(self, callback):
        if self.window.io_worker is not None or self.window._closing:
            return
        # 主窗口继续拥有新对话框及 IO；中心自身不复制业务逻辑。
        # The main window owns the next dialog and IO; this center does not duplicate business logic.
        self.close()
        callback()

    def update_controls(self):
        busy = self.window.io_worker is not None or self.window._closing or self._closed
        for button in getattr(self, 'actions', []):
            button.setEnabled(not busy)
