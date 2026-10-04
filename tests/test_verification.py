# SPDX-License-Identifier: AGPL-3.0-only
from contextlib import closing, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from integrations.github_snapshot import GitHubClient, NoRedirect, repository_url
from polyscholar import cli
from polyscholar.service import LocalService
from polyscholar.store import LocalStore
from verification_fixtures import FixtureOpener, github_report, research_report, REPOSITORY, TREE


class GitHubClientTests(unittest.TestCase):
    def test_repository_boundary_and_fixed_commit_sources(self):
        self.assertEqual(repository_url(REPOSITORY + '.git/'), REPOSITORY)
        for url in ('http://github.com/example/research', 'https://github.com.evil.test/a/b',
                    'https://user:secret@github.com/a/b', 'https://github.com/a/b/tree/main',
                    'https://github.com/a/b?token=secret', 'https://github.com/a/b#readme',
                    'https://github.com/a/..', 'https://github.com/a/%2e%2e'):
            with self.assertRaises(ValueError):
                repository_url(url)
        opener = FixtureOpener()
        report = GitHubClient(opener).collect(REPOSITORY)
        self.assertEqual(report['status'], 'checked')
        self.assertTrue(report['snapshot']['tree_complete'])
        self.assertEqual(report['readme']['excerpt'], opener.readme.decode())
        for request in opener.requests:
            self.assertIsNone(request.data)
            self.assertNotIn('Authorization', request.headers)
            self.assertTrue(request.full_url.startswith('https://api.github.com/repos/example/research'))
        self.assertIn('/git/trees/' + TREE, opener.requests[2].full_url)
        training = next(item for item in report['findings'] if item['key'] == 'training')
        self.assertEqual(training['status'], 'observed')
        self.assertIn('/blob/' + 'a' * 40 + '/train.py', training['evidence'][0]['url'])

    def test_partial_tree_never_reports_absence(self):
        report = github_report(truncated=True)
        self.assertEqual(report['status'], 'partial')
        self.assertTrue(all(item['status'] != 'not_observed' for item in report['findings']))

    def test_public_access_errors_are_unknown_not_false_fraud(self):
        for status, expected in ((404, 'not_accessible'), (403, 'rate_limited_or_forbidden'),
                                 (409, 'empty_repository'), (302, 'http_error')):
            opener = FixtureOpener()
            with patch.object(opener, 'open', side_effect=HTTPError(REPOSITORY, status, 'secret raw server body', {}, io.BytesIO())):
                report = GitHubClient(opener).collect(REPOSITORY)
            self.assertEqual(report['status'], 'unavailable')
            self.assertEqual(report['error_code'], expected)
            self.assertNotIn('secret', json.dumps(report))
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test'))

    def test_readme_hash_and_remote_tree_identity_rejected(self):
        opener = FixtureOpener()
        blob_key = next(path for path in opener.responses if '/git/blobs/' in path)
        opener.responses[blob_key]['content'] = 'c2VjcmV0'
        report = GitHubClient(opener).collect(REPOSITORY)
        self.assertEqual(report['status'], 'partial')
        self.assertIsNone(report['readme'])
        opener = FixtureOpener()
        tree_key = next(path for path in opener.responses if '/git/trees/' in path)
        opener.responses[tree_key]['sha'] = 'd' * 40
        report = GitHubClient(opener).collect(REPOSITORY)
        self.assertEqual(report['error_code'], 'invalid_response')
        self.assertFalse(report['snapshot']['tree_complete'])


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root / 'data')
        self.addCleanup(self.service.close)
        self.source = self.root / 'synthetic.pdf'
        self.source.write_bytes(b'%PDF-1.7 synthetic external verification target')
        self.document = self.service.import_pdf(self.source)
        self.service.set_scores(self.document['id'], [{'kind': 'paper', 'score': 80}, {'kind': 'confidence', 'score': 90}])

    def save(self, report):
        return self.service.store.save_verification(self.document['id'], report, self.document['sha256'])

    def write_research(self, report=None):
        path = self.root / 'research.json'
        path.write_text(json.dumps(report or research_report(self.document['sha256'])), encoding='utf-8')
        return path

    def test_immutable_history_scores_export_and_restart(self):
        before = self.service.document_scores(self.document['id'])
        a = self.save(github_report())
        b = self.service.import_research_report(self.document['id'], self.write_research())
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(self.service.document_scores(self.document['id']), before)
        result = self.service.verification_report(self.document['id'], b['id'])
        self.assertIsNone(result['report']['benchmarks'][0]['delta'])
        self.assertEqual(result['report']['provenance'], 'agent_report_not_independently_verified')
        target = self.root / 'export.json'
        self.service.export_verification(self.document['id'], b['id'], target)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')), result)
        with self.assertRaises(ValueError):
            self.service.export_verification(self.document['id'], b['id'], self.source)
        self.service.close()
        reopened = LocalService(self.root / 'data')
        try:
            self.assertEqual(len(reopened.list_verifications(self.document['id'])), 2)
            self.assertEqual(reopened.verification_report(self.document['id'], a['id'])['sha256'], a['sha256'])
            reopened.delete_document(self.document['id'])
            with self.assertRaises(ValueError):
                reopened.list_verifications(self.document['id'])
            reopened.restore_document(self.document['id'])
            self.assertEqual(len(reopened.list_verifications(self.document['id'])), 2)
            reopened.delete_document(self.document['id'])
            reopened.purge_document(self.document['id'])
            with reopened.store.connection() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_verifications').fetchone()[0], 0)
        finally:
            reopened.close()

    def test_research_boundary_rejects_identity_dates_links_and_numeric_bools(self):
        valid = research_report(self.document['sha256'])
        variants = []
        for key, value in (('paper_sha256', 'wrong'), ('as_of', '2099-01-01'), ('score', 100)):
            report = deepcopy(valid)
            report[key] = value
            variants.append(report)
        for key, value in (('url', 'file:///private'), ('published_at', '2025-01-01'),
                           ('published_at', None), ('retrieved_at', '2024-01-01')):
            report = deepcopy(valid)
            report['sources'][0][key] = value
            variants.append(report)
        report = deepcopy(valid)
        report['comparisons'][0]['source_ids'] = ['invented']
        variants.append(report)
        report = deepcopy(valid)
        report['benchmarks'][0]['reference']['value'] = True
        variants.append(report)
        report = deepcopy(valid)
        report['benchmarks'][0]['reference']['value'] = 10 ** 400
        variants.append(report)
        report = deepcopy(valid)
        report['benchmarks'][0].update(protocol='comparable',
            candidate={'value': 1e308, 'source_id': valid['sources'][0]['id']},
            reference={'value': -1e308, 'source_id': valid['sources'][1]['id']})
        variants.append(report)
        for report in variants:
            with self.assertRaises(ValueError):
                self.save(report)
        self.assertEqual(self.service.list_verifications(self.document['id']), [])
        report = deepcopy(valid)
        report['benchmarks'][0]['protocol'] = 'comparable'
        saved = self.save(report)
        self.assertEqual(self.service.verification_report(self.document['id'], saved['id'])['report']['benchmarks'][0]['delta'], 5)

    def test_transaction_rollback_audit_privacy_and_cross_document_guard(self):
        with self.service.store.connection() as db:
            db.execute("CREATE TRIGGER reject_verification_audit BEFORE INSERT ON desktop_audit WHEN NEW.point='external_verification_saved' BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.save(github_report())
        self.assertEqual(self.service.list_verifications(self.document['id']), [])
        with self.service.store.connection() as db:
            db.execute('DROP TRIGGER reject_verification_audit')
        saved = self.save(github_report())
        other = self.root / 'other.pdf'
        other.write_bytes(b'%PDF-1.7 another synthetic identity')
        other = self.service.import_pdf(other)
        with self.assertRaises(ValueError):
            self.service.verification_report(other['id'], saved['id'])
        with self.assertRaises(ValueError):
            self.service.store.save_verification(self.document['id'], github_report(), 'changed')
        with self.service.store.connection() as db:
            audit = db.execute("SELECT point,outcome FROM desktop_audit WHERE point='external_verification_saved'").fetchall()
            self.assertEqual(audit, [('external_verification_saved', 'succeeded')])
            db.execute('UPDATE desktop_verifications SET data=? WHERE id=?', ('{}', saved['id']))
        with self.assertRaisesRegex(ValueError, '校验和'):
            self.service.verification_report(self.document['id'], saved['id'])

    def test_forged_snapshot_evidence_and_absence_are_rejected(self):
        variants = []
        report = github_report()
        report['findings'][0]['evidence'][0]['url'] += '?redirect=another'
        variants.append(report)
        report = github_report()
        report['findings'][0]['evidence'][0]['blob_sha'] = 'not-a-blob'
        variants.append(report)
        report = github_report(truncated=True)
        next(item for item in report['findings'] if item['count'] == 0)['status'] = 'not_observed'
        variants.append(report)
        report = github_report()
        report['snapshot']['file_count'] = True
        variants.append(report)
        for report in variants:
            with self.assertRaises(ValueError):
                self.save(report)
        self.assertEqual(self.service.list_verifications(self.document['id']), [])

    def test_migration_failure_keeps_v12_backup_documents_and_scores(self):
        self.service.close()
        db_path = self.root / 'data/library.sqlite3'
        with closing(sqlite3.connect(db_path)) as db:
            db.executescript('DROP TABLE desktop_verifications; PRAGMA user_version=12;')
        with patch('polyscholar.store.VERIFICATION_SCHEMA', 'CREATE TABLE broken('):
            with self.assertRaises(sqlite3.Error):
                LocalStore(self.root / 'data')
        with closing(sqlite3.connect(db_path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 12)
            self.assertEqual(db.execute('SELECT count(*) FROM desktop_scores').fetchone()[0], 2)
        self.assertTrue((self.root / 'data/library-before-v14.sqlite3').is_file())
        reopened = LocalService(self.root / 'data')
        try:
            with reopened.store.connection() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 15)
        finally:
            reopened.close()

    def test_cli_authorization_import_and_light_receipts(self):
        with patch('polyscholar.service.subprocess.Popen') as launch:
            with redirect_stderr(io.StringIO()):
                code = cli.main(['verify', 'github', self.document['id'], '--repo', REPOSITORY], service=self.service)
            self.assertEqual(code, 1)
            launch.assert_not_called()
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(['verify', 'import-research', self.document['id'], '--file', str(self.write_research()), '--json'], service=self.service)
        self.assertEqual(code, 0)
        receipt = json.loads(output.getvalue())
        self.assertEqual(set(receipt), {'id', 'kind', 'status', 'created_at', 'sha256'})
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['verify', 'import-research', self.document['id']], service=self.service), 1)

    def test_fileless_root_resolves_real_primary_pdf_and_preserves_report_identity(self):
        item = self.service.create_bibliographic_item({'title': 'Fileless research record'})
        with patch('polyscholar.service.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError, '实际 PDF'):
                self.service.verify_github(item['id'], REPOSITORY, authorized=True)
            launch.assert_not_called()
        attached_source = self.root / 'unique-primary.pdf'
        attached_source.write_bytes(self.source.read_bytes() + b'\n% unique primary identity\n')
        primary = self.service.import_attachment(item['id'], attached_source, 'supplement')
        self.service.set_primary_pdf(item['id'], primary['documentId'])
        report = research_report(self.service.store.document(primary['documentId'])['sha256'])
        receipt = self.service.import_research_report(item['id'], self.write_research(report))
        wrapper = self.service.verification_report(item['id'], receipt['id'])
        self.assertEqual(wrapper['document_sha256'], report['paper_sha256'])
        self.assertEqual(self.service.list_verifications(item['id'])[0]['id'], receipt['id'])
        self.assertIsNone(self.service.store.document(item['id'])['sha256'])

    def test_both_v13_schema_variants_upgrade_without_losing_data(self):
        for pdf_only in (False, True):
            with self.subTest(pdf_only=pdf_only):
                root = self.root / ('feature-v13' if pdf_only else 'main-v13')
                service = LocalService(root)
                try:
                    document = service.import_pdf(self.source)
                    service.set_scores(document['id'], [{'kind': 'summary', 'rationale': 'Saved extraction'}])
                    if pdf_only:
                        saved = service.store.save_verification(document['id'], github_report(), document['sha256'])
                    else:
                        claim = service.store.save_claim(document['id'], 'Saved evidence note', [])
                        book = service.create_bibliographic_item({'title': 'Saved fileless book'})
                finally:
                    service.close()
                with closing(sqlite3.connect(root / 'library.sqlite3')) as db:
                    if pdf_only:
                        db.executescript('''PRAGMA foreign_keys=OFF;PRAGMA legacy_alter_table=ON;
                            BEGIN IMMEDIATE;
                            CREATE TABLE feature_documents(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,data TEXT NOT NULL);
                            INSERT INTO feature_documents SELECT * FROM desktop_documents;
                            DROP TABLE desktop_documents;ALTER TABLE feature_documents RENAME TO desktop_documents;
                            DROP TABLE desktop_claim_provenance;DROP TABLE desktop_model_summaries;
                            DROP TABLE desktop_claim_evidence;DROP TABLE desktop_claims;
                            PRAGMA user_version=13;COMMIT;''')
                    else:
                        db.executescript('DROP TABLE desktop_verifications;PRAGMA user_version=13;')
                reopened = LocalService(root)
                try:
                    self.assertTrue((root / 'library-before-v14.sqlite3').is_file())
                    self.assertEqual(reopened.document_scores(document['id'])['summary']['rationale'], 'Saved extraction')
                    if pdf_only:
                        self.assertEqual(reopened.verification_report(document['id'], saved['id'])['report']['status'], 'checked')
                        self.assertIsNone(reopened.create_bibliographic_item({'title': 'New book'})['sha256'])
                    else:
                        self.assertEqual(reopened.store.list_claims(document['id'])[0]['id'], claim['id'])
                        self.assertIsNone(reopened.store.document(book['id'])['sha256'])
                    with reopened.store.connection() as db:
                        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 15)
                        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                finally:
                    reopened.close()


if __name__ == '__main__':
    unittest.main()
