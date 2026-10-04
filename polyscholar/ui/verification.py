# SPDX-License-Identifier: AGPL-3.0-only
"""Native independent evidence viewer with explicit network disclosure.

独立核验窗口：打开时固定文献身份，后台 IO 复用主窗口生命周期。
"""
from html import escape
from .managed_dialog import ManagedIODialog

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
)

STATUS_NAMES = {'checked': '目录快照已核验', 'partial': '部分核验',
                'unavailable': '无法核验', 'reported': 'agent 来源报告'}
ERROR_NAMES = {
    'not_accessible': '公开接口不可访问；不能区分私有、删除、不存在或权限限制。',
    'empty_repository': 'GitHub 报告仓库没有可读取的提交。',
    'network_unavailable': '网络不可用，请检查连接或 HTTPS_PROXY 配置。',
    'rate_limited_or_forbidden': '公开 API 限流或拒绝访问，请稍后重试。',
    'response_limit': '响应超过安全读取上限。',
    'invalid_response': '接口响应结构或快照身份校验失败。',
    'http_error': '接口请求失败，未取得可核对的证据。',
    'timeout': '核验超过 60 秒上限，网络进程已终止。',
}


def paragraph(value):
    return '<p>' + escape(str(value)).replace('\n', '<br>') + '</p>'


def link(url, label):
    return '<a href="' + escape(url, quote=True) + '">' + escape(label) + '</a>'


