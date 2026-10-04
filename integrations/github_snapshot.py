# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded public GitHub observations; never clone or execute repository code.

公开仓库的有界观察；深入核验按需读取文本，不克隆或执行代码，也不读取 API 密钥。
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

FORMAT = 'external-verification-1'
MAX_REPORT_BYTES = 1024 * 1024
MAX_TREE_BYTES = 2 * 1024 * 1024
MAX_TREE_ITEMS = 5000
MAX_README_BYTES = 64 * 1024
MAX_CODE_BYTES = 64 * 1024
SHA = re.compile(r'[0-9a-f]{40}')


def repository_url(value):
    """Only accept an explicit repository root. 只接受用户给出的仓库根 URL。"""
    if not isinstance(value, str) or len(value) > 300 or any(c.isspace() for c in value):
        raise ValueError('请输入 https://github.com/owner/repo 形式的公开仓库地址。')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.netloc.lower() != 'github.com'
            or parsed.query or parsed.fragment):
        raise ValueError('仅支持无凭据、查询参数或片段的 GitHub HTTPS 仓库根地址。')
    parts = parsed.path.rstrip('/').split('/')
    if len(parts) != 3 or parts[0]:
        raise ValueError('请使用仓库根地址，不要粘贴文件或分支链接。')
    owner, repo = parts[1:]
    repo = repo[:-4] if repo.endswith('.git') else repo
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', owner)
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', repo)
            or repo in ('.', '..')):
        raise ValueError('GitHub 仓库标识无效。')
    return f'https://github.com/{owner}/{repo}'


