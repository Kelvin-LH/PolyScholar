# SPDX-License-Identifier: AGPL-3.0-only
"""Local collection relationships and metadata/tag search; no file moves or network."""
import json
import sqlite3
import uuid


def clean_name(value, label='名称'):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128 or any(ord(c) < 32 for c in value):
        raise ValueError(f'{label}必须是 1–128 字的单行文本。')
    return value.strip()


def normalize_tags(values):
    if not isinstance(values, list) or len(values) > 256:
        raise ValueError('标签必须是最多 256 项的文本列表。')
    return list(dict.fromkeys(clean_name(value, '标签') for value in values))


class CollectionLibrary:
    def list_collections(self):
        with self.connection() as db:
            return [dict(id=row[0], name=row[1], parentId=row[2], count=row[3]) for row in db.execute('''
                SELECT c.id,c.name,c.parent_id,COUNT(m.document_id) FROM desktop_collections c
                LEFT JOIN desktop_memberships m ON m.collection_id=c.id
                  AND NOT EXISTS(SELECT 1 FROM desktop_attachment_links a WHERE a.child_document_id=m.document_id)
                GROUP BY c.id ORDER BY c.name COLLATE NOCASE,c.id
            ''')]

    @staticmethod
    def _require_collection(db, identifier):
        row = db.execute('SELECT name,parent_id FROM desktop_collections WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('集合不存在。')
        return row

    def create_collection(self, name, parent_id=None):
        name = clean_name(name, '集合名称')
        identifier = str(uuid.uuid4())
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if parent_id is not None:
                self._require_collection(db, parent_id)
            try:
                db.execute('INSERT INTO desktop_collections VALUES(?,?,?)', (identifier, name, parent_id))
            except sqlite3.IntegrityError:
                raise ValueError('同一层级已有同名集合。') from None
        self.audit('collection_created')
        return dict(id=identifier, name=name, parentId=parent_id, count=0)

    def update_collection(self, identifier, name, parent_id=None):
        name = clean_name(name, '集合名称')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._require_collection(db, identifier)
            if parent_id is not None:
                self._require_collection(db, parent_id)
                descendants = {row[0] for row in db.execute('''
                    WITH RECURSIVE branch(id) AS (
                      SELECT id FROM desktop_collections WHERE id=?
                      UNION SELECT c.id FROM desktop_collections c JOIN branch b ON c.parent_id=b.id
                    ) SELECT id FROM branch
                ''', (identifier,))}
                if parent_id in descendants:
                    raise ValueError('集合不能移动到自身或自己的子集合。')
            try:
                db.execute('UPDATE desktop_collections SET name=?,parent_id=? WHERE id=?', (name, parent_id, identifier))
            except sqlite3.IntegrityError:
                raise ValueError('目标层级已有同名集合。') from None
        self.audit('collection_updated')

    def delete_collection(self, identifier):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._require_collection(db, identifier)
            db.execute('DELETE FROM desktop_collections WHERE id=?', (identifier,))
        self.audit('collection_deleted')

    def document_collections(self, document_id):
        self.document(document_id)
        with self.connection() as db:
            return [row[0] for row in db.execute('SELECT collection_id FROM desktop_memberships WHERE document_id=?', (document_id,))]

    def set_membership(self, document_id, collection_id, present=True):
        if type(present) is not bool:
            raise ValueError('成员操作必须是添加或移出。')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._require_collection(db, collection_id)
            self._require_root(db, document_id)
            if not db.execute('SELECT 1 FROM desktop_documents WHERE id=?', (document_id,)).fetchone():
                raise ValueError('文献不存在。')
            if present:
                db.execute('INSERT OR IGNORE INTO desktop_memberships VALUES(?,?)', (document_id, collection_id))
            else:
                db.execute('DELETE FROM desktop_memberships WHERE document_id=? AND collection_id=?', (document_id, collection_id))
        self.audit('collection_membership_updated')

    def list_tags(self):
        return sorted({tag for document in self.list_root_documents() for tag in document.get('tags', [])}, key=str.casefold)

    def rename_tag(self, old, new=None):
        old = clean_name(old, '标签')
        if new is not None:
            new = clean_name(new, '标签')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for identifier, raw in db.execute('SELECT id,data FROM desktop_documents').fetchall():
                document = json.loads(raw)
                tags = document.get('tags', [])
                if old not in tags:
                    continue
                document['tags'] = list(dict.fromkeys(new if tag == old else tag for tag in tags if tag != old or new is not None))
                db.execute('UPDATE desktop_documents SET data=? WHERE id=?', (json.dumps(document, ensure_ascii=False), identifier))
        self.audit('tag_renamed' if new is not None else 'tag_removed')

    def search_documents(self, text='', collection_id=None, unfiled=False, tags=None, include_descendants=False):
        if not isinstance(text, str) or len(text) > 4096:
            raise ValueError('搜索文本无效。')
        if type(unfiled) is not bool or type(include_descendants) is not bool or (unfiled and collection_id is not None):
            raise ValueError('集合筛选条件无效。')
        required = set(normalize_tags(tags) if tags is not None else [])
        with self.connection() as db:
            if collection_id is not None:
                self._require_collection(db, collection_id)
                if include_descendants:
                    rows = db.execute('''WITH RECURSIVE branch(id) AS (
                        SELECT id FROM desktop_collections WHERE id=?
                        UNION SELECT c.id FROM desktop_collections c JOIN branch b ON c.parent_id=b.id)
                        SELECT d.data FROM desktop_documents d WHERE EXISTS (
                          SELECT 1 FROM desktop_memberships m JOIN branch b ON b.id=m.collection_id WHERE m.document_id=d.id)
                        ORDER BY d.rowid DESC''', (collection_id,))
                else:
                    rows = db.execute('''SELECT d.data FROM desktop_documents d JOIN desktop_memberships m
                        ON d.id=m.document_id WHERE m.collection_id=? ORDER BY d.rowid DESC''', (collection_id,))
            elif unfiled:
                rows = db.execute('''SELECT d.data FROM desktop_documents d WHERE NOT EXISTS (
                    SELECT 1 FROM desktop_memberships m WHERE m.document_id=d.id) ORDER BY d.rowid DESC''')
            else:
                rows = db.execute('SELECT data FROM desktop_documents ORDER BY rowid DESC')
            documents = [json.loads(row[0]) for row in rows]
            children = {row[0] for row in db.execute('SELECT child_document_id FROM desktop_attachment_links')}
            documents = [document for document in documents if document['id'] not in children]
        needle = text.strip().casefold()
        return [document for document in documents if required.issubset(set(document.get('tags', []))) and
                (not needle or needle in ' '.join(str(document.get(key, '')) for key in
                 ('title', 'authors', 'doi', 'year', 'tags', 'notes')).casefold())]
