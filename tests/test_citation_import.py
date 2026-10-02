# SPDX-License-Identifier: AGPL-3.0-only
"""Real parser, atomic import and spawned lifecycle checks / 真实解析与原子导入检查。"""
from copy import deepcopy
import json
import os
import struct
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from polyscholar.service import LocalService
from polyscholar.citation_import import CitationImporter, CitationImportPolicy


def delayed_parser(text, format, connection):
    time.sleep(10)
    connection.close()


def partial_frame_parser(text, format, connection):
    os.write(connection.fileno(), struct.pack('!i',4096)+b'{')
    time.sleep(10)
    connection.close()


class CitationImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root/'data')

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def preview(self, text, format):
        path = self.root/('sources.'+format)
        path.write_text(text,encoding='utf-8')
        return self.service.preview_metadata_import(path,format)

    def test_real_parsers_four_types_names_unknown_fields_and_safe_urls(self):
        sources = {
            'bibtex': '@article{a,title={Paper},author={{Doe}, {Jane} and {Research and Development}},year={2024},date={2024-03},journal={Journal},url={file:///private/file.pdf}}\n'
                      '@inproceedings{b,title={Conference},booktitle={Proceedings}}\n'
                      '@book{c,title={Book},isbn={9780306406157},publisher={Publisher}}\n'
                      '@misc{d,title={Thesis},polyscholaritemtype={thesis},type={Master of Science},school={University}}',
            'ris': 'TY  - JOUR\nTI  - Paper\nAU  - Doe, Jane\nAU  - Research and Development\nPY  - 2024\nDA  - 2024/03\nT2  - Journal\nUR  - file:///private/file.pdf\nER  -\n'
                   'TY  - CPAPER\nTI  - Conference\nT2  - Proceedings\nER  -\n'
                   'TY  - BOOK\nTI  - Book\nSN  - 9780306406157\nPB  - Publisher\nER  -\n'
                   'TY  - THES\nTI  - Thesis\nPB  - University\nM3  - Master of Science\nER  -\n',
            'csl-json': json.dumps([
                dict(id='external-id',type='article-journal',title='Paper',author=[dict(family='Doe',given='Jane'),dict(literal='Research and Development')],issued={'date-parts':[[2024,3]]},URL='file:///private/file.pdf'),
                dict(type='paper-conference',title='Conference',**{'container-title':'Proceedings'}),
                dict(type='book',title='Book',ISBN='9780306406157',publisher='Publisher'),
                dict(type='thesis',title='Thesis',publisher='University',genre='Master of Science')]),
        }
        for format, source in sources.items():
            with self.subTest(format=format):
                preview = self.preview(source,format)
                self.assertEqual((preview['total'],preview['validCount']),(4,4))
                article = preview['items'][0]
                self.assertEqual(article['metadata']['creators'][0]['family'],'Doe')
                self.assertEqual(article['metadata']['creators'][1]['literal'],'Research and Development')
                self.assertTrue(article['warnings'])
                self.assertEqual(article['metadata']['date'],'2024-03')
                self.assertEqual(preview['items'][3]['metadata']['institution'],'University')
                result = self.service.import_metadata_preview(preview,[0,1,2,3])
                self.assertEqual(result['importedCount'],4)
                self.assertNotIn('external-id',result['documentIds'])
                self.assertTrue(all(document['sha256'] is None for document in self.service.list_documents()))
                self.assertEqual(list(self.service.store.objects.iterdir()),[])
                with self.assertRaises(ValueError):self.service.import_metadata_preview(preview,[0])

    def test_invalid_rows_syntax_and_no_silent_partial_records(self):
        for format,text in (
            ('bibtex','@article{a,title={Valid}} @online{b,title={Unsupported}}'),
            ('ris','TY  - JOUR\nTI  - Valid\nER  -\nTY  - ELEC\nTI  - Unsupported\nER  -\n'),
            ('csl-json','[{"type":"article-journal","title":"Valid"},{"type":"webpage","title":"Unsupported"}]')):
            preview=self.preview(text,format)
            self.assertEqual((preview['validCount'],preview['invalidCount']),(1,1))
            self.assertEqual(preview['items'][1]['title'],'Unsupported')
            with self.assertRaises(ValueError):self.service.import_metadata_preview(preview,[1])
        for format,text in (
            ('bibtex','@article{a,title={Good}} @article{b,title={Broken}'),
            ('bibtex','@article{a,title={First},TITLE={Second}}'),
            ('ris','TY  - JOUR\nTI  - Missing terminator\n'),
            ('csl-json','[{"type":"book","title":"A","title":"B"}]'),
            ('csl-json','[{"type":"book","title":"A","volume":NaN}]'),
            ('csl-json','[{"type":"book","title":"A","volume":1e999}]')):
            with self.assertRaises(ValueError):self.preview(text,format)
        self.assertEqual(self.service.list_documents(),[])

    def test_alias_conflicts_dates_macros_and_escaped_bibtex(self):
        for format,text in (
            ('bibtex','@article{a,title={Title},journal={A},booktitle={B}}'),
            ('bibtex','@article{a,title={Title},year={2020},date={2021}}'),
            ('bibtex','@article{a,title={Title},date={2024---}}'),
            ('ris','TY  - BOOK\nTI  - Title\nDA  - 2024///\nER  -\n'),
            ('ris','TY  - BOOK\nTI  - One\nT1  - Two\nER  -\n'),
            ('csl-json','[{"type":"book","title":"Title","issued":{"date-parts":[[2020,2,30]]}}]')):
            preview=self.preview(text,format)
            self.assertEqual(preview['invalidCount'],1)
        preview=self.preview('@string{large={Repeated}}\n@article{a,title=large # large}','bibtex')
        self.assertEqual(preview['invalidCount'],1)
        self.assertTrue(preview['warnings'])
        preview=self.preview(r'@book{a,title={A \& B \textasciitilde{}},author={{Doe}, {Jane}}}','bibtex')
        self.assertEqual(preview['items'][0]['metadata']['title'],'A & B ~')
        self.assertEqual(preview['items'][0]['metadata']['creators'][0]['given'],'Jane')

    def test_preview_mutation_source_drift_selection_and_cache_eviction(self):
        preview=self.preview('[{"id":"target","type":"book","title":"Original"}]','csl-json')
        forged=deepcopy(preview);forged['items'][0]['metadata']['title']='Forged'
        with self.assertRaises(ValueError):self.service.import_metadata_preview(forged,[0])
        for indices in ([],[True],[0,0],[-1],[1],{0}):
            with self.assertRaises(ValueError):self.service.import_metadata_preview(preview,indices)
        (self.root/'sources.csl-json').write_text('[{"type":"book","title":"Changed"}]')
        with self.assertRaisesRegex(ValueError,'变化'):self.service.import_metadata_preview(preview,[0])
        with patch.object(CitationImportPolicy,'max_cached_previews',1):
            self.preview('[{"type":"book","title":"Second"}]','csl-json')
        with self.assertRaisesRegex(ValueError,'失效'):self.service.import_metadata_preview(preview,[0])
        self.assertEqual(self.service.list_documents(),[])

    def test_batch_collection_and_audit_failure_roll_back_all_items(self):
        preview=self.preview('[{"type":"book","title":"One"},{"type":"book","title":"Two"}]','csl-json')
        with self.assertRaises(ValueError):self.service.import_metadata_preview(preview,[0,1],'missing')
        collection=self.service.create_collection('Target')
        with self.service.store.connection() as db:
            db.execute("CREATE TRIGGER fail_import BEFORE INSERT ON desktop_audit WHEN NEW.point='citation_imported' BEGIN SELECT RAISE(ABORT,'fail');END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.import_metadata_preview(preview,[0,1],collection['id'])
        self.assertEqual(self.service.list_documents(),[])
        with self.service.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM desktop_memberships').fetchone()[0],0)
            db.execute('DROP TRIGGER fail_import')
        result=self.service.import_metadata_preview(preview,[0,1],collection['id'])
        for identifier in result['documentIds']:
            self.assertEqual(self.service.document_collections(identifier),[collection['id']])
        self.assertEqual(result['collectionId'],collection['id'])

    def test_size_encoding_and_record_limits_do_not_truncate(self):
        source=self.root/'oversize.json'
        source.write_bytes(b'x'*(CitationImportPolicy.max_file_bytes+1))
        with self.assertRaisesRegex(ValueError,'8 MiB'):self.service.preview_metadata_import(source,'csl-json')
        source.write_bytes(b'\xff')
        with self.assertRaisesRegex(ValueError,'UTF-8'):self.service.preview_metadata_import(source,'csl-json')
        source.write_bytes(b'\xef\xbb\xbf[{"type":"book","title":"BOM"}]')
        self.assertEqual(self.service.preview_metadata_import(source,'csl-json')['validCount'],1)
        source.write_text(json.dumps([dict(type='book',title='T')]*1001))
        with self.assertRaises(ValueError):self.service.preview_metadata_import(source,'csl-json')
        self.assertEqual(self.service.list_documents(),[])

    def test_real_spawn_timeout_and_close_release_workers(self):
        importer=CitationImporter()
        source=self.root/'one.json';source.write_text('[{"type":"book","title":"One"}]')
        with patch('polyscholar.citation_import._parse_worker',delayed_parser), patch.object(CitationImportPolicy,'parse_timeout_seconds',0.2):
            started=time.monotonic()
            with self.assertRaisesRegex(ValueError,'30 秒'):importer.preview(source,'csl-json')
            self.assertLess(time.monotonic()-started,3)
        failures=[]
        def parse():
            try:importer.preview(source,'csl-json')
            except ValueError as error:failures.append(str(error))
        with patch('polyscholar.citation_import._parse_worker',delayed_parser):
            thread=threading.Thread(target=parse);thread.start()
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                with importer._lock:
                    if importer._workers:break
                time.sleep(0.01)
            importer.close();thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(failures)
        self.assertEqual(importer._workers,set())
        self.assertEqual(importer._previews,{})

    @unittest.skipUnless(os.name == 'posix','Raw pipe frame test uses POSIX descriptors.')
    def test_real_spawn_incomplete_frame_cannot_outlive_deadline(self):
        importer = CitationImporter()
        source = self.root/'frame.json'
        source.write_text('[{"type":"book","title":"One"}]')
        with patch('polyscholar.citation_import._parse_worker',partial_frame_parser), patch.object(CitationImportPolicy,'parse_timeout_seconds',0.4):
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError,'30 秒'):
                importer.preview(source,'csl-json')
            self.assertLess(time.monotonic()-started,3)
        self.assertEqual(importer._workers,set())
        self.assertEqual(importer._previews,{})
        importer.close()

    def test_deep_bibtex_and_unexpanded_macro_chain_remain_bounded(self):
        started = time.monotonic()
        with self.assertRaises(ValueError):
            self.preview('@book{a,title={'+'{'*2000+'Private text'+'}'*2000+'}}','bibtex')
        macros = ['@string{m0={x}}']
        macros += ['@string{m'+str(i)+'=m'+str(i-1)+' # m'+str(i-1)+'}' for i in range(1,25)]
        preview = self.preview('\n'.join(macros)+ '\n@book{a,title=m24}','bibtex')
        self.assertEqual(preview['invalidCount'],1)
        self.assertTrue(preview['warnings'])
        self.assertLess(time.monotonic()-started,5)
        self.assertEqual(self.service.store.list_documents(),[])

    def test_only_ris_allows_empty_slash_precision(self):
        for date, expected in [('2024//','2024'),('2024/02/','2024-02')]:
            preview = self.preview('TY  - BOOK\nTI  - T\nDA  - '+date+'\nER  -\n','ris')
            self.assertEqual(preview['items'][0]['metadata']['date'],expected)
        preview = self.preview('[{"type":"book","title":"T","issued":{"raw":"2024-"}}]','csl-json')
        self.assertEqual(preview['invalidCount'],1)

    def test_ris_author_aliases_and_editors_keep_source_order(self):
        preview = self.preview('TY  - JOUR\nTI  - Mixed tags\n'
            'AU  - Doe, Jane\nA2  - Editor, One\nA1  - Smith, Pat\n'
            'AU  - Lee, Alex\nA2  - Editor, Two\nER  -\n','ris')
        creators = preview['items'][0]['metadata']['creators']
        self.assertEqual([creator['family'] for creator in creators if creator['role']=='author'],
                         ['Doe','Smith','Lee'])
        self.assertEqual([creator['given'] for creator in creators if creator['role']=='editor'],
                         ['One','Two'])
