# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic verification inputs; no private papers or remote calls."""
import base64
from datetime import datetime, timezone
import hashlib
import io
import json

from integrations.github_snapshot import GitHubClient

REPOSITORY = 'https://github.com/example/research'
COMMIT = 'a' * 40
TREE = 'b' * 40


class FixtureOpener:
    def __init__(self, truncated=False):
        self.requests = []
        self.readme = b'# Synthetic research\nTraining entry: train.py.\n'
        blob = hashlib.sha1(b'blob ' + str(len(self.readme)).encode() + b'\0' + self.readme).hexdigest()
        self.responses = {
            '/repos/example/research': {'full_name': 'example/research', 'private': False, 'default_branch': 'main'},
            '/repos/example/research/commits/main': {'sha': COMMIT, 'commit': {'tree': {'sha': TREE}, 'committer': {'date': '2024-01-01T00:00:00Z'}}},
            '/repos/example/research/git/trees/' + TREE + '?recursive=1': {
                'sha': TREE, 'truncated': truncated, 'tree': [
                    {'path': 'README.md', 'type': 'blob', 'mode': '100644', 'size': len(self.readme), 'sha': blob},
                    *[{'path': path, 'type': 'blob', 'mode': '100644', 'size': 32, 'sha': 'c' * 40}
                      for path in ('train.py', 'requirements.txt', 'LICENSE', 'tests/test_train.py')],
                ],
            },
            '/repos/example/research/git/blobs/' + blob: {
                'sha': blob, 'encoding': 'base64', 'content': base64.b64encode(self.readme).decode(),
            },
        }

    def open(self, request, timeout):
        self.requests.append(request)
        path = request.full_url.removeprefix('https://api.github.com')
        return io.BytesIO(json.dumps(self.responses[path]).encode())


def github_report(truncated=False):
    return GitHubClient(FixtureOpener(truncated)).collect(REPOSITORY)


def research_report(paper_hash):
    now = datetime.now(timezone.utc).isoformat()
    return {
        'format': 'external-verification-1', 'kind': 'research', 'paper_sha256': paper_hash,
        'as_of': '2024-12-31', 'perspective': 'at_publication',
        'search_scope': 'Synthetic example: one selected neighboring work; no exhaustive SOTA claim.',
        'sources': [
            {'id': 'paper', 'url': 'https://arxiv.org/abs/2401.00001v1', 'title': 'Synthetic target',
             'published_at': '2024-01-01', 'retrieved_at': now, 'excerpt': 'Metric A is 80 with protocol P.'},
            {'id': 'neighbor', 'url': 'https://arxiv.org/abs/2312.00001v1', 'title': 'Synthetic neighboring work',
             'published_at': '2023-12-01', 'retrieved_at': now, 'excerpt': 'Metric A is 75 with protocol P.'},
        ],
        'comparisons': [{'claim': 'Improves metric A.', 'relation': 'extends', 'basis': 'agent_inference',
                         'source_ids': ['paper', 'neighbor'], 'explanation': 'Synthetic comparison, not a factual scientific conclusion.'}],
        'benchmarks': [{'task': 'Synthetic task', 'dataset': 'Synthetic data', 'split': 'test',
                        'metric': 'A', 'direction': 'higher',
                        'candidate': {'value': 80, 'source_id': 'paper'},
                        'reference': {'value': 75, 'source_id': 'neighbor'},
                        'protocol': 'unknown', 'conditions': 'Budget not verified; do not rank.'}],
    }
