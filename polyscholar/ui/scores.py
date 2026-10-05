# SPDX-License-Identifier: AGPL-3.0-only
"""Readable stored agent reviews / 可读的本地 Agent 评阅记录。"""
from html import escape
import json
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QTabWidget, QTextBrowser, QVBoxLayout,
)
from .managed_dialog import ManagedIODialog
from .workers import safe_error

KIND_NAMES = {'paper': '论文评阅', 'confidence': '文本内置信度', 'summary': 'Agent 提炼'}
DIM_NAMES = {
    'novelty': '创新点', 'innovation_degree': '创新程度', 'effectiveness': '实际效果',
    'rigor': '方法严谨性', 'clarity': '表达清晰度', 'evidence': '证据支持',
    'consistency': '内部一致性', 'traceability': '来源可追溯性', 'plausibility': '结果合理性',
}
SUMMARY_SECTIONS = (('problem', '研究问题'), ('method', '研究方法'),
                    ('results', '报告结果'), ('limitations', '局限与不足'))


def score_text(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return '—'
    return f'{value:g}'


def _text(value):
    return escape(str(value)).replace('\n', '<br>')


def _findings(items, label):
    if not isinstance(items, list):
        return ''
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        point = item.get('point') or item.get('flag') or item.get('text')
        if not point:
            continue
        text = _text(point)
        if item.get('severity') is not None:
            text += '（报告严重度：' + _text(item['severity']) + '）'
        evidence = item.get('evidence')
        if isinstance(evidence, list):
            evidence = '；'.join(str(part) for part in evidence)
        if evidence:
            text += '<br><span style="color:#52645f">出处：' + _text(evidence) + '</span>'
        agents = item.get('agents')
        if isinstance(agents, list) and agents:
            text += '<br>记录来源：' + _text('、'.join(str(agent) for agent in agents))
        rows.append('<li>' + text + '</li>')
    return '<h3>' + _text(label) + '</h3><ul>' + ''.join(rows) + '</ul>' if rows else ''


def _render_kind(kind, entry):
    """Render only stored values; absent fields never become inferred zeroes.

    只呈现已有记录；缺失的维度与分数不能补成零分或科学结论。
    """
    detail = entry.get('detail') if isinstance(entry.get('detail'), dict) else {}
    lines = ['<h2>' + _text(KIND_NAMES.get(kind, kind)) + '</h2>']
    if kind != 'summary':
        lines.append('<p><b>报告评分：' + score_text(entry.get('score')) + ' / 100</b></p>')
    lines.append('<p>' + _text(entry.get('rationale') or '尚未记录评阅理由。') + '</p>')
    if detail.get('rubric_version'):
        lines.append('<p>评阅规则版本：' + _text(detail['rubric_version']) + '</p>')
        if kind == 'paper' and detail['rubric_version'] == '1.0.0':
            lines.append('<p>此为旧版评分；按新规则评阅需要重新评分，已有分数保留。</p>')
    if detail.get('verdict'):
        lines.append('<p>Agent 原始判断用语：' + _text(detail['verdict']) + '</p>')
    aggregation = detail.get('aggregation')
    if isinstance(aggregation, dict):
        parts = []
        if aggregation.get('method'):
            parts.append('汇总方法：' + str(aggregation['method']))
        if 'spread' in aggregation:
            parts.append('报告分差：' + str(aggregation['spread']))
        if aggregation.get('rechecked'):
            parts.append('记录标注已复核')
        if aggregation.get('unresolved_disagreement'):
            parts.append('仍有未解决分歧')
        if parts:
            lines.append('<p>' + _text('；'.join(parts)) + '</p>')
        if aggregation.get('recheck_reason'):
            lines.append('<p>复核原因：' + _text(aggregation['recheck_reason']) + '</p>')
        for key, label in (('initial_critical_items', '首轮关键分项分歧'),
                           ('critical_items', '当前关键分项分歧')):
            items = aggregation.get(key)
            if isinstance(items, list) and items:
                lines.append('<p>' + label + '：' + _text('、'.join(str(item) for item in items)) + '</p>')
    dimensions = detail.get('dimensions')
    if isinstance(dimensions, list) and dimensions:
        lines.append('<h3>维度与理由</h3>')
        for dimension in dimensions:
            if not isinstance(dimension, dict):
                continue
            key = str(dimension.get('key') or '')
            name = DIM_NAMES.get(key, key or '未命名维度')
            value = score_text(dimension.get('score'))
            maximum = dimension.get('max')
            bounds = ' / ' + score_text(maximum) if maximum is not None else ''
            lines.append('<p><b>' + _text(name) + '：' + value + bounds + '</b><br>'
                         + _text(dimension.get('rationale') or '该维度理由未记录。') + '</p>')
    for key, label in SUMMARY_SECTIONS:
        lines.append(_findings(detail.get(key), label))
    for key, label in (('strengths', '支持理由'), ('weaknesses', '局限与失分理由'),
                       ('red_flags', '待核查问题')):
        lines.append(_findings(detail.get(key), label))
    disagreement = detail.get('disagreement_table')
    if isinstance(disagreement, list) and disagreement:
        lines.append('<h3>分歧记录</h3><ul>' + ''.join('<li>' + _text(row) + '</li>' for row in disagreement) + '</ul>')
    reports = detail.get('sub_scores')
    if isinstance(reports, list) and reports:
        lines.append('<h3>各 Agent 的评阅记录</h3>')
        for report in reports:
            if not isinstance(report, dict):
                continue
            name = report.get('agent_id') or report.get('agent') or '未命名 Agent'
            model = ' · ' + str(report['model']) if report.get('model') else ''
            lines.append('<p><b>' + _text(str(name) + model) + '</b> · 报告总分 '
                         + score_text(report.get('total')) + '</p>')
            original = report.get('original_output')
            if isinstance(original, dict):
                lines.append('<p>' + _text(original.get('rationale') or original.get('summary')
                                          or '详细字段可在原始记录中查看。') + '</p>')
                lines.append(_findings(original.get('strengths'), '支持理由'))
                lines.append(_findings(original.get('weaknesses'), '局限'))
            elif isinstance(original, str):
                lines.append('<p>' + _text(original) + '</p>')
    if not detail:
        lines.append('<p>尚未保存结构化明细，可在生成评阅记录后查看维度、出处和分歧。</p>')
    return ''.join(lines)


class ScoreDetailDialog(ManagedIODialog):
    def __init__(self, window, document_id, title, scores):
        super().__init__(window)
        self.document_id = document_id
        self._scores = scores
        self.setWindowTitle('Agent 评阅 · ' + (title or '未命名文献'))
        self.resize(800, 680)
        self.setMinimumSize(560, 440)
        layout = QVBoxLayout(self)
        heading = QLabel(title or '未命名文献')
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        heading.setObjectName('heading')
        layout.addWidget(heading)
        hint = QLabel('评分与提炼来自已保存的 Agent 评阅记录，供阅读核查；分数不是科学结论真伪的概率。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName('Agent 评阅类别')
        present = [(kind, scores[kind]) for kind in KIND_NAMES if isinstance(scores.get(kind), dict)]
        for kind, entry in present:
            browser = QTextBrowser()
            browser.setOpenExternalLinks(False)
            browser.setOpenLinks(False)
            browser.setAccessibleName(KIND_NAMES[kind] + '明细')
            browser.setHtml(_render_kind(kind, entry))
            self.tabs.addTab(browser, KIND_NAMES[kind])
        if present:
            raw = QPlainTextEdit()
            raw.setReadOnly(True)
            raw.setAccessibleName('原始评阅 JSON')
            raw.setPlainText(json.dumps(scores, ensure_ascii=False, indent=2))
            self.tabs.addTab(raw, '原始记录')
        else:
            empty = QLabel('这篇文献尚未保存 Agent 评阅记录。生成或导入记录后，可在此查看分数、理由与出处。')
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tabs.addTab(empty, '暂无记录')
        layout.addWidget(self.tabs, 1)
        self.status = QLabel('')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.export_button = QPushButton('导出评阅记录 JSON…')
        self.export_button.clicked.connect(self._export)
        row.addWidget(self.export_button)
        row.addStretch()
        close = QPushButton('关闭')
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.update_controls()

    def update_controls(self):
        if hasattr(self, 'export_button'):
            busy = self._busy or self.window.io_worker is not None or self.window._closing or self._closed
            self.export_button.setEnabled(not busy and any(self._scores.values()))

    def _export(self):
        if self._busy or self.window.io_worker is not None or self.window._closing:
            return
        path, _ = QFileDialog.getSaveFileName(self, '导出 Agent 评阅记录', 'agent-review.json', 'JSON (*.json)')
        if not path:
            return

        def work():
            try:
                self.window.service.export_score_details(self.document_id, path)
                return True, '评阅记录已导出。'
            except Exception as error:
                return False, safe_error(error)

        self.run(work, lambda result: self.show_status(result[1]), '正在导出评阅记录…')

    def reject(self):
        self.close()
