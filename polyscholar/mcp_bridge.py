# SPDX-License-Identifier: AGPL-3.0-only
"""Configured stdio-to-CLI bridge / 按配置连接 stdio 与共用 CLI。"""
from dataclasses import dataclass
import json
import os
import argparse
from pathlib import Path
import subprocess
import sys

from integrations.engines import limited_environment
from integrations.managed_process import ProcessCleanupError, run_captured

COMMANDS = frozenset({'add', 'list', 'show', 'update', 'remove', 'text', 'parse',
    'collection', 'translate', 'jobs', 'export-translation', 'score', 'rubric', 'status', 'search'})
MAX_ARGUMENTS = 128
MAX_ARGUMENT_LENGTH = 8192
MAX_ARGUMENT_BYTES = 32 * 1024


class CommandRejected(ValueError):
    """A stable policy failure without echoing private input / 不回显私人输入的策略拒绝。"""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class CommandPolicy:
    """Classify side effects conservatively before process launch.

    进程启动前保守分类副作用；白名单不是文件系统沙箱。
    """
    def validate(self, argv, *, read_only=False):
        if (not isinstance(argv, list) or not 1 <= len(argv) <= MAX_ARGUMENTS
                or any(not isinstance(value, str) or not value or len(value) > MAX_ARGUMENT_LENGTH
                       or any(ord(character) < 32 for character in value) for value in argv)
):
            raise CommandRejected('invalid_arguments', '拒绝执行：参数为空、过长或含控制字符。')
        try:
            byte_count = sum(len(value.encode('utf-8')) for value in argv)
        except UnicodeEncodeError:
            raise CommandRejected('invalid_arguments', '拒绝执行：参数含无效 Unicode 字符。') from None
        if byte_count > MAX_ARGUMENT_BYTES:
            raise CommandRejected('invalid_arguments', '拒绝执行：参数总长度超过上限。')
        if argv[0] not in COMMANDS:
            raise CommandRejected('unsupported_command', '拒绝执行：子命令不在命令契约中。')
        for value in argv:
            option = value.split('=', 1)[0]
            if option.startswith('--') and '--data-dir'.startswith(option):
                raise CommandRejected('library_override', '拒绝执行：文献库由 MCP 配置固定，不能通过命令更改。')
        self._validate_cli_arguments(argv)
        operation = self.classify(argv)
        if read_only and operation != 'read':
            raise CommandRejected('read_only', '拒绝执行：当前 MCP 配置为只读模式，不允许写入或联网命令。')
        return operation

    @staticmethod
    def _validate_cli_arguments(argv):
        """Use the real CLI parser without protocol stdout/stderr side effects.

        复用实际 CLI 解析器，禁止帮助输出污染 stdio 协议。
        """
        from .cli import build_parser
        if '--help' in argv or '-h' in argv:
            raise CommandRejected('invalid_arguments', '请通过 cli_docs 读取命令说明。')
        parser = build_parser()

        def reject(message):
            raise CommandRejected('invalid_arguments', '拒绝执行：命令参数不符合契约，请读取 cli_docs。')

        def protect(current):
            # Child parsers use their own error method / 子解析器有独立的错误入口。
            current.error = reject
            for action in current._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for child in action.choices.values():
                        protect(child)
        protect(parser)
        parser.parse_args(argv)

    def classify(self, argv):
        command = argv[0]
        if command == 'translate' or (command == 'add' and any('--arxiv'.startswith(a.split('=', 1)[0]) for a in argv[1:] if a.startswith('--'))):
            return 'network'
        if command in {'list', 'show', 'text', 'jobs', 'rubric', 'status', 'search'}:
            return 'read'
        if command == 'collection' and len(argv) > 1 and argv[1] == 'list':
            return 'read'
        if command == 'score' and len(argv) > 1 and argv[1] in {'show', 'reports', 'validate'}:
            return 'read'
        return 'write'


@dataclass(frozen=True)
class BridgeConfiguration:
    """Server-lifetime library selection / 服务生命周期内固定的文献库选择。"""
    data_dir: Path
    read_only: bool = False
    timeout: float = 1800
    max_output: int = 100 * 1024

    @classmethod
    def from_environment(cls, *, timeout=1800, max_output=100 * 1024):
        from .service import app_data_dir
        directory = os.environ.get('POLYSCHOLAR_DATA_DIR')
        return cls(Path(directory).expanduser().resolve() if directory else app_data_dir().resolve(),
            os.environ.get('POLYSCHOLAR_MCP_READ_ONLY', '').lower() in {'1', 'true', 'yes'},
            timeout, max_output)


