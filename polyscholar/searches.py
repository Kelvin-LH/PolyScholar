# SPDX-License-Identifier: AGPL-3.0-only
"""Local metadata rules and dynamic saved searches. 本地元数据规则与动态保存搜索。

Conditions match metadata only; full-text indexing lives in fulltext.py.
条件仅匹配元数据；全文索引由 fulltext.py 负责。
"""
from datetime import datetime, timezone
import json
import re
import uuid
from .metadata import normalize_metadata, creator_display
from .library import clean_name
from .validation import QUERY_TEXT_POLICY

SEARCH_FIELDS = ('title', 'creator', 'doi', 'year', 'itemType', 'publicationTitle', 'publisher', 'isbn', 'tag', 'notes')
TEXT_OPERATORS = ('contains', 'not_contains', 'is', 'is_not', 'is_empty', 'is_not_empty')
SEARCH_SCHEMA = '''CREATE TABLE IF NOT EXISTS desktop_saved_searches(
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, query TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);'''

def validate_query(query):
    if not isinstance(query, dict) or set(query) != {'match', 'conditions'} or query['match'] not in ('all', 'any'):
        raise ValueError('搜索规则必须选择全部或任一条件。')
    conditions = query['conditions']
    if not isinstance(conditions, list) or not 1 <= len(conditions) <= 20:
        raise ValueError('高级搜索需要 1–20 个条件。')
    normalized = []
    for condition in conditions:
        if not isinstance(condition, dict) or set(condition) != {'field', 'operator', 'value'}:
            raise ValueError('搜索条件字段无效。')
        field, operator, value = (condition[k] for k in ('field', 'operator', 'value'))
        if field not in SEARCH_FIELDS or operator not in TEXT_OPERATORS + (('before', 'after') if field == 'year' else ()):
            raise ValueError('搜索字段或操作无效。')
        value = QUERY_TEXT_POLICY.validate(value, '每个搜索值必须是最多 4 KiB 的文本。')
        if operator in ('is_empty', 'is_not_empty'):
            if value:
                raise ValueError('空值条件不能填写搜索值。')
        elif not value:
            raise ValueError('搜索值不能为空。')
        if operator in ('before', 'after') and (not re.fullmatch(r'[0-9]{1,4}', value) or not 1 <= int(value) <= 9999):
            raise ValueError('比较年份必须是 1–9999 的 ASCII 数字。')
        normalized.append(dict(field=field, operator=operator, value=value))
    return dict(match=query['match'], conditions=normalized)

def _values(document, field):
    if field == 'creator':
        creators = normalize_metadata(document)['creators']
        return [value for creator in creators for value in (creator_display(creator), creator.get('literal',''), creator.get('family',''), creator.get('given',''))]
    if field == 'tag':
        return document.get('tags', [])
    return [normalize_metadata(document).get(field, '')]

def matches_query(document, query):
    outcomes = []
    for condition in query['conditions']:
        values = [str(value).strip().casefold() for value in _values(document, condition['field']) if str(value).strip()]
        needle, operator = condition['value'].casefold(), condition['operator']
        if operator in ('is_empty', 'is_not_empty'):
            result = not values if operator == 'is_empty' else bool(values)
        elif operator in ('before', 'after'):
            numeric = [int(value) for value in values if re.fullmatch(r'[0-9]{1,4}', value) and 1 <= int(value) <= 9999]
            result = any(value < int(needle) if operator == 'before' else value > int(needle) for value in numeric)
        else:
            positive = any(needle in value if operator in ('contains', 'not_contains') else needle == value for value in values)
            # A negative creator/tag rule must reject every matching value.
            # 作者/标签否定条件要求所有独立值均不匹配，不能因另一值不匹配而通过。
            result = not positive if operator in ('not_contains', 'is_not') else positive
        outcomes.append(result)
    return all(outcomes) if query['match'] == 'all' else any(outcomes)

class SearchLibrary:
    def search_documents(self, text='', collection_id=None, unfiled=False, tags=None, include_descendants=False, query=None):
        rules = validate_query(query) if query is not None else None
        documents = super().search_documents(text=text, collection_id=collection_id, unfiled=unfiled, tags=tags, include_descendants=include_descendants)
        return [document for document in documents if matches_query(document, rules)] if rules else documents

    def list_saved_searches(self):
        with self.connection() as db:
            return [dict(id=row[0], name=row[1], query=validate_query(json.loads(row[2])), createdAt=row[3], updatedAt=row[4])
                    for row in db.execute('SELECT id,name,query,created_at,updated_at FROM desktop_saved_searches ORDER BY name,id')]

    def save_saved_search(self, name, query, search_id=None):
        name = clean_name(name, '搜索名称')
        if len(name.splitlines()) != 1 or any(ord(char) == 127 for char in name):
            raise ValueError('搜索名称必须是单行文本。')
        query = validate_query(query)
        if search_id is not None and (not isinstance(search_id, str) or not search_id):
            raise ValueError('保存搜索标识无效。')
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT created_at FROM desktop_saved_searches WHERE id=?', (search_id,)).fetchone() if search_id else None
            if search_id and not previous:
                raise ValueError('保存搜索不存在。')
            if any(identifier != search_id and existing.casefold() == name.casefold()
                   for identifier, existing in db.execute('SELECT id,name FROM desktop_saved_searches')):
                raise ValueError('已存在同名保存搜索。')
            identifier = search_id or str(uuid.uuid4())
            created = previous[0] if previous else now
            db.execute('INSERT INTO desktop_saved_searches(id,name,query,created_at,updated_at) VALUES(?,?,?,?,?) '
                       'ON CONFLICT(id) DO UPDATE SET name=excluded.name,query=excluded.query,updated_at=excluded.updated_at',
                       (identifier, name, json.dumps(query, ensure_ascii=False), created, now))
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',
                       ('saved_search_updated' if previous else 'saved_search_created', 'succeeded', now))
        return dict(id=identifier, name=name, query=query, createdAt=created, updatedAt=now)

    def delete_saved_search(self, search_id):
        if not isinstance(search_id, str) or not search_id:
            raise ValueError('保存搜索标识无效。')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('DELETE FROM desktop_saved_searches WHERE id=?', (search_id,)).rowcount != 1:
                raise ValueError('保存搜索不存在。')
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',
                       ('saved_search_deleted', 'succeeded', datetime.now(timezone.utc).isoformat()))
