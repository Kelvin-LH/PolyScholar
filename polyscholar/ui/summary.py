# SPDX-License-Identifier: AGPL-3.0-only
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

class SummaryPage:
    def summary(self):
        l=self.page('证据摘要','摘要应关联原文页码和段落；此功能尚未实现。')
        l.addStretch();label=QLabel('证据定位与模型摘要正在研发\n暂不提供自动生成结果');label.setAlignment(Qt.AlignmentFlag.AlignCenter);l.addWidget(label);l.addStretch()

