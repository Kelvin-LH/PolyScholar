# SPDX-License-Identifier: AGPL-3.0-only
"""Independent multi-value predicate and literal-query vectors."""
from pathlib import Path
import tempfile
import unittest
from polyscholar.service import LocalService


class SearchVectorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = LocalService(self.root / 'library'); self.addCleanup(self.service.close)
        self.ids = []
        for index, patch in enumerate([
            dict(title='100% research_α', authors='Alice; Bob', year='2024', tags=['method', 'baseline']),
            dict(title='Research β', authors='Carol', year='2020', tags=['baseline']),
            dict(title='Unknown year', authors='', year='unknown', tags=[]),
        ]):
            source = self.root / f'{index}.pdf'; source.write_bytes(f'%PDF-1.7 vector {index}'.encode())
            doc = self.service.import_pdf(source); self.ids.append(doc['id'])
            self.service.update_document(doc['id'], patch)

    def search(self, field, operator, value='', match='all', extra=()):
        query = dict(match=match, conditions=[dict(field=field, operator=operator, value=value), *extra])
        return {doc['id'] for doc in self.service.search_documents(query=query)}

    def test_negative_multi_value_predicates_and_unknown_year(self):
        first, second, empty = self.ids
        self.assertEqual(self.search('creator', 'is_not', 'Alice'), {second, empty})
        self.assertEqual(self.search('creator', 'not_contains', 'boB'), {second, empty})
        self.assertEqual(self.search('tag', 'is_not', 'method'), {second, empty})
        self.assertEqual(self.search('year', 'after', '2021'), {first})
        self.assertEqual(self.search('year', 'before', '2021'), {second})
        self.assertEqual(self.search('creator', 'is_empty'), {empty})

    def test_unicode_and_sql_like_characters_remain_literal(self):
        first, second, _ = self.ids
        self.assertEqual(self.search('title', 'contains', 'Α'), {first})
        self.assertEqual(self.search('title', 'contains', '%'), {first})
        self.assertEqual(self.search('title', 'contains', "' OR 1=1 --"), set())
        self.assertEqual(self.search('title', 'contains', 'Research', extra=[dict(field='year', operator='before', value='2021')]), {second})
        self.assertEqual(self.search('title', 'contains', '%', match='any', extra=[dict(field='year', operator='before', value='2021')]), {first, second})

    def test_saved_rules_have_no_snapshot_or_scope_and_failure_is_atomic(self):
        query = dict(match='all', conditions=[dict(field='tag', operator='is', value='method')])
        saved = self.service.save_saved_search('Methods', query)
        self.service.update_document(self.ids[1], dict(tags=['method']))
        self.assertEqual({d['id'] for d in self.service.search_documents(query=saved['query'])}, set(self.ids[:2]))
        invalid = dict(query, documentIds=self.ids)
        with self.assertRaises(ValueError):
            self.service.save_saved_search('Overwritten', invalid, search_id=saved['id'])
        self.assertEqual(self.service.list_saved_searches(), [saved])
        self.service.close()
        self.service = LocalService(self.root / 'library'); self.addCleanup(self.service.close)
        self.assertEqual(self.service.list_saved_searches(), [saved])
        self.assertEqual({d['id'] for d in self.service.search_documents(query=saved['query'])}, set(self.ids[:2]))
