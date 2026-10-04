# SPDX-License-Identifier: AGPL-3.0-only
"""Isolated HTML translation; credentials arrive only through bounded stdin.

隔离 HTML 翻译进程，仅通过有上限的 stdin 接收凭据。
"""
import importlib
import json
from pathlib import Path
import sys
import time
import types

MAX_REQUEST = 1024 * 1024
MAX_RESULT = 100 * 1024 * 1024


def main():
    """Load trusted adjacent helpers and emit one bounded protocol response.

    加载可信相邻组件，只输出一个受限协议结果，不输出原始异常。
    """
    try:
        directory = Path(__file__).resolve().parent
        helpers = directory / 'html_helpers'
        if not helpers.is_dir():
            helpers = directory.parent / 'polyscholar'
        # A private package namespace avoids application/Qt initialization.
        # 私有包命名空间避免启动桌面应用或 Qt。
        package = types.ModuleType('polyscholar_html_worker_helpers')
        package.__path__ = [str(helpers)]
        sys.modules[package.__name__] = package
        module = importlib.import_module(package.__name__ + '.html_translate')
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise ValueError()
        request = json.loads(raw)
        timeout = request['timeout']
        if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
            raise ValueError()
        html, total, translated = module.translate_paper(
            request['identifier'], request['endpoint'], request['model'], request['token'],
            timeout=timeout, deadline=time.monotonic() + timeout)
        if translated == 0:
            raise ValueError()
        result = {'ok': True, 'html': html, 'total': total, 'translated': translated}
        output = json.dumps(result, ensure_ascii=True).encode('ascii')
        if len(output) > MAX_RESULT:
            raise ValueError()
    except Exception:
        output = b'{"ok":false}'
    sys.stdout.buffer.write(output)


if __name__ == '__main__':
    main()