class SnapshotError(ValueError):
    """Stable error codes, no remote response text. 固定错误码，不传递远端正文。"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    def __init__(self, opener=None):
        self.opener = opener or build_opener(NoRedirect())

    def get(self, path, cap=256 * 1024):
        request = Request('https://api.github.com' + path, headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'PolyScholar-public-artifact-check/0.1',
            'X-GitHub-Api-Version': '2022-11-28',
        })
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = response.read(cap + 1)
            if len(raw) > cap:
                raise SnapshotError('response_limit')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise SnapshotError('invalid_response')
            return value
        except HTTPError as error:
            code = {403: 'rate_limited_or_forbidden', 429: 'rate_limited_or_forbidden',
                    404: 'not_accessible', 409: 'empty_repository'}.get(error.code, 'http_error')
            error.close()
            raise SnapshotError(code) from None
        except (URLError, TimeoutError, OSError):
            raise SnapshotError('network_unavailable') from None
        except (UnicodeError, json.JSONDecodeError):
            raise SnapshotError('invalid_response') from None

    def collect(self, value):
        url = repository_url(value)
        owner, repo = urlsplit(url).path.strip('/').split('/')
        prefix = f'/repos/{owner}/{repo}'
        report = empty_report(url)
        try:
            metadata = self.get(prefix)
            if (metadata.get('full_name', '').casefold() != f'{owner}/{repo}'.casefold()
                    or metadata.get('private') is not False):
                raise SnapshotError('invalid_response')
            branch = metadata.get('default_branch')
            if not isinstance(branch, str) or not branch or len(branch) > 255:
                raise SnapshotError('invalid_response')
            commit = self.get(prefix + '/commits/' + quote(branch, safe=''))
            sha = commit.get('sha', '')
            tree_sha = commit.get('commit', {}).get('tree', {}).get('sha', '')
            if not SHA.fullmatch(sha) or not SHA.fullmatch(tree_sha):
                raise SnapshotError('invalid_response')
            report['snapshot'] = {
                'commit_sha': sha, 'tree_sha': tree_sha,
                'committed_at': commit.get('commit', {}).get('committer', {}).get('date'),
                'tree_complete': False, 'file_count': None, 'tree_response_sha256': None,
                'commit_url': url + '/commit/' + sha,
            }
            tree = self.get(prefix + '/git/trees/' + tree_sha + '?recursive=1', MAX_TREE_BYTES)
            entries = tree.get('tree')
            if (tree.get('sha') != tree_sha or not isinstance(entries, list)
                    or not isinstance(tree.get('truncated'), bool)):
                raise SnapshotError('invalid_response')
            files = checked_files(entries[:MAX_TREE_ITEMS])
            complete = not tree['truncated'] and len(entries) <= MAX_TREE_ITEMS
            report['snapshot'].update(
                tree_complete=complete, file_count=len(files),
                tree_response_sha256=hashlib.sha256(json.dumps(
                    tree, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest(),
            )
            report['findings'] = observe_files(files, url, sha, complete)
            report['status'] = 'checked' if complete else 'partial'
            if not complete:
                report['limitations'].append('目录结果不完整；未观察到的路径一律记为未知。')
            readmes = [item for item in files if re.fullmatch(r'readme(?:\.[a-z0-9]+)?', item['path'], re.I)]
            if readmes:
                self._read_readme(report, prefix, sorted(readmes, key=lambda x: x['path'])[0])
        except (SnapshotError, KeyError, TypeError, AttributeError) as error:
            report['error_code'] = str(error) if isinstance(error, SnapshotError) else 'invalid_response'
            if report['snapshot']:
                report['status'] = 'partial'
        return report

    def fixed_tree(self, value, tree_sha):
        repository = repository_url(value)
        if not isinstance(tree_sha, str) or not SHA.fullmatch(tree_sha):
            raise ValueError('目录 SHA 无效。')
        prefix = '/repos/' + urlsplit(repository).path.strip('/')
        tree = self.get(prefix + '/git/trees/' + tree_sha + '?recursive=1', MAX_TREE_BYTES)
        if (tree.get('sha') != tree_sha or not isinstance(tree.get('tree'), list)
                or type(tree.get('truncated')) is not bool):
            raise SnapshotError('invalid_response')
        return {
            'tree_sha': tree_sha,
            'complete': not tree['truncated'] and len(tree['tree']) <= MAX_TREE_ITEMS,
            'files': checked_files(tree['tree'][:MAX_TREE_ITEMS]),
        }

    def code_blob(self, value, blob_sha, size):
        repository = repository_url(value)
        if (not isinstance(blob_sha, str) or not SHA.fullmatch(blob_sha)
                or type(size) is not int or not 0 < size <= MAX_CODE_BYTES):
            raise ValueError('只读取不超过 64 KiB 的非空文本文件。')
        prefix = '/repos/' + urlsplit(repository).path.strip('/')
        blob = self.get(prefix + '/git/blobs/' + blob_sha, 128 * 1024)
        try:
            raw = base64.b64decode(''.join(blob['content'].split()), validate=True)
            digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if (blob.get('encoding') != 'base64' or blob.get('sha') != blob_sha
                    or len(raw) != size or digest != blob_sha):
                raise ValueError()
            content = raw.decode('utf-8')
            if (any(ord(c) < 32 and c not in '\n\r\t' for c in content)
                    or content.startswith('version https://git-lfs.github.com/spec/v1')):
                raise ValueError()
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise SnapshotError('invalid_text_blob') from None
        return {'blob_sha': blob_sha, 'size': size, 'content': content,
                'sha256': hashlib.sha256(raw).hexdigest()}

    def _read_readme(self, report, prefix, entry):
        try:
            if entry['size'] > MAX_README_BYTES:
                raise SnapshotError('readme_limit')
            blob = self.get(prefix + '/git/blobs/' + entry['sha'])
            if blob.get('encoding') != 'base64' or blob.get('sha') != entry['sha']:
                raise SnapshotError('invalid_response')
            raw = base64.b64decode(''.join(blob['content'].split()), validate=True)
            git_digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if len(raw) != entry['size'] or git_digest != entry['sha']:
                raise SnapshotError('invalid_response')
            text = raw.decode('utf-8')
            if not text.strip() or any(ord(c) < 32 and c not in '\n\r\t' for c in text):
                raise SnapshotError('invalid_response')
            report['readme'] = {
                'path': entry['path'], 'blob_sha': entry['sha'],
                'sha256': hashlib.sha256(raw).hexdigest(), 'excerpt': text[:4000],
                'excerpt_truncated': len(text) > 4000,
                'url': report['repository_url'] + '/blob/' + report['snapshot']['commit_sha'] + '/' + quote(entry['path'], safe='/'),
            }
        except (SnapshotError, ValueError, UnicodeError, KeyError, TypeError):
            report['status'] = 'partial'
            report['limitations'].append('README 内容不可读取或超过上限；不据此推断论文与代码是否一致。')


def empty_report(url):
    return {
        'format': FORMAT, 'kind': 'github',
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'repository_url': url, 'status': 'unavailable', 'error_code': None,
        'snapshot': None, 'findings': [], 'readme': None,
        'limitations': [
            '仅检查默认分支的一次提交快照，未执行代码或复现实验。',
            '路径匹配仅为制品线索，不证明功能实现、代码质量或科学结论正确。',
            '仓库与论文主张的一致性、训练数据、随机种子和结果真实性未自动核实。',
            '不读取子模块、LFS 对象、其他分支或外部制品。',
        ],
    }

def checked_files(entries):
    """Validate remote paths without touching the filesystem. 校验远端路径，不落盘。"""
    files = []
    seen = set()
    for item in entries:
        if not isinstance(item, dict):
            raise SnapshotError('invalid_response')
        if item.get('type') != 'blob' or item.get('mode') not in ('100644', '100755'):
            continue
        path, sha, size = item.get('path'), item.get('sha'), item.get('size')
        if (not isinstance(path, str) or not 1 <= len(path) <= 1024
                or path.startswith('/') or '\\' in path
                or any(part in ('', '.', '..') for part in path.split('/'))
                or any(ord(c) < 32 for c in path) or path in seen
                or not isinstance(sha, str) or not SHA.fullmatch(sha)
                or type(size) is not int or size < 0):
            raise SnapshotError('invalid_response')
        seen.add(path)
        files.append({'path': path, 'sha': sha, 'size': size})
    return files


def observe_files(files, url, sha, complete):
    patterns = (
        ('source', '源代码路径', r'\.(?:py|ipynb|cpp|cc|c|h|rs|jl|r|m|java|go)$'),
        ('training', '训练相关路径', r'(?:^|/)(?:train|training|trainer)(?:[_/.]|$)'),
        ('inference', '推理相关路径', r'(?:^|/)(?:inference|infer|predict|demo)(?:[_/.]|$)'),
        ('evaluation', '评测相关路径', r'(?:^|/)(?:eval|evaluate|evaluation|benchmark)(?:[_/.]|$)'),
        ('dependencies', '依赖说明路径', r'(?:^|/)(?:requirements[^/]*\.txt|pyproject\.toml|environment\.ya?ml|setup\.py|Cargo\.toml)$'),
        ('license', '许可文件路径', r'(?:^|/)(?:license|licence|copying)(?:\.[^/]*)?$'),
        ('tests', '测试相关路径', r'(?:^|/)(?:tests?/|test[_/.])'),
        ('ci', 'CI 配置路径', r'^\.github/workflows/[^/]+\.ya?ml$'),
    )
    findings = []
    for key, label, pattern in patterns:
        matches = [item for item in files if re.search(pattern, item['path'], re.I) and item['size'] > 0]
        findings.append({
            'key': key, 'label': label,
            'status': 'observed' if matches else ('not_observed' if complete else 'unknown'),
            'count': len(matches),
            'evidence': [{'path': item['path'], 'blob_sha': item['sha'],
                          'url': url + '/blob/' + sha + '/' + quote(item['path'], safe='/')}
                         for item in matches[:5]],
        })
    return findings


def main():
    try:
        raw = sys.stdin.buffer.readline(2049)
        request = json.loads(raw)
        client = GitHubClient()
        if set(request) == {'repository_url'}:
            report = client.collect(request['repository_url'])
        elif set(request) == {'action', 'repository_url', 'tree_sha'} and request['action'] == 'tree':
            report = client.fixed_tree(request['repository_url'], request['tree_sha'])
        elif set(request) == {'action', 'repository_url', 'blob_sha', 'size'} and request['action'] == 'file':
            report = client.code_blob(request['repository_url'], request['blob_sha'], request['size'])
        else:
            raise ValueError('invalid_request')
        result = json.dumps(report, ensure_ascii=False).encode('utf-8')
        if len(result) > MAX_REPORT_BYTES:
            raise ValueError('report_limit')
        sys.stdout.buffer.write(result + b'\n')
        return 0
    except Exception:
        sys.stdout.buffer.write(b'{"error":"invalid_output"}\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