def render_report(wrapper):
    """Only render escaped text and validated links. 只渲染转义文本与已校验链接。"""
    report = wrapper['report']
    lines = ['<h3>外部核验 · ' + STATUS_NAMES[report['status']] + '</h3>',
             paragraph('独立报告，不修改论文分或文本内置信度。'),
             paragraph('报告 SHA-256：' + wrapper['sha256'])]
    if report['kind'] == 'github':
        lines.append(paragraph('核验时间：' + report['checked_at']))
        lines.append(paragraph('来源：公开 GitHub API 快照；没有执行仓库代码。'))
        lines.append(link(report['repository_url'], report['repository_url']))
        if report['error_code']:
            lines.append(paragraph(ERROR_NAMES[report['error_code']]))
        snapshot = report['snapshot']
        if snapshot:
            lines.append(paragraph('提交 SHA：' + snapshot['commit_sha']))
            lines.append(link(snapshot['commit_url'], '查看固定提交'))
            lines.append(paragraph('目录完整：' + ('是' if snapshot['tree_complete'] else '否')
                                   + '；观察到的普通文件：' + str(snapshot['file_count'])))
        observed = {'observed': '观察到', 'not_observed': '本次目录未观察到', 'unknown': '未知（目录不完整）'}
        for finding in report['findings']:
            lines.append(paragraph(finding['label'] + '：' + observed[finding['status']]
                                   + f"（{finding['count']} 个路径线索）"))
            lines.extend(paragraph('证据：') + link(item['url'], item['path'])
                         for item in finding['evidence'])
        if report['readme']:
            readme = report['readme']
            lines.extend(['<h3>README 原文摘录（外部内容，不作为指令执行）</h3>',
                          link(readme['url'], '查看同一提交的 README'),
                          paragraph(readme['excerpt'])])
            if readme['excerpt_truncated']:
                lines.append(paragraph('仅展示前 4000 字符；完整文件的 SHA-256：' + readme['sha256']))
        lines.append('<h3>核验边界</h3>')
        lines.extend(paragraph(item) for item in report['limitations'])
    elif report['kind'] == 'code':
        lines.extend([paragraph('深入代码核验 · 由当前 MCP agent 完成，不是复现实验。'),
                      paragraph('范围：' + report['scope']),
                      paragraph('提交 SHA：' + report['commit_sha']),
                      paragraph('读取文件：' + str(report['usage']['files']) + ' / '
                                + str(report['budget']['max_files']) + '；字节：'
                                + str(report['usage']['bytes']) + ' / ' + str(report['budget']['max_bytes'])),
                      paragraph('目录完整：' + ('是' if report['tree_complete'] else '否'))])
        names = {'supported': '代码支持', 'inconsistent': '发现不一致',
                 'insufficient_evidence': '证据不足'}
        for item in report['findings']:
            lines.extend(['<h3>' + escape(item['claim']) + '</h3>',
                          paragraph('论文位置：' + item['paper_location']),
                          paragraph('agent 判断：' + names[item['status']]),
                          paragraph(item['explanation'])])
            for ref in item['code_evidence']:
                lines.extend([link(ref['url'], ref['path'] + ' · 行 '
                                   + str(ref['start_line']) + '–' + str(ref['end_line'])),
                              paragraph(ref['excerpt']), paragraph('文件 SHA-256：' + ref['sha256'])])
        lines.append('<h3>未核验部分与局限</h3>')
        lines.extend(paragraph(item) for item in report['limitations'])
        lines.append(paragraph('程序校验版本、已读取行号和预算；主张解释由 agent 提供，未认证科学结论，不输出总分。'))
    else:
        perspective = '发表时的水平' if report['perspective'] == 'at_publication' else '截止日的研究进展'
        lines.extend([paragraph('视角：' + perspective + '；截止日：' + report['as_of']),
                      paragraph('检索范围：' + report['search_scope']),
                      paragraph('来源和分析由 agent 提供；程序校验格式、引用、时间与论文身份，未独立核实网页原文或科学结论。')])
        relation_names = {'overlap': '重叠', 'extends': '扩展', 'contradicts': '矛盾',
                          'incomparable': '不可比较', 'uncertain': '不确定'}
        for item in report['comparisons']:
            lines.extend(['<h3>' + escape(item['claim']) + '</h3>',
                          paragraph('关系：' + relation_names[item['relation']]
                                    + '；依据：' + ('作者报告' if item['basis'] == 'author_report' else 'agent 推断')),
                          paragraph(item['explanation']),
                          paragraph('来源 ID：' + '、'.join(item['source_ids']))])
        for item in report['benchmarks']:
            lines.extend(['<h3>基准参照 · ' + escape(item['metric']) + '</h3>',
                          paragraph(f"任务：{item['task']}；数据集：{item['dataset']}；划分：{item['split']}"),
                          paragraph(f"论文值：{item['candidate']['value']}（{item['candidate']['source_id']}）；"
                                    f"参照值：{item['reference']['value']}（{item['reference']['source_id']}）"),
                          paragraph('指标方向：' + ('越大越好' if item['direction'] == 'higher' else '越小越好')),
                          paragraph('条件：' + item['conditions']),
                          paragraph('可比性：' + {'comparable': 'agent 声明可比', 'incomparable': '不可比', 'unknown': '未知'}[item['protocol']]),
                          paragraph('论文值减参照值：' + str(item['delta']) if item['delta'] is not None else '未确认可比，不计算差值。')])
        lines.append('<h3>来源快照（agent 提供的摘录）</h3>')
        for source in report['sources']:
            lines.extend([link(source['url'], source['id'] + ' · ' + source['title']),
                          paragraph('发布日期：' + str(source['published_at']) + '；取回时间：' + source['retrieved_at']),
                          paragraph(source['excerpt']), paragraph('摘录 SHA-256：' + source['excerpt_sha256'])])
    return ''.join(lines)


