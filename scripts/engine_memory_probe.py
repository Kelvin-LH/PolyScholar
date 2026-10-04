# SPDX-License-Identifier: AGPL-3.0-only
"""Disposable fixture for pinned upstream configuration boundaries.

夹具隔离缓存发现并阻断网络，不替换上游解析器或翻译器类。
"""
from contextlib import redirect_stderr, redirect_stdout
import importlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import socket
from unittest.mock import patch

COMPONENTS = (
    'engine_entry', 'engines', 'job_worker', 'managed_process',
    'process_gate', 'windows_job',
)


def assert_component_origins(root):
    """Every integration import must come from the explicitly selected package.

    所有集成模块都必须来自指定包目录；不接受源码回退或模块替身。
    """
    root = Path(root).resolve(strict=True)
    for name in ('integrations', *('integrations.' + item for item in COMPONENTS)):
        module = sys.modules.get(name)
        filename = '__init__.py' if name == 'integrations' else name.rsplit('.', 1)[1] + '.py'
        expected = root / filename
        if (module is None or getattr(module, '__file__', None) is None
                or Path(module.__file__).resolve(strict=True) != expected
                or getattr(module, '__spec__', None) is None
                or Path(module.__spec__.origin).resolve(strict=True) != expected):
            raise RuntimeError('Integration component origin does not match')
    package = sys.modules['integrations']
    if ([Path(item).resolve() for item in package.__path__] != [root]
            or [Path(item).resolve() for item in package.__spec__.submodule_search_locations]
            != [root]):
        raise RuntimeError('Integration package search path does not match')
    entry = sys.modules['integrations.engine_entry']
    engines = sys.modules['integrations.engines']
    worker = sys.modules['integrations.job_worker']
    if entry.engines is not engines or entry.job_worker is not worker or worker.engines is not engines:
        raise RuntimeError('Integration components use foreign dependencies')


def load_integrations(directory):
    directory = Path(directory).absolute()
    if directory.is_symlink():
        raise RuntimeError('Integration directory must not redirect outside the package')
    root = directory.resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError('Integration directory is missing')
    for filename in ('__init__.py', *(item + '.py' for item in COMPONENTS)):
        path = root / filename
        if not path.is_file() or path.resolve(strict=True) != path:
            raise RuntimeError('Integration component is missing or points outside the package')
    if any(name == 'integrations' or name.startswith('integrations.') for name in sys.modules):
        raise RuntimeError('Integration components were loaded before the origin check')
    spec = importlib.util.spec_from_file_location(
        'integrations', root / '__init__.py', submodule_search_locations=[str(root)])
    if spec is None or spec.loader is None:
        raise RuntimeError('Integration package cannot be loaded')
    package = importlib.util.module_from_spec(spec)
    sys.modules['integrations'] = package
    spec.loader.exec_module(package)
    for name in COMPONENTS:
        importlib.import_module('integrations.' + name)
    assert_component_origins(root)
    return sys.modules['integrations.engine_entry'], sys.modules['integrations.engines']


class MemoryConfigurationProbe:
    def __init__(self, engine, root, integrations):
        if engine not in ('babeldoc', 'pdfmathtranslate'):
            raise ValueError('Unsupported probe engine')
        # Loading selected package components must not add bytecode to the bundle.
        # 验证夹具不能因导入而向待验收安装包写入字节码。
        sys.dont_write_bytecode = True
        self.integrations = Path(integrations)
        self.engine_entry, self.engines = load_integrations(self.integrations)
        self.inspections = 0
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
            self.inspections += 1
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
            self.inspections += 1
            return 0
        prepared.upstream.main = inspect

    def run(self):
        engines, engine_entry = self.engines, self.engine_entry
        req = engines.Request(
            self.engine, Path(sys.executable), self.source, self.output,
            'http://127.0.0.1:9999/v1', 'synthetic-model', pages='1-2',
            allow_document_upload=True, allow_asset_download=True,
        )
        payload = engines.request_input(req, self.key)
        self.output.mkdir()
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
            original_prepare = engine_entry.prepare

            def prepare_and_inspect(actual_req, actual_key):
                if actual_req != req or actual_key != self.key:
                    raise RuntimeError('Stdin entry changed the selected request')
                prepared = original_prepare(actual_req, actual_key)
                if self.engine == 'babeldoc':
                    self.inspect_babeldoc(prepared)
                else:
                    self.inspect_pdfmathtranslate(prepared)
                return prepared

            # Exercise the actual stdin parser and pinned checks. Only replace the
            # final translation call, retaining the real parser and client.
            # 走真实 main/stdin 校验；只替换最终翻译调用，不称翻译成功。
            with io.TextIOWrapper(io.BytesIO(payload), encoding='utf-8') as incoming, \
                    patch.object(sys, 'stdin', incoming), \
                    patch.object(sys, 'argv', [str(engine_entry.__file__)]), \
                    patch.object(engine_entry, 'prepare', side_effect=prepare_and_inspect):
                if engine_entry.main() != 0 or self.inspections != 1:
                    raise RuntimeError('Offline inspection did not finish')
        assert_component_origins(self.integrations)
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
                if len(sys.argv) != 4:
                    raise ValueError('Invalid fixture invocation')
                MemoryConfigurationProbe(sys.argv[1], sys.argv[2], sys.argv[3]).run()
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
