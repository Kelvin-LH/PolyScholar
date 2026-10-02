# SPDX-License-Identifier: AGPL-3.0-only
"""Isolated bounded model HTTP exchange. Credentials arrive only on stdin."""
import importlib.util
import json
from pathlib import Path
import sys


def main():
    try:
        directory = Path(__file__).resolve().parent
        helper = directory / 'summary_model.py'
        if not helper.is_file():
            helper = directory.parent / 'polyscholar/summary_model.py'
        # Controlled package resource, never a user-provided import path.
        spec = importlib.util.spec_from_file_location('polyscholar_summary_protocol', helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        request = module._json(raw)
        claims, usage = module._http_summary(request['endpoint'], request['token'], request['model'],
            request['language'], request['blocks'], request['timeout'])
        result = {'ok': True, 'claims': claims, 'usage': usage}
    except Exception:
        result = {'ok': False}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode('ascii'))


if __name__ == '__main__':
    main()