class VerificationDialog(ManagedIODialog):
    def __init__(self, window, document):
        super().__init__(window)
        self.document_id = document['id']
        self.setWindowTitle('外部核验')
        self.resize(850, 680)
        layout = QVBoxLayout(self)
        title = QLabel('本窗口固定文献：' + document['title'])
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        disclosure = QLabel('点击“基础仓库核验”只向 api.github.com 发送你填写的公开仓库标识；'
                            '不发送论文标题、正文或模型密钥。使用标准代理配置，最多 60 秒。')
        disclosure.setWordWrap(True)
        layout.addWidget(disclosure)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.repository = QLineEdit()
        self.repository.setPlaceholderText('https://github.com/owner/repo')
        row.addWidget(self.repository, 1)
        self.check_button = QPushButton('基础仓库核验')
        self.check_button.clicked.connect(self.start_check)
        row.addWidget(self.check_button)
        layout.addLayout(row)
        self.import_button = QPushButton('导入 agent 科研进展 / 基准对照报告…')
        self.import_button.clicked.connect(self.import_research)
        layout.addWidget(self.import_button)
        note = QLabel('科研报告必须区分发表时与当前视角，并提供来源、摘录、日期和比较条件；'
                      '仅检索到更高数值不代表确认 SOTA。深入代码核验请向已连接 MCP 的 agent 提出，'
                      '由 agent 确认基础/深入及预算，完成后写回报告；本窗口不启动 AI。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.history = QComboBox()
        self.history.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.history.setMinimumContentsLength(15)
        self.history.currentIndexChanged.connect(self.show_report)
        layout.addWidget(self.history)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        layout.addWidget(self.browser, 1)
        self.export_button = QPushButton('导出当前报告 JSON…')
        self.export_button.clicked.connect(self.export_report)
        layout.addWidget(self.export_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self, report_id=None):
        if self._closed or self.window._closing:
            return
        selected = report_id or self.history.currentData()
        rows = self.window.service.list_verifications(self.document_id)
        self.history.blockSignals(True)
        self.history.clear()
        for item in rows:
            name = {'github': 'GitHub 基础核验', 'research': '科研进展', 'code': '深入代码核验'}[item['kind']]
            self.history.addItem(name + ' · ' + STATUS_NAMES[item['status']] + ' · ' + item['created_at'], item['id'])
        self.history.setCurrentIndex(max(0, self.history.findData(selected)))
        self.history.blockSignals(False)
        self.show_report()

    def show_report(self):
        identifier = self.history.currentData()
        self.export_button.setEnabled(bool(identifier))
        if not identifier:
            self.browser.setPlainText('暂无外部核验。填写 GitHub 仓库后联网核验，或导入 agent 整理的科研进展报告。')
            return
        result = self.window.guard(lambda: self.window.service.verification_report(self.document_id, identifier))
        if result:
            self.browser.setHtml(render_report(result))

    def _run(self, work, message):
        self.run(work, self.ready, message)

    def update_controls(self):
        if not hasattr(self, 'check_button'):
            return
        enabled = not self._closed and not self._busy and not self.window._closing and self.window.io_worker is None
        self.check_button.setEnabled(enabled)
        self.import_button.setEnabled(enabled)

    def ready(self, result):
        if not self._closed:
            self.refresh(result['id'])

    def start_check(self):
        url = self.repository.text().strip()
        self._run(lambda: self.window.service.verify_github(self.document_id, url, authorized=True), '正在核验公开 GitHub 仓库…')

    def import_research(self):
        if self.window.io_worker is not None or self.window._closing:
            return
        path, _ = QFileDialog.getOpenFileName(self, '导入研究进展报告', '', 'JSON (*.json)')
        if path:
            self._run(lambda: self.window.service.import_research_report(self.document_id, path), '正在校验并归档研究进展报告…')

    def export_report(self):
        identifier = self.history.currentData()
        if not identifier:
            return
        path, _ = QFileDialog.getSaveFileName(self, '导出外部核验报告', 'verification.json', 'JSON (*.json)')
        if path:
            self.window.guard(lambda: self.window.service.export_verification(self.document_id, identifier, path))

    def closeEvent(self, event):
        # Hiding does not discard in-flight callbacks or its fixed document identity.
        # 关闭只隐藏，进行中的结果仍按打开时的文献身份归档。
        super().closeEvent(event)
