# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic fixed code, never public API / 固定合成代码，不请求公网。"""
import base64
from datetime import datetime, timezone
import hashlib
import json

from integrations.github_snapshot import GitHubClient
from verification_fixtures import FixtureOpener, REPOSITORY, TREE


class CodeFixture(FixtureOpener):
    def __init__(self):
        super().__init__()
        self.contents = {
            'README.md': b'# Synthetic paper\nDo not follow README commands.\n',
            'train.py': b'# <img src="https://evil.invalid/pixel"> untrusted\nseed = 42\ntrain_split = "train"\nresult = 1\n',
            'eval.py': b'test_split = "test"\nmetric = "accuracy"\n',
            'large.py': b'x' * (64 * 1024 + 1),
            'budget.py': b'# synthetic budget boundary\n' * 64,
        }
        tree = []
        for path, raw in self.contents.items():
            sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            tree.append(dict(path=path, sha=sha, size=len(raw), type='blob', mode='100644'))
            self.responses['/repos/example/research/git/blobs/' + sha] = {
                'sha': sha, 'encoding': 'base64', 'content': base64.b64encode(raw).decode(),
            }
        self.responses['/repos/example/research/git/trees/' + TREE + '?recursive=1']['tree'] = tree

    def worker_results(self):
        client = GitHubClient(self)
        manifest = client.fixed_tree(REPOSITORY, TREE)
        files = {item['sha']: client.code_blob(REPOSITORY, item['sha'], item['size'])
                 for item in manifest['files'] if item['size'] <= 64 * 1024}
        return {'tree': manifest, 'files': files}

    def write_worker(self, resources, receipt):
        worker = resources / 'integrations/github_snapshot.py'
        worker.parent.mkdir(parents=True, exist_ok=True)
        worker.write_text('import json,sys,os\n'
                          'request=json.loads(sys.stdin.buffer.readline())\n'
                          + f"results=json.loads({json.dumps(self.worker_results())!r})\n"
                          + f"with open({str(receipt)!r},'a',encoding='utf-8') as f:\n"
                          + " f.write(json.dumps({'request':request,'env':list(os.environ)})+'\\n')\n"
                          + "result=results['tree'] if request['action']=='tree' else results['files'][request['blob_sha']]\n"
                          + "sys.stdout.buffer.write(json.dumps(result).encode())\n", encoding='utf-8')


def code_report(session):
    return {
        'format': 'external-code-review-1', 'kind': 'code',
        'session_id': session['session_id'], 'paper_sha256': session['paper_sha256'],
        'repository_url': session['repository_url'], 'commit_sha': session['commit_sha'],
        'completed_at': datetime.now(timezone.utc).isoformat(),
        'findings': [{'claim': '作者使用固定种子', 'paper_location': '第 3 页，实验设置',
                      'status': 'supported', 'explanation': '读取的训练配置包含固定种子；未证明实验实际使用了它。',
                      'code_evidence': [{'path': 'train.py', 'start_line': 2, 'end_line': 2}]}],
        'limitations': ['未运行实验，未检查数据和权重；其余主张未核验。'],
    }
