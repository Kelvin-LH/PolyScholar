# SPDX-License-Identifier: AGPL-3.0-only
"""Native progressive disclosure. 原生分层展示，折叠不丢失编辑草稿。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)


class DisclosureSection(QWidget):
    """Hide existing controls without rebuilding them. 隐藏控件而非重建，保留草稿。"""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.toggle = QToolButton()
        self.toggle.setObjectName('disclosure')
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)
        self.toggle.toggled.connect(self.set_expanded)
        self.set_expanded(False)

    def set_expanded(self, expanded):
        self.toggle.setChecked(expanded)
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.content.setVisible(expanded)


class TwoLinePreview(QLabel):
    """Elide after two wrapped lines; keep the original elsewhere.

    两行截断仅用于展示，原文由评分卡完整保存；宽度变化时重新排版。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = ''
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)

    def set_source(self, text):
        self._source = ' '.join(text.split())
        self._reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self):
        width = max(1, self.contentsRect().width())
        metrics = self.fontMetrics()
        self.setFixedHeight(metrics.lineSpacing() * 2 + 4)
        layout = QTextLayout(self._source, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        first = layout.createLine()
        if not first.isValid():
            preview = ''
        else:
            first.setLineWidth(width)
            end = first.textLength()
            preview = self._source[:end].rstrip()
            if end < len(self._source):
                remaining = self._source[end:].lstrip()
                preview += '\n' + metrics.elidedText(
                    remaining, Qt.TextElideMode.ElideRight, width
                )
        layout.endLayout()
        self.setText(preview)


class ScoreCard(QWidget):
    """Compact stored conclusions with explicit access to the complete rationale.

    只展示已存结论；展开只影响本地视图，不触发模型或修改评分。
    """

    def __init__(self, name, empty_hint, parent=None):
        super().__init__(parent)
        self.name = name
        self.empty_hint = empty_hint
        self._rationale = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.header = QLabel(name + ' —')
        self.header.setTextFormat(Qt.TextFormat.PlainText)
        self.preview = TwoLinePreview()
        self.preview.setAccessibleName(name + '理由预览')
        self.full_text = QPlainTextEdit()
        self.full_text.setReadOnly(True)
        self.full_text.setAccessibleName(name + '完整理由')
        self.full_text.setFixedHeight(self.fontMetrics().lineSpacing() * 8 + 20)
        self.expand_button = QPushButton('展开理由')
        self.expand_button.setObjectName('scoreToggle')
        self.expand_button.setAccessibleName(name + '展开或收起理由')
        self.expand_button.setCheckable(True)
        self.expand_button.toggled.connect(self._toggle)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(self.header, 1)
        header_row.addWidget(self.expand_button)
        layout.addLayout(header_row)
        layout.addWidget(self.preview)
        layout.addWidget(self.full_text)
        self.set_entry(None, reset=True)

    def set_entry(self, entry, reset=False):
        value = (entry or {}).get('score')
        status = ('已提炼' if entry else '暂无') if self.name == 'AI 提炼' else (
            '暂无' if value is None else f'{value:g} / 100'
        )
        self.header.setText(f'{self.name} · {status}')
        self._rationale = ((entry or {}).get('rationale') or '').strip()
        text = self._rationale or ('已存结果；查看完整明细核对。' if entry else self.empty_hint)
        self.preview.set_source(text)
        self.full_text.setPlainText(self._rationale)
        self.expand_button.setVisible(bool(self._rationale))
        if reset or not self._rationale:
            self.expand_button.setChecked(False)
        self._toggle(self.expand_button.isChecked())

    def _toggle(self, expanded):
        self.preview.setVisible(not expanded)
        self.full_text.setVisible(expanded)
        self.expand_button.setText('收起理由' if expanded else '展开理由')
