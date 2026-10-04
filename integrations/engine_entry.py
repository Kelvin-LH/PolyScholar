# SPDX-License-Identifier: AGPL-3.0-only
"""Trusted, stdin-only configuration for the pinned upstream engines.

可信入口只从 stdin 接收配置；保留上游翻译实现，密钥不写配置文件。
"""
from contextlib import redirect_stderr, redirect_stdout
import copy
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sys

if __package__:
    from . import engines, job_worker
else:
    # Isolated Python loads only the trusted adjacent integration components.
    # 隔离 Python 只加载同目录可信集成组件。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import engines
    import job_worker


def _arguments(req):
    source, output = str(req.source.resolve()), str(req.output.resolve())
    if req.engine == 'babeldoc':
        arguments = [
            '--files', source, '--openai',
            '--lang-in', req.source_language,
            '--lang-out', req.target_language,
            '--output', output,
            '--watermark-output-mode', 'no_watermark',
        ]
    else:
        arguments = [
            source, '--service', 'openai',
            '--lang-in', req.source_language,
            '--lang-out', req.target_language,
            '--output', output, '--thread', '1',
        ]
    if req.pages:
        arguments.extend(['--pages', req.pages])
    return arguments


def _install_pdf_memory_config(configuration):
    """Install the original config API with a memory-only persistence boundary.

先装入原配置 API 的内存策略，再导入任何 pdf2zh 高层模块。
"""
    if 'pdf2zh' in sys.modules or 'pdf2zh.config' in sys.modules:
        raise RuntimeError('PDFMathTranslate must be prepared before package import')
    package = importlib.util.find_spec('pdf2zh')
    if package is None or not package.origin:
        raise RuntimeError('Pinned PDFMathTranslate package is unavailable')
    source = Path(package.origin).parent / 'config.py'
    spec = importlib.util.spec_from_file_location('pdf2zh.config', source)
    if spec is None or spec.loader is None:
        raise RuntimeError('Pinned configuration component is unavailable')
    module = importlib.util.module_from_spec(spec)
    sys.modules['pdf2zh.config'] = module
    try:
        spec.loader.exec_module(module)
        base = module.ConfigManager

        class MemoryConfigManager(base):
            def __init__(self):
                self._initialized = True
                self._config_data = copy.deepcopy(configuration)
                self._config_path = None

            def _save_config(self):
                # Original get/set methods still operate; no key reaches disk.
                # 复用原 get/set 方法，仅把持久化边界替换为内存。
                return None

            def _load_config(self):
                raise RuntimeError('Disk configuration is disabled')

            def _ensure_config_exists(self, isInit=True):
                raise RuntimeError('Disk configuration is disabled')

            @classmethod
            def custome_config(cls, file_path):
                raise RuntimeError('Disk configuration is disabled')

            @classmethod
            def remove(cls):
                raise RuntimeError('Disk configuration is disabled')

        MemoryConfigManager._instance = MemoryConfigManager()
        module.ConfigManager = MemoryConfigManager
    except BaseException:
        sys.modules.pop('pdf2zh.config', None)
        raise
    return module


def load_upstream(req, api_key):
    package, expected = engines.VERSIONS[req.engine]
    if importlib.metadata.version(package) != expected:
        raise RuntimeError('Pinned upstream version does not match')
    if req.engine == 'babeldoc':
        return importlib.import_module('babeldoc.main')
    values = {
        'OPENAI_BASE_URL': req.endpoint,
        'OPENAI_API_KEY': api_key,
        'OPENAI_MODEL': req.model,
    }
    # Upstream set_envs lets provider environment override configuration.
    # 上游会以环境覆盖配置；拒绝该入口继承任何此类配置，不读取其值。
    if any(name in os.environ for name in values):
        raise RuntimeError('Provider environment is not permitted')
    _install_pdf_memory_config({
        'translators': [{'name': 'openai', 'envs': values}],
    })
    return importlib.import_module('pdf2zh.pdf2zh')


class PreparedEngine:
    """One process, one prepared upstream invocation / 每个进程只执行一次上游调用。"""
    def __init__(self, req, api_key):
        engines.validate(req, output_ready=True)
        if not isinstance(api_key, str) or not api_key:
            raise ValueError('An API key is required')
        self.engine = req.engine
        self.arguments = _arguments(req)
        self.upstream = load_upstream(req, api_key)
        self._used = False
        self._parser = None
        if self.engine == 'babeldoc':
            self._parser = self.upstream.create_parser()
            contents = '[babeldoc]\n' + '\n'.join(
                name + ' = ' + json.dumps(value, ensure_ascii=False)
                for name, value in (
                    ('openai-model', req.model),
                    ('openai-base-url', req.endpoint),
                    ('openai-api-key', api_key),
                )
            ) + '\n'
            namespace = self._parser.parse_args(
                self.arguments, config_file_contents=contents, env_vars={})
            # Upstream main() asks this same real parser for its prepared result.
            # 上游 main() 使用同一真实解析器的结果，不再读取系统 argv 或环境。
            self._parser.parse_args = lambda: namespace

    def run(self):
        if self._used:
            raise RuntimeError('Prepared engine was already invoked')
        self._used = True
        if self.engine == 'babeldoc':
            original = self.upstream.create_parser
            self.upstream.create_parser = lambda: self._parser
            try:
                result = self.upstream.cli()
            finally:
                self.upstream.create_parser = original
        else:
            result = self.upstream.main(self.arguments)
        if result is None and self.engine == 'babeldoc':
            return 0
        if type(result) is not int or result != 0:
            raise RuntimeError('Upstream entry did not complete successfully')
        return 0


def prepare(req, api_key):
    return PreparedEngine(req, api_key)


def run(req, api_key):
    return prepare(req, api_key).run()


def main():
    # Suppress parser/import diagnostics before any third-party code is loaded.
    # 在加载第三方代码前抑制原始诊断，协议内容及异常不输出。
    with open(os.devnull, 'w') as quiet:
        with redirect_stdout(quiet), redirect_stderr(quiet):
            try:
                os.dup2(quiet.fileno(), 1)
                os.dup2(quiet.fileno(), 2)
                if len(sys.argv) != 1:
                    return 1
                raw = sys.stdin.buffer.readline(job_worker.MAX_INPUT_BYTES + 1)
                _, req, api_key = job_worker.parse_request(raw, output_ready=True)
                engines.check_version(req, engines.limited_environment())
                return run(req, api_key)
            except BaseException:
                return 1


if __name__ == '__main__':
    import multiprocessing

    multiprocessing.freeze_support()
    # Match the upstream CLI's platform-specific multiprocessing initialization.
    # 与上游 CLI 的平台相关多进程初始化保持一致。
    multiprocessing.set_start_method(
        'spawn' if sys.platform in ('darwin', 'win32') else 'forkserver')
    raise SystemExit(main())
