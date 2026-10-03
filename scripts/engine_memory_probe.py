# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable fixture for pinned upstream configuration boundaries.

夹具隔离缓存发现并阻断网络，不替换上游解析器或翻译器类。
"""
from contextlib import redirect_stderr, redirect_stdout
import os
from pathlib import Path
import sys
import socket
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations import engine_entry, engines


class MemoryConfigurationProbe:
    def __init__(self, engine, root):
        self.engine = engine
        self.root = Path(root) / engine
        self.root.mkdir()
        self.home = self.root / 'synthetic-home'
        self.home.mkdir()
        self.key = 'synthetic-memory-only-test-token'
        self.network_attempts = []
        self.sentinel = self.home / '.config/PDFMathTranslate/config.json'
        self.sentinel.parent.mkdir(parents=True)
        self.original = b'{"sentinel":"synthetic-user-configuration"}\n'
        self.sentinel.write_bytes(self.original)
        self.source = self.root / 'synthetic.pdf'
        self.source.write_bytes(b'%PDF-1.4\n% Parser-only synthetic fixture\n')
        self.output = self.root / 'output'
        self.output.mkdir()

    def audit(self, event, args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'socket.bind'):
            self.network_attempts.append(event)
            raise RuntimeError('Network is disabled in the offline probe')
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0]))
            if path == self.sentinel and not getattr(self, 'finished', False):
                raise RuntimeError('Upstream attempted to read or write user configuration')

    def assert_client(self, translator):
        try:
            if translator.client.api_key != self.key:
                raise RuntimeError('Synthetic credential did not reach the real client')
            if self.key in translator.cache.translate_engine_params:
                raise RuntimeError('Credential reached translation cache parameters')
        finally:
            translator.client.close()

    def inspect_babeldoc(self, prepared):
        def inspect():
            namespace = prepared.upstream.create_parser().parse_args()
            if (namespace.openai_api_key != self.key
                    or namespace.openai_model != 'synthetic-model'
                    or namespace.openai_base_url != 'http://127.0.0.1:9999/v1'
                    or namespace.lang_in != 'en' or namespace.lang_out != 'zh'
                    or namespace.pages != '1-2'):
                raise RuntimeError('Real BabelDOC parser produced incorrect configuration')
            translator = prepared.upstream.OpenAITranslator(
                lang_in=namespace.lang_in, lang_out=namespace.lang_out,
                model=namespace.openai_model, base_url=namespace.openai_base_url,
                api_key=namespace.openai_api_key,
            )
            self.assert_client(translator)
        prepared.upstream.cli = inspect

    def inspect_pdfmathtranslate(self, prepared):
        def inspect(arguments):
            namespace = prepared.upstream.parse_args(arguments)
            if (namespace.service != 'openai' or namespace.lang_in != 'en'
                    or namespace.lang_out != 'zh' or namespace.pages != [0, 1]
                    or namespace.raw_pages != '1-2'):
                raise RuntimeError('Real PDFMathTranslate parser produced incorrect arguments')
            from pdf2zh.config import ConfigManager
            from pdf2zh.translator import OpenAITranslator

            manager = ConfigManager.get_instance()
            saved = []
            original_save = manager._save_config

            def save():
                saved.append(True)
                return original_save()

            manager._save_config = save
            translator = OpenAITranslator('en', 'zh', None, envs={})
            self.assert_client(translator)
            ConfigManager.set('synthetic-probe-default', 'memory-only')
            if not saved or ConfigManager.get('synthetic-probe-default') != 'memory-only':
                raise RuntimeError('Configuration persistence boundary was not exercised')
            return 0
        prepared.upstream.main = inspect

    def run(self):
        req = engines.Request(
            self.engine, Path(sys.executable), self.source, self.output,
            'http://127.0.0.1:9999/v1', 'synthetic-model', pages='1-2',
            allow_document_upload=True, allow_asset_download=True,
        )
        engines.check_version(req, engines.limited_environment())
        original_environment = {
            name: os.environ.get(name) for name in ('HOME', 'USERPROFILE')
        }
        original_expanduser = os.path.expanduser

        def isolated_expanduser(path):
            text = os.fsdecode(path)
            if text == '~' or text.startswith('~/') or text.startswith('~\\'):
                value = str(self.home) + text[1:]
                return os.fsencode(value) if isinstance(path, bytes) else value
            if text.startswith('~'):
                raise RuntimeError('Foreign home discovery is disabled')
            return original_expanduser(path)

        sys.addaudithook(self.audit)
        # Only this fixture changes cache discovery, never HOME/USERPROFILE.
        # 仅夹具替换非凭据缓存路径发现，绝不改变 HOME/USERPROFILE。
        # CPython probes IPv6 by binding on import; refuse the probe before OS IO.
        # CPython 导入时以 bind 探测 IPv6；夹具在系统调用前拒绝此能力探测。
        def refuse_passive_bind(*args, **kwargs):
            raise OSError('Passive bind is disabled in the offline fixture')

        with patch.object(Path, 'home', return_value=self.home), \
                patch('os.path.expanduser', side_effect=isolated_expanduser), \
                patch.object(socket.socket, 'bind', side_effect=refuse_passive_bind):
            prepared = engine_entry.prepare(req, self.key)
            if self.engine == 'babeldoc':
                self.inspect_babeldoc(prepared)
            else:
                self.inspect_pdfmathtranslate(prepared)
            # Replace the final translation call, retaining real parser and client.
            # 只替换最终翻译调用，保留真实解析器和客户端，不称翻译成功。
            if prepared.run() != 0:
                raise RuntimeError('Offline inspection did not finish')
        self.finished = True
        if self.sentinel.read_bytes() != self.original or self.network_attempts:
            raise RuntimeError('Probe touched configuration or attempted network')
        if any(os.environ.get(name) != value
               for name, value in original_environment.items()):
            raise RuntimeError('Probe changed an OS home environment variable')
        for path in self.root.rglob('*'):
            if path.is_file() and self.key.encode() in path.read_bytes():
                raise RuntimeError('Synthetic credential reached a local file')


def main():
    saved = [os.dup(1), os.dup(2)]
    outcome = 1
    try:
        with open(os.devnull, 'w') as quiet, \
                redirect_stdout(quiet), redirect_stderr(quiet):
            os.dup2(quiet.fileno(), 1)
            os.dup2(quiet.fileno(), 2)
            try:
                if len(sys.argv) != 3:
                    raise ValueError('Invalid fixture invocation')
                MemoryConfigurationProbe(sys.argv[1], sys.argv[2]).run()
                outcome = 0
            except BaseException:
                outcome = 1
    finally:
        for number, original in zip((1, 2), saved):
            os.dup2(original, number)
            os.close(original)
    if outcome == 0:
        print('Pinned engine memory configuration checks passed', flush=True)
    return outcome


if __name__ == '__main__':
    raise SystemExit(main())
