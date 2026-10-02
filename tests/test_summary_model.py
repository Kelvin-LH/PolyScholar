# SPDX-License-Identifier: AGPL-3.0-only
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from polyscholar.service import LocalService
from polyscholar.summary_model import MAX_RESPONSE_BYTES, selected_blocks, validate_claims, _http_summary


class SummaryModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root / 'data')
        self.addCleanup(self.service.close)
        source = self.root / 'private-title.pdf'
        source.write_bytes(b'%PDF-1.7 test source')
        self.doc = self.service.import_pdf(source)
        self.pages = [dict(number=1, width=612, height=792, blocks=[
            dict(text='Authors observed twelve samples.', bbox=[.1, .1, .5, .2], order=0),
            dict(text='Unselected private content.', bbox=[.1, .3, .5, .4], order=1)])]
        self.revision = self.service.store.replace_document_ir(self.doc['id'], 'fixture/1', self.pages)
        self.blocks = self.service.document_blocks(self.doc['id'])
        self.service.save_settings({'endpoint': 'https://provider.test/v1', 'model': 'test-model'})
        self.service.set_session_key('private-test-key')
        self.claim = dict(text='Twelve samples were reported.', category='result', attribution='author_report',
                          evidence=[dict(blockId=self.blocks[0]['id'], quote='twelve samples')])

    def response(self, claims=None, finish='stop'):
        return io.BytesIO(json.dumps(dict(choices=[dict(finish_reason=finish,
            message=dict(content=json.dumps({'claims': [self.claim] if claims is None else claims})))],
            usage=dict(prompt_tokens=10, completion_tokens=12, total_tokens=22, provider_private='omit'))).encode())

    def invoke(self):
        # Pure adapter tests mock transport deliberately; actual worker/HTTP lifecycle
        # is exercised separately by test_summary_http, not claimed by these mocks.
        with patch('polyscholar.service.request_summary', side_effect=lambda *args, **kwargs: _http_summary(*args)):
            return self.service.summarize_document(self.doc['id'], [self.blocks[0]['id']], self.revision['id'])

    def test_selected_payload_and_model_provenance(self):
        with patch('polyscholar.summary_model.build_opener') as factory:
            factory.return_value.open.return_value = self.response()
            claims = self.invoke()
            request = factory.return_value.open.call_args.args[0]
            payload = json.loads(request.data)
            source = json.loads(payload['messages'][1]['content'])['sourceBlocks']
            self.assertEqual(source, [{'blockId': self.blocks[0]['id'], 'text': self.blocks[0]['text']}])
            self.assertNotIn('Unselected private content', request.data.decode())
            self.assertNotIn('private-title', request.data.decode())
            self.assertEqual(request.full_url, 'https://provider.test/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'], 'Bearer private-test-key')
            self.assertEqual(payload['response_format'], {'type': 'json_object'})
            self.assertFalse(payload['stream'])
            self.assertEqual(claims[0]['provenance']['source'], 'model')
            self.assertEqual(claims[0]['provenance']['model'], 'test-model')
            self.assertEqual(claims[0]['provenance']['category'], 'result')
            self.assertEqual(claims[0]['provenance']['usage'], {'prompt_tokens': 10, 'completion_tokens': 12, 'total_tokens': 22})
        with self.service.store.connection() as db:
            raw = '\n'.join(str(row) for row in db.execute('SELECT * FROM desktop_model_summaries'))
        self.assertNotIn('private-test-key', raw)
        self.assertNotIn('provider.test', raw)

    def test_invalid_second_claim_rolls_back_all_and_unselected_evidence_rejected(self):
        invalid = copy.deepcopy(self.claim)
        invalid['evidence'][0]['quote'] = 'fabricated'
        unselected = copy.deepcopy(self.claim)
        unselected['evidence'] = [dict(blockId=self.blocks[1]['id'], quote='private content')]
        for claims in ([self.claim, invalid], [unselected]):
            with patch('polyscholar.summary_model.build_opener') as factory:
                factory.return_value.open.return_value = self.response(claims)
                with self.assertRaises(ValueError):
                    self.invoke()
                self.assertEqual(self.service.list_claims(self.doc['id']), [])
                self.assertEqual(factory.return_value.open.call_count, 1)

    def test_partial_oversized_duplicate_json_and_errors_never_save(self):
        duplicate = self.response().getvalue().replace(b'"finish_reason": "stop"', b'"finish_reason": "length", "finish_reason": "stop"')
        for response in (self.response(finish='length'), io.BytesIO(b'x' * (MAX_RESPONSE_BYTES + 1)), io.BytesIO(duplicate), io.BytesIO(b'{broken')):
            with patch('polyscholar.summary_model.build_opener') as factory:
                factory.return_value.open.return_value = response
                with self.assertRaises(ValueError) as caught:
                    self.invoke()
                self.assertNotIn('private-test-key', str(caught.exception))
                self.assertEqual(self.service.list_claims(self.doc['id']), [])
        with patch('polyscholar.summary_model.build_opener') as factory:
            factory.return_value.open.side_effect = RuntimeError('private-test-key source body')
            with self.assertRaises(ValueError) as caught:
                self.invoke()
            self.assertNotIn('private-test-key', str(caught.exception))

    def test_selection_preflight_and_stale_revision_do_not_network(self):
        with patch('polyscholar.summary_model.build_opener') as factory:
            for ids in ([], [self.blocks[0]['id']] * 2, ['unknown'], 'all'):
                with self.assertRaises(ValueError):
                    self.service.summarize_document(self.doc['id'], ids, self.revision['id'])
            self.service.store.replace_document_ir(self.doc['id'], 'fixture/2', self.pages)
            with self.assertRaises(ValueError):
                self.invoke()
            factory.assert_not_called()
        with self.assertRaises(ValueError):
            selected_blocks([{'id': 'a', 'text': 'x' * (128 * 1024 + 1)}], ['a'])

    def test_reparse_during_request_prevents_save(self):
        def response(*args, **kwargs):
            self.service.store.replace_document_ir(self.doc['id'], 'fixture/2', self.pages)
            return self.response()
        with patch('polyscholar.summary_model.build_opener') as factory:
            factory.return_value.open.side_effect = response
            with self.assertRaises(ValueError):
                self.invoke()
        self.assertEqual(self.service.list_claims(self.doc['id']), [])

    def test_missing_evidence_is_explicit_and_invalid_attribution_rejected(self):
        claim = dict(self.claim, evidence=[], attribution='model_inference')
        with patch('polyscholar.summary_model.build_opener') as factory:
            factory.return_value.open.return_value = self.response([claim])
            saved = self.invoke()
        self.assertEqual(saved[0]['status'], 'insufficient_evidence')
        for bad in (dict(claim, category='invented'), dict(claim, attribution='scientifically_true')):
            with self.assertRaises(ValueError):
                validate_claims({'claims': [bad]}, self.blocks[:1])

    def test_changed_displayed_destination_and_missing_key_prevent_network(self):
        with patch('polyscholar.summary_model.build_opener') as factory:
            with self.assertRaises(ValueError):
                self.service.summarize_document(self.doc['id'], [self.blocks[0]['id']], self.revision['id'],
                    expected_settings={'endpoint': 'https://different.test', 'model': 'test-model', 'targetLanguage': 'zh'})
            self.service.set_session_key('')
            with self.assertRaises(ValueError):
                self.invoke()
            factory.assert_not_called()

    def test_close_during_network_never_persists(self):
        def response(*args, **kwargs):
            self.service.close()
            return self.response()
        with patch('polyscholar.summary_model.build_opener') as factory:
            factory.return_value.open.side_effect = response
            with self.assertRaises(ValueError) as caught:
                self.invoke()
        self.assertIn('关闭', str(caught.exception))
        from polyscholar.store import LocalStore
        store = LocalStore(self.root / 'data')
        self.addCleanup(store.close)
        self.assertEqual(store.list_claims(self.doc['id']), [])


if __name__ == '__main__':
    unittest.main()
