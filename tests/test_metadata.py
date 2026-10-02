# SPDX-License-Identifier: AGPL-3.0-only
import json
from pathlib import Path
import tempfile
import unittest
from polyscholar.metadata import normalize_metadata, retained_metadata_fields
from polyscholar.service import LocalService

class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = LocalService(self.root/'data', self.root/'resources')
        source = self.root/'private-file.pdf'
        source.write_bytes(b'%PDF-1.7 metadata-fixture')
        self.doc = self.service.import_pdf(source)
    def tearDown(self):
        self.service.close()
        self.temp.cleanup()
    def update(self, **patch):
        return self.service.update_document(self.doc['id'], patch)
    def csl(self):
        return json.loads(self.service.format_metadata(self.doc['id'], 'csl-json'))[0]

    def test_four_types_export_semantic_fields_and_partial_dates(self):
        cases = [
            ('article-journal', dict(publicationTitle='Journal', volume='2', issue='3', pages='15–27'), 'article', 'JOUR'),
            ('paper-conference', dict(publicationTitle='Proceedings', eventTitle='Conference', publisher='Press', pages='30-42'), 'inproceedings', 'CPAPER'),
            ('book', dict(publisher='Book Press', place='Melbourne', isbn='978000', edition='2'), 'book', 'BOOK'),
            ('thesis', dict(institution='University', thesisType='Master thesis'), 'misc', 'THES'),
        ]
        for kind, fields, bibtype, ristype in cases:
            with self.subTest(kind=kind):
                self.update(itemType=kind, title='Research', date='2024-02', **fields)
                item = self.csl()
                self.assertEqual(item['type'], kind)
                self.assertEqual(item['issued']['date-parts'], [[2024,2]])
                self.assertIn('@'+bibtype+'{', self.service.format_metadata(self.doc['id'], 'bibtex'))
                ris = self.service.format_metadata(self.doc['id'], 'ris')
                self.assertTrue(ris.startswith('TY  - '+ristype+'\n'))
                self.assertIn('DA  - 2024/02\n', ris)
                if kind in ('article-journal','paper-conference'):
                    self.assertEqual(item['container-title'], fields['publicationTitle'])
                    self.assertIn('EP  - ', ris)
                if kind == 'thesis':
                    self.assertEqual(item['publisher'], 'University')
                    self.assertEqual(item['genre'], 'Master thesis')
                    self.assertNotIn('container-title', item)

    def test_ordered_person_organization_and_editor_without_guessing(self):
        creators = [dict(role='author', type='person', family='van Doe', given='Jane'),
                    dict(role='author', type='organization', literal='Research and Development {Group}'),
                    dict(role='editor', type='person', literal='Unparsed Editor')]
        doc = self.update(creators=creators, title='A% & B_$#^~\\{}')
        self.assertEqual(doc['authors'], 'Jane van Doe; Research and Development {Group}')
        item = self.csl()
        self.assertEqual(item['author'], [{'family':'van Doe','given':'Jane'}, {'literal':'Research and Development {Group}'}])
        self.assertEqual(item['editor'], [{'literal':'Unparsed Editor'}])
        bib = self.service.format_metadata(self.doc['id'], 'bibtex')
        self.assertIn('{van Doe}, {Jane}', bib)
        self.assertIn('{Research and Development \\{Group\\}}', bib)
        self.assertIn('editor = {{Unparsed Editor}}', bib)
        self.assertIn('\\%', bib)
        ris = self.service.format_metadata(self.doc['id'], 'ris')
        self.assertIn('AU  - van Doe, Jane', ris)
        self.assertIn('A2  - Unparsed Editor', ris)

    def test_old_json_normalizes_without_migration_or_name_inference(self):
        old = self.update(authors='Doe, Jane; 李明', year='2020')
        with self.service.store.connection() as db:
            for field in ('itemType','creators','date'):
                old.pop(field, None)
            db.execute('UPDATE desktop_documents SET data=? WHERE id=?', (json.dumps(old), self.doc['id']))
        normalized = normalize_metadata(self.service.store.document(self.doc['id']))
        self.assertEqual(normalized['creators'][0]['literal'], 'Doe, Jane')
        self.assertEqual(self.csl()['issued']['date-parts'], [[2020]])
        self.assertEqual(self.csl()['author'], [{'literal':'Doe, Jane'}, {'literal':'李明'}])
        self.update(notes='Keep legacy names')
        self.assertEqual(self.service.store.document(self.doc['id'])['authors'], 'Doe, Jane; 李明')

    def test_type_switch_retains_fields_but_exports_only_applicable(self):
        self.update(itemType='article-journal', publicationTitle='Journal', volume='8', issue='2', pages='1-9')
        switched = self.update(itemType='thesis', institution='Uni')
        self.assertEqual(switched['publicationTitle'], 'Journal')
        self.assertIn('publicationTitle', retained_metadata_fields(switched))
        for format in ('csl-json','bibtex','ris'):
            self.assertNotIn('Journal', self.service.format_metadata(self.doc['id'], format))
        self.update(itemType='article-journal')
        self.assertEqual(self.csl()['container-title'], 'Journal')

    def test_invalid_metadata_rejected_atomically(self):
        for patch in [dict(itemType='movie', title='Changed'), dict(date='2024-02-30'), dict(date='2024-13'),
                      dict(date='0000'), dict(date='2024-2'), dict(date='2024',year='2023'),
                      dict(creators=[dict(type='organization', literal='Org',family='Wrong')]),
                      dict(creators=[dict(type='person',literal='Person',given='Guess')]),
                      dict(creators=[dict(role='translator',literal='P')]), dict(publisher='X'*65537)]:
            before = self.service.store.document(self.doc['id'])
            with self.subTest(patch=list(patch)):
                with self.assertRaises(ValueError):
                    self.service.update_document(self.doc['id'],patch)
                self.assertEqual(self.service.store.document(self.doc['id']),before)
        self.update(date='2024-02-29')
        with self.assertRaises(ValueError):
            self.update(year='2020')

    def test_preview_is_export_and_private_paths_never_exchange(self):
        self.update(title='Public Title', creators=[dict(type='person',family='Doe',given='J')])
        for format in ('csl-json','bibtex','ris'):
            preview = self.service.format_metadata(self.doc['id'],format)
            target = self.root/('out.'+format)
            self.service.export_metadata(self.doc['id'],format,target)
            self.assertEqual(target.read_text(),preview)
            self.assertNotIn(str(self.root),preview)
            self.assertNotIn('private-file.pdf',preview)
        childsource=self.root/'child.pdf'
        childsource.write_bytes(b'%PDF-1.7 different fixture')
        child = self.service.import_attachment(self.doc['id'],childsource)
        with self.assertRaisesRegex(ValueError,'所属文献'):
            self.service.format_metadata(child['id'],'ris')

if __name__ == '__main__':
    unittest.main()