class CliBridge:
    """Run shared business operations without bypassing library locks.

    经共用业务入口运行，不绕过本地库锁和事务。
    """
    def __init__(self, configuration, *, policy=None):
        self.configuration = configuration
        self.policy = policy or CommandPolicy()

    @staticmethod
    def result(*, success=False, error_code=None, exit_code=None, stdout='', stderr='',
               truncated=False, operation=None):
        return dict(success=success, error_code=error_code, exit_code=exit_code,
                    stdout=stdout, stderr=stderr, truncated=truncated, operation=operation)

    def execute(self, argv):
        if getattr(sys, 'frozen', False):
            return self.result(error_code='unsupported_runtime', stderr='桌面安装包尚未提供已验收的 MCP 命令组件，请使用独立 CLI/MCP 安装。')
        try:
            operation = self.policy.validate(argv, read_only=self.configuration.read_only)
        except CommandRejected as error:
            return self.result(error_code=error.code, stderr=str(error))
        command = [sys.executable, '-m', 'polyscholar.cli', '--data-dir',
                   str(self.configuration.data_dir), *argv]
        return self.run(command, operation=operation)

    def run(self, command, *, operation=None):
        environment = limited_environment()
        # Protocol bytes must not depend on Windows locale or caller settings.
        # 协议字节不依赖 Windows 区域编码或调用方环境设置。
        environment['PYTHONIOENCODING'] = 'utf-8'
        environment['PYTHONUTF8'] = '1'
        for name in ('HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY', 'https_proxy', 'http_proxy',
                     'no_proxy', 'POLYSCHOLAR_RUBRICS_DIR', 'POLYSCHOLAR_CLI_MD'):
            if name in os.environ:
                environment[name] = os.environ[name]
        try:
            output, errors, exit_code, truncated = run_captured(command, env=environment,
                timeout=self.configuration.timeout, max_bytes=self.configuration.max_output)
        except subprocess.TimeoutExpired:
            return self.result(error_code='timeout', stderr='命令超时，进程树已终止；请减少任务范围或使用桌面应用。', operation=operation)
        except ProcessCleanupError:
            return self.result(error_code='cleanup_failed', stderr='无法确认命令进程已完全退出，请停止后续操作并检查应用状态。', operation=operation)
        except OSError:
            return self.result(error_code='launch_failed', stderr='无法启动内置命令组件，请检查安装。', operation=operation)
        stdout = output.decode('utf-8', errors='replace')
        stderr = errors.decode('utf-8', errors='replace')
        code = None
        if exit_code:
            code = {3: 'network_failed', 130: 'cancelled'}.get(exit_code, 'command_failed')
            if exit_code == 2:
                code = ('library_busy' if stderr.strip() == '此文献库已在运行，请切换到已打开的 PolyScholar。'
                        else 'invalid_arguments')
            # CLI failures may contain provider/private paths: return a safe action.
            # CLI 失败可能包含供应商或私人路径信息，返回可操作的固定提示。
            stderr = {'library_busy': '此文献库已在运行，请先关闭使用该库的桌面应用，再重试。',
                      'network_failed': '请求失败，请检查网络与代理设置。',
                      'cancelled': '命令已取消。'}.get(code, '命令未完成，请检查参数及本地配置；用 cli_docs 查看契约。')
        result = self.result(success=exit_code == 0, error_code=code, exit_code=exit_code,
            stdout=stdout, stderr=stderr, truncated=truncated, operation=operation)
        if exit_code == 0 and not truncated:
            try:
                result['data'] = json.loads(stdout)
            except ValueError:
                pass
        return result

    def library_status(self):
        result = self.execute(['status', '--json'])
        result['read_only'] = self.configuration.read_only
        if result['success'] and not result['truncated']:
            try:
                status = json.loads(result['stdout'])
                if not isinstance(status, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                return {**result, 'success': False, 'error_code': 'invalid_status'}
            result['status'] = status
            result['next_action'] = ('请先关闭使用该文献库的桌面应用，再执行库命令。'
                                     if status.get('lock') == 'busy' else '可执行允许范围内的文献库命令。')
        return result

    @staticmethod
    def legacy_text(result):
        """Keep legacy stdout contract / 保留历史文本输出契约。"""
        if result['exit_code'] is None:
            return result['stderr']
        suffix = '\n…[输出截断，请缩小查询范围]' if result['truncated'] else ''
        return 'exit=%d\n--- stdout ---\n%s%s--- stderr ---\n%s' % (
            result['exit_code'], result['stdout'], suffix, result['stderr'])
