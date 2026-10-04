# SPDX-License-Identifier: AGPL-3.0-only
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from integrations.github_snapshot import GitHubClient, SnapshotError
from polyscholar.mcp_bridge import CliBridge, BridgeConfiguration, CommandPolicy, CommandRejected
from polyscholar.service import LocalService
from polyscholar.store import LocalStore
from polyscholar.verification import VERIFICATION_SCHEMA
from code_review_fixtures import CodeFixture, code_report
from verification_fixtures import REPOSITORY, TREE


class CodeReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.resources = self.root / 'resources'
        self.receipt = self.root / 'requests.jsonl'
        self.fixture = CodeFixture()
        self.fixture.write_worker(self.resources, self.receipt)
        self.service = LocalService(self.root / 'data', resources_dir=self.resources)
        self.addCleanup(lambda: self.service.close())
        source = self.root / 'paper.pdf'
        source.write_bytes(b'%PDF-1.7 private synthetic paper')
        self.document = self.service.import_pdf(source)
        self.service.set_scores(self.document['id'], [{'kind': 'confidence', 'score': 80}])
        report = GitHubClient(self.fixture).collect(REPOSITORY)
        self.base = self.service.store.save_verification(self.document['id'], report, self.document['sha256'])

    def begin(self, **options):
        return self.service.begin_code_review(self.document['id'], self.base['id'],
                                               depth='deep', authorized=True, **options)

    def read(self, session, path='train.py', start=1, end=2):
        return self.service.read_code(self.document['id'], session['session_id'], path, start, end)

    def write(self, payload):
        path = self.root / 'code.json'
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return path

    def test_depth_and_budget_confirmation_precede_network(self):
        plan = self.service.code_review_plan(self.document['id'])
        self.assertTrue(plan['confirmation_required'])
        self.assertEqual([item['depth'] for item in plan['depths']], ['basic', 'deep'])
        with patch.object(self.service, '_run_github_worker') as worker:
            for depth, yes in ((None, True), ('basic', True), ('deep', False)):
                with self.assertRaises(ValueError):
                    self.service.begin_code_review(self.document['id'], self.base['id'], depth=depth, authorized=yes)
            for kwargs in ({'max_files': 33}, {'max_bytes': True}, {'max_bytes': 999999}, {'scope': ''}):
                with self.assertRaises(ValueError):
                    self.begin(**kwargs)
            worker.assert_not_called()

    def test_fixed_objects_worker_privacy_cache_and_budget(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'secret', 'OPENAI_API_KEY': 'private',
                                     'POLYSCHOLAR_DATA_DIR': 'private-path'}):
            session = self.begin(max_files=1)
            first = self.read(session)
        self.assertEqual(first['end_line'], 2)
        self.assertTrue(first['untrusted_content'])
        self.assertTrue(first['has_more'])
        self.read(session, start=3, end=4)
        for path in ('eval.py', '../train.py', 'large.py'):
            with self.assertRaises(ValueError):
                self.read(session, path)
        requests = [json.loads(line) for line in self.receipt.read_text().splitlines()]
        self.assertEqual(len(requests), 2)  # cached reads and budget refusal make no requests
        self.assertEqual(requests[0]['request'], {'action': 'tree', 'repository_url': REPOSITORY, 'tree_sha': TREE})
        self.assertEqual(set(requests[1]['request']), {'action', 'repository_url', 'blob_sha', 'size'})
        for request in requests:
            self.assertFalse({'GITHUB_TOKEN', 'OPENAI_API_KEY', 'POLYSCHOLAR_DATA_DIR'} & set(request['env']))
            self.assertNotIn(self.document['sha256'], json.dumps(request['request']))
        self.assertFalse(self.service._children)

    def test_report_rejects_unread_lines_forged_versions_and_scores(self):
        session = self.begin()
        self.read(session)
        payload = code_report(session)
        variants = []
        for key, value in (('paper_sha256', '0' * 64), ('commit_sha', '0' * 40), ('score', 100), ('limitations', [])):
            candidate = deepcopy(payload)
            candidate[key] = value
            variants.append(candidate)
        for path, start, end in (('eval.py', 1, 1), ('train.py', 3, 3), ('train.py', 2, 200)):
            candidate = deepcopy(payload)
            candidate['findings'][0]['code_evidence'] = [{'path': path, 'start_line': start, 'end_line': end}]
            variants.append(candidate)
        for candidate in variants:
            with self.assertRaises(ValueError):
                self.service.import_code_review(self.document['id'], self.write(candidate))
        self.assertEqual(self.service.code_review_status(self.document['id'], session['session_id'])['state'], 'open')
        self.assertEqual(len(self.service.list_verifications(self.document['id'])), 1)

    def test_byte_budget_partial_tree_and_cross_document_identity(self):
        session = self.begin(max_bytes=1024)
        with self.assertRaisesRegex(ValueError, '预算'):
            self.read(session, 'budget.py')
        self.assertEqual(len(self.receipt.read_text().splitlines()), 1)
        self.read(session)
        source = self.root / 'other.pdf'
        source.write_bytes(b'%PDF-1.7 different synthetic paper')
        other = self.service.import_pdf(source)
        with self.assertRaises(ValueError):
            self.service.read_code(other['id'], session['session_id'], 'train.py')
        with self.assertRaises(ValueError):
            self.service.submit_code_review(other['id'], code_report(session))
        tree = self.fixture.worker_results()['tree']
        tree['complete'] = False
        with patch.object(self.service, '_run_github_worker', return_value=tree):
            partial = self.begin()
        self.assertFalse(partial['tree_complete'])

    def test_cli_wrong_depth_and_payload_options_do_not_launch(self):
        from contextlib import redirect_stderr
        import io
        from polyscholar import cli
        with patch.object(self.service, '_run_github_worker') as worker, redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['verify', 'github', self.document['id'], '--repo', REPOSITORY,
                                       '--depth', 'deep', '--yes'], service=self.service), 1)
            self.assertEqual(cli.main(['verify', 'import-code', self.document['id'], '--payload', '{}',
                                       '--file', 'report.json'], service=self.service), 1)
            self.assertEqual(cli.main(['verify', 'import-code', self.document['id'], '--payload', 'broken'],
                                      service=self.service), 1)
            worker.assert_not_called()

    def test_restart_cli_bridge_report_display_and_atomic_history(self):
        session = self.begin()
        self.read(session)
        before = self.service.document_scores(self.document['id'])
        path = self.write(code_report(session))
        self.service.close()
        bridge = CliBridge(BridgeConfiguration(self.root / 'data'))
        def execute(action, *args):
            result = bridge.execute(['verify', action, self.document['id'], *args, '--json'])
            self.assertTrue(result['success'], result)
            self.assertFalse(result['truncated'])
            return json.loads(result['stdout'])
        execute('code-tree', '--session', session['session_id'], '--prefix', 'train')
        cached = execute('read-code', '--session', session['session_id'], '--path', 'train.py')
        self.assertIn('seed = 42', cached['content'])
        saved = execute('import-code', '--payload', path.read_text(encoding='utf-8'))
        self.assertEqual(saved['kind'], 'code')
        self.service = LocalService(self.root / 'data', resources_dir=self.resources)
        wrapper = self.service.verification_report(self.document['id'], saved['id'])
        evidence = wrapper['report']['findings'][0]['code_evidence'][0]
        self.assertEqual(evidence['excerpt'], 'seed = 42')
        self.assertIn(session['commit_sha'], evidence['url'])
        self.assertEqual(self.service.document_scores(self.document['id']), before)
        with self.assertRaises(ValueError):
            self.service.import_code_review(self.document['id'], path)
        with self.assertRaises(ValueError):
            self.read(session)
        self.service.delete_document(self.document['id'])
        with self.assertRaises(ValueError):
            self.service.code_review_status(self.document['id'], session['session_id'])
        self.service.restore_document(self.document['id'])
        self.assertEqual(len(self.service.list_verifications(self.document['id'])), 2)
        self.service.delete_document(self.document['id'])
        self.service.purge_document(self.document['id'])
        with self.service.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM desktop_code_files').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM desktop_code_sessions').fetchone()[0], 0)

    def test_atomic_completion_rolls_back_when_audit_fails(self):
        session = self.begin()
        self.read(session)
        with self.service.store.connection() as db:
            db.execute("CREATE TRIGGER reject_code_audit BEFORE INSERT ON desktop_audit BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.Error):
            self.service.import_code_review(self.document['id'], self.write(code_report(session)))
        self.assertEqual(self.service.code_review_status(self.document['id'], session['session_id'])['state'], 'open')
        self.assertEqual(len(self.service.list_verifications(self.document['id'])), 1)

    def test_v14_upgrade_preserves_reports_and_failure_rolls_back(self):
        self.service.close()
        db_path = self.root / 'data/library.sqlite3'
        old_schema = VERIFICATION_SCHEMA.replace("'github','research','code'", "'github','research'")
        with closing(sqlite3.connect(db_path)) as db:
            db.executescript('DROP TABLE desktop_code_files;DROP TABLE desktop_code_sessions;'
                             'ALTER TABLE desktop_verifications RENAME TO old_reports;'
                             'DROP INDEX desktop_verifications_document;' + old_schema
                             + 'INSERT INTO desktop_verifications SELECT * FROM old_reports;'
                             'DROP TABLE old_reports; PRAGMA user_version=14;')
        with patch('polyscholar.store.CODE_SCHEMA', 'CREATE TABLE broken('):
            with self.assertRaises(sqlite3.Error):
                LocalStore(self.root / 'data')
        with closing(sqlite3.connect(db_path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 14)
            self.assertEqual(db.execute('SELECT count(*) FROM desktop_verifications').fetchone()[0], 1)
        self.assertTrue((self.root / 'data/library-before-v15.sqlite3').is_file())
        self.service = LocalService(self.root / 'data', resources_dir=self.resources)
        self.assertEqual(self.service.verification_report(self.document['id'], self.base['id'])['sha256'], self.base['sha256'])
        self.begin()
        with self.service.store.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 15)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_readonly_mcp_rejects_start_read_and_import(self):
        policy = CommandPolicy()
        for action, args in (('begin-code', ['--report', 'base', '--depth', 'deep', '--yes']),
                             ('read-code', ['--session', 'session', '--path', 'train.py']),
                             ('import-code', ['--file', 'report.json'])):
            with self.assertRaises(CommandRejected):
                policy.validate(['verify', action, 'doc', *args], read_only=True)
        for action in ('plan', 'code-tree', 'code-status'):
            self.assertEqual(policy.validate(['verify', action, 'doc'], read_only=True), 'read')

    def test_client_rejects_changed_blobs_binary_and_symlink_manifest(self):
        client = GitHubClient(self.fixture)
        tree = client.fixed_tree(REPOSITORY, TREE)
        item = next(f for f in tree['files'] if f['path'] == 'train.py')
        response = self.fixture.responses['/repos/example/research/git/blobs/' + item['sha']]
        response['content'] = 'c2VjcmV0'
        with self.assertRaises(SnapshotError):
            client.code_blob(REPOSITORY, item['sha'], item['size'])
        self.fixture.responses['/repos/example/research/git/trees/' + TREE + '?recursive=1']['tree'][1]['mode'] = '120000'
        self.assertNotIn('train.py', [f['path'] for f in client.fixed_tree(REPOSITORY, TREE)['files']])
