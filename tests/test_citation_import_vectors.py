# SPDX-License-Identifier: AGPL-3.0-only
"""Independent citation-import vectors / 独立本地引文导入向量。"""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
from polyscholar.service import LocalService
from polyscholar.citation_import import CitationImportPolicy


def partial_frame_parser(text, format, connection):
    """发送 IPC 包头后停滞，验证接收也受时限监督。 / Stall after an IPC header to check receive supervision."""
    os.write(connection.fileno(), struct.pack('!i',4096)+b'{')
    Path(json.loads(text)[0]['title']).write_text('header sent',encoding='utf-8')
    time.sleep(10)
    connection.close()


class CitationImportVectors(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root/'data')

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def preview(self, text, kind='csl-json'):
        path = self.root/('input.'+{'csl-json':'json','bibtex':'bib','ris':'ris'}[kind])
        path.write_text(text, encoding='utf-8')
        return self.service.preview_metadata_import(path, kind)

    def csl(self, **extra):
        return dict(id='external-key', type='article-journal', title='中文 Paper',
                    author=[{'family':'Li','given':'Ming'}, {'literal':'WHO'}],
                    issued={'date-parts':[[2024,2,29]]}, **extra)

    def test_csl_types_creators_losses_and_subset_create_actual_null_identity(self):
        items = [self.csl(**{'container-title':'Journal','volume':'12','issue':'2','page':'1–9',
                             'DOI':'10.1000/example','URL':'https://invalid.example/paper',
                             'attachments':[{'path':'file:///no-access.pdf'}]}),
                 {'id':'external-key','type':'book','title':'Book','publisher':'Press'},
                 {'id':'external-key','type':'webpage','title':'Unsupported'}]
        preview = self.preview(json.dumps(items, ensure_ascii=False))
        self.assertEqual(preview['total'],3)
        self.assertEqual(preview['validCount'],2)
        self.assertFalse(preview['items'][2]['valid'])
        first = preview['items'][0]
        self.assertTrue(first['warnings'])
        self.assertEqual(first['metadata']['date'],'2024-02-29')
        self.assertEqual(first['metadata']['creators'][0]['family'],'Li')
        self.assertEqual(first['metadata']['creators'][1]['literal'],'WHO')
        collection = self.service.create_collection('Imported')
        result = self.service.import_metadata_preview(preview,[0,1],collection['id'])
        self.assertEqual(result['importedCount'],2)
        self.assertEqual(len(set(result['documentIds'])),2)
        self.assertNotIn('external-key', result['documentIds'])
        for identifier in result['documentIds']:
            doc = self.service.store.document(identifier)
            self.assertEqual(doc['fileKind'],'bibliographic')
            self.assertIsNone(doc['sha256'])
            self.assertEqual(doc['sourcePaths'],[])
            self.assertEqual(self.service.list_attachments(identifier),[])
            self.assertEqual(self.service.document_collections(identifier),[collection['id']])
        self.assertEqual(list(self.service.store.objects.iterdir()),[])

    def test_bibtex_nested_names_unknown_fields_and_ris_order(self):
        bib = r'''@article{same, title={A {Nested} Title},
          author={Doe, Jane and {Research and Development Group}},
          journal={Journal}, year={2024}, volume={8}, pages={10--20},
          url={https://invalid.example}, file={/private/not-read.pdf}}
        @book{same, title={Book}, publisher={Press}, year={2023}}
        '''
        preview = self.preview(bib,'bibtex')
        self.assertEqual(preview['validCount'],2)
        meta = preview['items'][0]['metadata']
        self.assertEqual(meta['creators'][0]['family'],'Doe')
        self.assertEqual(meta['creators'][0]['given'],'Jane')
        self.assertEqual(len(meta['creators']),2)
        self.assertTrue(preview['items'][0]['warnings'])
        result = self.service.import_metadata_preview(preview,[0,1])
        self.assertEqual(len(set(result['documentIds'])),2)
        ris = ('TY  - CPAPER\nTI  - Conference\nAU  - Doe, Jane\nAU  - Lee, Alex\n'
               'A2  - Editor, Pat\nT2  - Proceedings\nPY  - 2024\nDA  - 2024/02/29\n'
               'SP  - 10\nEP  - 20\nUR  - https://invalid.example\nER  -\n')
        second = self.preview(ris,'ris')
        self.assertEqual(second['validCount'],1)
        converted = second['items'][0]['metadata']
        self.assertEqual(converted['itemType'],'paper-conference')
        self.assertEqual(converted['date'],'2024-02-29')
        self.assertEqual([c['family'] for c in converted['creators']],['Doe','Lee','Editor'])
        self.assertEqual(converted['creators'][2]['role'],'editor')
        self.assertEqual(converted['pages'],'10-20')

    def test_tampered_preview_changed_source_and_consumed_token_never_write(self):
        preview = self.preview(json.dumps([self.csl()]))
        tampered = deepcopy(preview)
        tampered['items'][0]['metadata']['title']='Injected'
        with self.assertRaises(ValueError):
            self.service.import_metadata_preview(tampered,[0])
        (self.root/'input.json').write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.service.import_metadata_preview(preview,[0])
        self.assertEqual(self.service.list_documents(),[])
        preview = self.preview(json.dumps([self.csl()]))
        self.service.import_metadata_preview(preview,[0])
        with self.assertRaises(ValueError):
            self.service.import_metadata_preview(preview,[0])
        self.assertEqual(len(self.service.list_documents()),1)

    def test_ris_author_aliases_keep_actual_source_order(self):
        preview = self.preview('TY  - JOUR\nTI  - Mixed tags\nAU  - Doe, Jane\n'
                               'A1  - Smith, Pat\nAU  - Lee, Alex\nER  -\n','ris')
        self.assertEqual([c['family'] for c in preview['items'][0]['metadata']['creators']],
                         ['Doe','Smith','Lee'])

    def test_invalid_selection_and_audit_failure_are_atomic_and_retryable(self):
        preview = self.preview(json.dumps([self.csl(),{'type':'book','title':'Another'}]))
        for selection in ([True], [0,0], [2], [], [0,'1']):
            with self.assertRaises(ValueError):
                self.service.import_metadata_preview(preview,selection)
        store = self.service.store
        with store.connection() as db:
            db.execute("CREATE TRIGGER reject_import BEFORE INSERT ON desktop_audit WHEN NEW.point='citation_imported' BEGIN SELECT RAISE(ABORT,'Injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.import_metadata_preview(preview,[0,1])
        self.assertEqual(self.service.list_documents(),[])
        with store.connection() as db:
            db.execute('DROP TRIGGER reject_import')
        self.assertEqual(self.service.import_metadata_preview(preview,[0,1])['importedCount'],2)

    def test_malformed_framing_duplicate_json_and_invalid_date_are_not_silently_lost(self):
        with self.assertRaises(ValueError):
            self.preview('TY  - JOUR\nTI  - Unterminated\n','ris')
        with self.assertRaises(ValueError):
            self.preview('[{"type":"book","title":"first","title":"second"}]')
        preview = self.preview(json.dumps([{'type':'article-journal','title':'Bad date',
                                           'issued':{'date-parts':[[2023,2,29]]}}]))
        self.assertFalse(preview['items'][0]['valid'])
        with self.assertRaises(ValueError):
            self.service.import_metadata_preview(preview,[0])
        self.assertEqual(self.service.list_documents(),[])

    def test_record_limit_encoding_and_latex_content_never_become_executable(self):
        with self.assertRaises(ValueError):
            self.preview(json.dumps([{'type':'book','title':'Book'}]*1001))
        path = self.root/'wrong.json'
        path.write_bytes(b'\xff\xfeInvalid')
        with self.assertRaises(ValueError):
            self.service.preview_metadata_import(path,'csl-json')
        command = r'\write18{touch /tmp/polyscholar-import-executed}'
        preview = self.preview('@article{local,title={'+command+'},year={2024}}','bibtex')
        self.assertEqual(preview['validCount'],1)
        self.assertIn('write18',preview['items'][0]['metadata']['title'])
        self.assertTrue(preview['items'][0]['warnings'])
        # 只验证实际书目文本/无对象创建；命令字符串从未交给 shell 或 TeX。
        # Verify the actual record text and absence of objects; never pass the command to a shell or TeX.
        result = self.service.import_metadata_preview(preview,[0])
        self.assertIn('write18',self.service.store.document(result['documentIds'][0])['title'])
        self.assertEqual(list(self.service.store.objects.iterdir()),[])

    def test_own_four_type_exports_are_importable_without_guessing_degree_or_names(self):
        for kind in ('article-journal','paper-conference','book','thesis'):
            original = self.service.create_bibliographic_item({
                'title':'Local {Model & Data}', 'itemType':kind, 'date':'2024-02-29',
                'creators':[{'role':'author','type':'person','family':'Doe','given':'Jane'},
                            {'role':'editor','type':'person','family':'Lee','given':'Alex'}],
                'institution':'University', 'thesisType':'Master of Science',
            })
            for format in ('csl-json','bibtex','ris'):
                with self.subTest(kind=kind,format=format):
                    text = self.service.format_metadata(original['id'],format)
                    preview = self.preview(text,format)
                    self.assertEqual(preview['validCount'],1)
                    meta = preview['items'][0]['metadata']
                    self.assertEqual(meta['itemType'],kind)
                    self.assertEqual(meta['title'],original['title'])
                    self.assertEqual(meta['date'],'2024-02-29')
                    self.assertEqual(meta['creators'][0]['family'],'Doe')
                    self.assertEqual(meta['creators'][0]['given'],'Jane')
                    if kind == 'thesis':
                        self.assertEqual(meta['thesisType'],'Master of Science')
                        self.assertEqual(meta['institution'],'University')

    @unittest.skipUnless(os.name=='posix','Partial raw pipe frame fixture uses a POSIX descriptor')
    def test_partial_ipc_frame_is_terminated_within_total_budget(self):
        marker = self.root/'sent-header.txt'
        source = self.root/'partial.json'
        source.write_text(json.dumps([{'type':'book','title':str(marker)}]),encoding='utf-8')
        with patch('polyscholar.citation_import._parse_worker',partial_frame_parser), \
                patch.object(CitationImportPolicy,'parse_timeout_seconds',1):
            started = time.monotonic()
            with self.assertRaises(ValueError):
                self.service.preview_metadata_import(source,'csl-json')
            elapsed = time.monotonic()-started
        self.assertTrue(marker.exists(),'Fixture must reach the partial-frame receive path')
        self.assertLess(elapsed,3)
        self.assertFalse(self.service._citation_importer._workers)
        self.assertEqual(self.service.list_documents(),[])


if __name__ == '__main__':
    unittest.main()
