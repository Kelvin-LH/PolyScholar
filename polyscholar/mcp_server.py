# SPDX-License-Identifier: AGPL-3.0-only
"""PolyScholar MCP server (stdio): a thin shell over polyscholar-cli.

设计:server 不含业务逻辑,只暴露两个工具——
1. cli_docs():返回仓库根目录 cli.md(命令契约,agent 自行学习用法);
2. cli_run(command):执行一条 polyscholar-cli 命令(白名单子命令、参数列表
   直传、不经 shell、超时与输出上限),返回退出码与输出。

软件用途写在 server instructions;GUI 与本 server 共享单实例锁,冲突时
命令失败并原样返回"此文献库已在运行"。
"""
import os
import shlex
import subprocess
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_MD = REPO_ROOT / 'cli.md'
COMMANDS = {'add', 'list', 'show', 'update', 'remove', 'text', 'parse', 'collection',
            'translate', 'jobs', 'export-translation', 'score',
            'rubric', 'status', 'search'}
MAX_OUTPUT = 100 * 1024
RUN_TIMEOUT = 1800

INSTRUCTIONS = (
    'PolyScholar:本地优先的学术文献保存库(AI 文献管理工作台)。'
    '能力:导入/管理论文(PDF 与 arXiv)、集合与标签、arXiv 论文逐段翻译为中文、'
    '解析全文文本、以及论文评分与置信度维度(依据 rubrics/ 打分文档)。'
    '先调用 cli_docs 阅读命令契约,再用 cli_run 执行;命令输出一律加 --json 便于解析。'
    '节省 token 的要点:rubric show 直接取打分文档;search 定位原文(show --text --pages 精确取页),'
    '避免整篇重读;score aggregate 程序化三盲评合卷(中位数/代表报告/复核指令),'
    'score validate 写库前校验,score import-report/reports 归档与读回评委原文;'
    'status 在不取锁的情况下报告库状态;add --arxiv 支持多篇批量。'
    '联网命令(add --arxiv)遵循 HTTPS_PROXY/HTTP_PROXY 环境变量;'
    '通过 MCP 使用时,在 MCP 配置的 env 中设置后重启客户端生效。'
)

def _cli_md_path():
    """Locate cli.md: env override -> source checkout -> frozen bundle resources."""
    override = os.environ.get('POLYSCHOLAR_CLI_MD')
    if override:
        return Path(override)
    if CLI_MD.is_file():
        return CLI_MD
    if getattr(sys, 'frozen', False) and getattr(sys, '_MEIPASS', None):
        return Path(sys._MEIPASS) / 'cli.md'
    return CLI_MD

def cli_docs() -> str:
    """返回命令行工具契约文档 cli.md 的全文。先读它,再用 cli_run 执行命令。"""
    path = _cli_md_path()
    if not path.is_file():
        return 'cli.md 不存在(预期位置:' + str(path) + ')。'
    text = path.read_text(encoding='utf-8')
    return text[:MAX_OUTPUT]

def cli_run(command: str) -> str:
    """执行一条 polyscholar-cli 命令并返回退出码与输出。

    command 为完整命令行字符串,例如:
      "list --json"  或  "score set <doc> --kind paper --score 87 --detail-file /tmp/r.json"
    仅允许 cli.md 中列出的子命令;参数按 shell 规则切分后以参数列表直传,不经 shell。
    """
    argv = shlex.split(command)
    if argv and argv[0] in ('polyscholar-cli', 'polyscholar'):
        argv = argv[1:]
    if not argv or argv[0] not in COMMANDS:
        allowed = ', '.join(sorted(COMMANDS))
        return '拒绝执行:子命令必须是以下之一:\n' + allowed + '\n(完整说明见 cli_docs)'
    data_dir = os.environ.get('POLYSCHOLAR_DATA_DIR')
    prefix = [sys.executable, '-m', 'polyscholar.cli']
    if data_dir:
        prefix += ['--data-dir', data_dir]
    try:
        completed = subprocess.run(prefix + argv, capture_output=True,
                                   timeout=RUN_TIMEOUT, shell=False)
    except subprocess.TimeoutExpired:
        return ('命令超时(>%d 秒)被终止。长任务请用 "translate <doc> --no-wait --json" '
                '创建后用 "jobs --json" 轮询。' % RUN_TIMEOUT)
    stdout = completed.stdout.decode('utf-8', errors='replace')
    stderr = completed.stderr.decode('utf-8', errors='replace')
    if len(stdout) > MAX_OUTPUT:
        stdout = stdout[:MAX_OUTPUT] + '\n…[输出截断,可用 --json 与更细的过滤缩小结果]'
    if len(stderr) > MAX_OUTPUT // 2:
        stderr = stderr[:MAX_OUTPUT // 2] + '\n…[stderr 截断]'
    return 'exit=%d\n--- stdout ---\n%s--- stderr ---\n%s' % (completed.returncode, stdout, stderr)

def create_server():
    server = MCPServer('PolyScholar', instructions=INSTRUCTIONS)
    server.tool()(cli_docs)
    server.tool()(cli_run)
    return server

def main():
    create_server().run()

if __name__ == '__main__':
    main()
