# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only viewer for the machine-readable score detail stored per document.

只读渲染 desktop_scores.detail(rubric 第 6 章的汇总报告 JSON)。界面只展示
已存储的字段:某段缺失就原样标注"未存储",不做任何推断、补齐或美化,
导出按钮写出的也是未经改动的原始 JSON。
"""
from html import escape
import json
from pathlib import Path

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QMessageBox, QPushButton, QTextBrowser, QTabWidget, QVBoxLayout)

KIND_NAMES = {'paper': '论文评分', 'confidence': '置信度评分', 'summary': 'AI 提炼'}
DIM_NAMES = {'novelty': '创新点', 'innovation_degree': '创新程度', 'effectiveness': '实际效果',
             'rigor': '方法严谨性', 'clarity': '清晰度',
             'evidence': '证据强度', 'consistency': '内部一致性',
             'traceability': '来源可溯源性', 'plausibility': '结果合理性'}
VERDICTS = ('高度可信', '较可信', '存疑', '明显存疑', '高度存疑')
SUMMARY_SECTIONS = (('problem', '解决的问题'), ('method', '使用的方法'),
                    ('results', '实验效果'), ('limitations', '不足与缺陷'))


def _text(value):
    return escape(str(value)).replace('\n', '<br>')


def _dim_line(dim):
    key = dim.get('key')
    name = DIM_NAMES.get(key, key or '?')
    max_score = dim.get('max')
    bounds = ' / %s' % max_score if isinstance(max_score, (int, float)) else ''
    line = '<b>%s</b>：%g%s' % (_text(name), dim.get('score', 0), bounds)
    rationale = (dim.get('rationale') or '').strip()
    if rationale:
        line += '<br><span style="color:#5F6C65">%s</span>' % _text(rationale)
    return '<p style="margin:2px 0">%s</p>' % line


def _findings(items, label):
    if not isinstance(items, list) or not items:
        return ''
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = _text(item.get('point') or item.get('flag') or '')
        severity = item.get('severity')
        if severity is not None:
            row += '（严重度 %s）' % _text(severity)
        evidence = item.get('evidence')
        if isinstance(evidence, list):
            evidence = '；'.join(str(part) for part in evidence)
        if evidence:
            row += '<br><span style="color:#5F6C65">证据：%s</span>' % _text(evidence)
        rows.append('<li>%s</li>' % row)
    if not rows:
        return ''
    return '<h3>%s</h3><ul>%s</ul>' % (label, ''.join(rows))


def _summary_sections(detail):
    """Render the two-agent extraction sections; items carry their agent tags."""
    lines = []
    for key, label in SUMMARY_SECTIONS:
        items = detail.get(key)
        if not isinstance(items, list) or not items:
            continue
        rows = []
        for item in items:
            if not isinstance(item, dict) or not str(item.get('text', '')).strip():
                continue
            row = _text(item['text'])
            agents = item.get('agents')
            if isinstance(agents, list) and agents:
                row += ' <span style="color:#5F6C65">（%s）</span>' % _text('、'.join(str(a) for a in agents))
            evidence = item.get('evidence')
            if evidence:
                row += '<br><span style="color:#5F6C65">出处：%s</span>' % _text(evidence)
            rows.append('<li>%s</li>' % row)
        if rows:
            lines.append('<h3>%s</h3><ul>%s</ul>' % (label, ''.join(rows)))
    return lines


def _render_kind(kind, entry):
    detail = entry.get('detail') if isinstance(entry.get('detail'), dict) else {}
    score = entry.get('score')
    head = '<h2>%s' % _text(KIND_NAMES.get(kind, kind))
    if score is not None:
        head += '：%g / 100' % score
    head += '</h2>'
    lines = [head]
    version = detail.get('rubric_version')
    if version:
        lines.append('<p>打分文档版本：%s</p>' % _text(version))
    if kind == 'confidence' and detail.get('verdict') in VERDICTS:
        lines.append('<p>结论档位：<b>%s</b></p>' % _text(detail['verdict']))
    lines.append('<p>%s</p>' % _text((entry.get('rationale') or '').strip() or '（理由未存储）'))
    aggregation = detail.get('aggregation')
    if isinstance(aggregation, dict):
        parts = ['汇总方式：中位数', '极差：%s' % aggregation.get('spread', '未存储')]
        if aggregation.get('rechecked'):
            parts.append('已复核')
        if aggregation.get('unresolved_disagreement'):
            parts.append('复核后分歧未消除')
        lines.append('<p>%s</p>' % '；'.join(_text(part) for part in parts))
        reason = aggregation.get('recheck_reason')
        if reason:
            lines.append('<p style="color:#5F6C65">复核原因：%s</p>' % _text(reason))
    dimensions = detail.get('dimensions')
    if isinstance(dimensions, list) and dimensions:
        lines.append('<h3>维度明细</h3>')
        lines.extend(_dim_line(dim) for dim in dimensions if isinstance(dim, dict))
    lines.extend(_summary_sections(detail))
    lines.append(_findings(detail.get('strengths'), '优势（得分点）'))
    lines.append(_findings(detail.get('weaknesses'), '劣势（失分点）'))
    lines.append(_findings(detail.get('red_flags'), '红旗清单'))
    table = detail.get('disagreement_table')
    if isinstance(table, list) and table:
        lines.append('<h3>复核分歧对照</h3><ul>%s</ul>' % ''.join(
            '<li>%s</li>' % _text(item) for item in table))
    subs = detail.get('sub_scores')
    if isinstance(subs, list) and subs:
        lines.append('<h3>三个子代理盲评原文</h3>')
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            who = _text(sub.get('agent_id') or sub.get('agent') or '子代理')
            model = sub.get('model')
            total = sub.get('total')
            lines.append('<p style="margin:4px 0"><b>%s</b>%s：%s</p>' % (
                who, '（%s）' % _text(model) if model else '',
                '总分 %s' % total if total is not None else '总分未存储'))
            original = sub.get('original_output')
            if isinstance(original, (dict, list)):
                blob = json.dumps(original, ensure_ascii=False, indent=1)
            elif original is not None:
                blob = str(original)
            else:
                blob = ''
            if blob:
                lines.append('<pre style="background:#F6F8F6;font-size:8pt">%s</pre>' % _text(blob))
    if not any(isinstance(detail, dict) and detail.get(key) for key in
               ('dimensions', 'strengths', 'weaknesses', 'red_flags', 'sub_scores')):
        lines.append('<p style="color:#9A5B13">该评分没有可展示的明细 JSON（仅分数与理由）。</p>')
    return ''.join(lines)


class ScoreDetailDialog(QDialog):
    """One tab per score kind: switching to 置信度 is a click, not a long scroll.

    Each tab renders the stored detail verbatim; export writes the same JSON out.
    """

    def __init__(self, title, scores, parent=None):
        super().__init__(parent)
        self.setWindowTitle('评分明细 · ' + (title or ''))
        self.setModal(True)
        self.resize(760, 640)
        self._scores = scores
        layout = QVBoxLayout(self)
        present = [(kind, entry) for kind, entry in scores.items() if entry]
        if len(present) == 1:
            browser = QTextBrowser()
            browser.setOpenExternalLinks(False)
            browser.setHtml(_render_kind(*present[0]))
            layout.addWidget(browser, 1)
        else:
            tabs = QTabWidget()
            for kind, entry in present:
                browser = QTextBrowser()
                browser.setOpenExternalLinks(False)
                browser.setHtml(_render_kind(kind, entry))
                tabs.addTab(browser, KIND_NAMES.get(kind, kind))
            layout.addWidget(tabs, 1)
        row = QHBoxLayout()
        export = QPushButton('导出明细 JSON…')
        export.clicked.connect(self._export)
        row.addWidget(export)
        row.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        row.addWidget(buttons)
        layout.addLayout(row)

    def _export(self):
        kinds = [kind for kind, entry in self._scores.items() if entry]
        name = '-'.join(kinds) or 'scores'
        path, _ = QFileDialog.getSaveFileName(self, '导出评分明细 JSON',
                                              str(Path.home() / (name + '-detail.json')),
                                              'JSON (*.json)')
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self._scores, ensure_ascii=False, indent=2),
                                  encoding='utf-8')
        except OSError as error:
            QMessageBox.warning(self, '导出失败', '写入失败：%s' % error)
            return
        QMessageBox.information(self, '已导出', '明细已写入:\n' + path)
