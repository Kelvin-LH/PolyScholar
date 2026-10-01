# SPDX-License-Identifier: AGPL-3.0-only
"""Desktop sidecar: one bounded JSON line on stdin, status NDJSON on stdout.

No credentials in argv, no engine logs on stdout, no network of its own.
The engine may issue authorized API and asset-download requests.
"""
import json
from pathlib import Path
import re
import signal
import subprocess
import sys

if __package__:
    from . import engines
else:
    # -I deliberately omits the script directory; load only the adjacent trusted adapter.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import engines

MAX_INPUT_BYTES = 1024 * 1024
FIELDS = {'protocol_version', 'job_id', 'engine', 'python', 'source', 'output',
          'endpoint', 'model', 'api_key', 'source_language', 'target_language',
          'pages', 'timeout', 'allow_document_upload', 'allow_asset_download'}
MESSAGES = {
    'invalid_request': '任务参数或授权无效，请检查配置。',
    'engine_unavailable': '指定环境未安装匹配版本的翻译引擎。',
    'engine_failed': '翻译引擎失败；原始输出已抑制，请检查引擎配置。',
    'timeout': '任务超时并已终止；已发送的 API 请求仍可能收费。',
    'cancelled': '任务已取消；已发送的 API 请求仍可能收费。',
    'source_changed': '源文件在处理期间发生变化。',
    'invalid_output': '引擎未产生具有有效 PDF 文件头的结果。',
    'io_error': '本机文件或进程操作失败。',
    'internal_error': '任务未能完成；私有诊断信息未输出。',
}

def object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate field')
        result[key] = value
    return result

def parse_request(raw):
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError('Request too large')
    body = json.loads(raw, object_pairs_hook=object_without_duplicates,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid number')))
    if not isinstance(body, dict) or set(body) - FIELDS:
        raise ValueError('Unknown fields')
    if type(body.get('protocol_version')) is not int or body['protocol_version'] != 1:
        raise ValueError('Unsupported protocol')
    job_id = body.get('job_id')
    if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', job_id):
        raise ValueError('Invalid job identifier')
    for field in ('engine', 'python', 'source', 'output', 'endpoint', 'model', 'api_key'):
        value = body.get(field)
        if not isinstance(value, str) or not value or len(value) > 16384 or any(ord(c) < 32 for c in value):
            raise ValueError('Invalid required string')
    for field in ('python', 'source', 'output'):
        if not Path(body[field]).is_absolute():
            raise ValueError('Absolute path required')
    for field, default in [('source_language', 'en'), ('target_language', 'zh'), ('pages', '')]:
        body.setdefault(field, default)
        if not isinstance(body[field], str) or len(body[field]) > 4096:
            raise ValueError('Invalid optional string')
    body.setdefault('timeout', 600)
    if type(body['timeout']) is not int:
        raise ValueError('Invalid timeout')
    for field in ('allow_document_upload', 'allow_asset_download'):
        if body.get(field) is not True:
            raise ValueError('Explicit consent required')
    req = engines.Request(body['engine'], Path(body['python']), Path(body['source']),
                          Path(body['output']), body['endpoint'], body['model'],
                          body['source_language'], body['target_language'], body['pages'],
                          body['timeout'], True, True)
    engines.validate(req)
    return job_id, req, body['api_key']

def emit(job_id, event, status, **extra):
    body = {'protocol_version': 1, 'job_id': job_id, 'event': event, 'status': status,
            'progress': None, 'cost': None, 'usage': None, **extra}
    print(json.dumps(body, ensure_ascii=True, allow_nan=False), flush=True)

def interrupted(_signum, _frame):
    raise KeyboardInterrupt()

def main():
    job_id = None
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        raw = sys.stdin.buffer.readline(MAX_INPUT_BYTES + 1)
        job_id, req, key = parse_request(raw)
        emit(job_id, 'started', 'running', engine=req.engine,
             engine_version=engines.VERSIONS[req.engine][1])
        result = engines.run(req, key)
        emit(job_id, 'completed', 'succeeded', manifest=result)
        return 0
    except engines.EngineError as error:
        code = error.code
    except subprocess.TimeoutExpired:
        code = 'timeout'
    except (ValueError, TypeError, UnicodeError, KeyError):
        code = 'invalid_request'
    except KeyboardInterrupt:
        code = 'cancelled'
    except OSError:
        code = 'io_error'
    except Exception:
        code = 'internal_error'
    emit(job_id, 'failed', 'cancelled' if code == 'cancelled' else 'failed',
         error_code=code, message=MESSAGES.get(code, MESSAGES['internal_error']))
    return 1

if __name__ == '__main__':
    raise SystemExit(main())
