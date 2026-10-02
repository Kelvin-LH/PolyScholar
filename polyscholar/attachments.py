# SPDX-License-Identifier: AGPL-3.0-only
"""Managed PDF attachments retain independent read, parse and translation identities."""
import json

ATTACHMENT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS desktop_attachment_links(
    parent_document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
    child_document_id TEXT PRIMARY KEY REFERENCES desktop_documents(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('translation','supplement')),
    label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 1024),
    CHECK(parent_document_id != child_document_id));
CREATE INDEX IF NOT EXISTS desktop_attachment_parent ON desktop_attachment_links(parent_document_id);
CREATE TRIGGER IF NOT EXISTS desktop_attachment_shape_insert BEFORE INSERT ON desktop_attachment_links
WHEN EXISTS(SELECT 1 FROM desktop_attachment_links WHERE child_document_id=NEW.parent_document_id)
 OR EXISTS(SELECT 1 FROM desktop_attachment_links WHERE parent_document_id=NEW.child_document_id)
BEGIN SELECT RAISE(ABORT, 'Attachments must belong directly to a root document'); END;
CREATE TRIGGER IF NOT EXISTS desktop_attachment_shape_update BEFORE UPDATE ON desktop_attachment_links
WHEN EXISTS(SELECT 1 FROM desktop_attachment_links WHERE child_document_id=NEW.parent_document_id)
 OR EXISTS(SELECT 1 FROM desktop_attachment_links WHERE parent_document_id=NEW.child_document_id)
BEGIN SELECT RAISE(ABORT, 'Attachments must belong directly to a root document'); END;
'''


class AttachmentLibrary:
    def _require_root(self, db, parent_id):
        self.require_active(parent_id, db)
        if not db.execute('SELECT 1 FROM desktop_documents WHERE id=?', (parent_id,)).fetchone():
            raise ValueError('文献不存在。')
        if db.execute('SELECT 1 FROM desktop_attachment_links WHERE child_document_id=?', (parent_id,)).fetchone():
            raise ValueError('请在所属文献条目下管理附件。')

    def list_root_documents(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('''SELECT d.data FROM desktop_documents d
                WHERE NOT EXISTS(SELECT 1 FROM desktop_attachment_links a WHERE a.child_document_id=d.id)
                AND NOT EXISTS(SELECT 1 FROM desktop_trash t WHERE t.document_id=d.id)
                ORDER BY d.rowid DESC''')]

    def list_attachments(self, parent_id):
        with self.connection() as db:
            self._require_root(db, parent_id)
            root = json.loads(db.execute('SELECT data FROM desktop_documents WHERE id=?', (parent_id,)).fetchone()[0])
            result = [{**root, 'documentId': parent_id, 'parentDocumentId': parent_id,
                       'role': 'original', 'label': root['filename']}]
            for raw, role, label in db.execute('''SELECT d.data,a.role,a.label FROM desktop_attachment_links a
                    JOIN desktop_documents d ON d.id=a.child_document_id WHERE a.parent_document_id=?
                    AND NOT EXISTS(SELECT 1 FROM desktop_trash t WHERE t.document_id=d.id)
                    ORDER BY d.rowid''', (parent_id,)):
                document = json.loads(raw)
                result.append({**document, 'documentId': document['id'], 'parentDocumentId': parent_id,
                               'role': role, 'label': label})
            return result

    def import_attachment(self, parent_id, path, role='supplement'):
        if role not in ('translation', 'supplement'):
            raise ValueError('附件类型必须是译文或补充材料。')
        with self.lock:
            document = self._import_pdf(path, parent_id=parent_id, role=role)
            return next(row for row in self.list_attachments(parent_id) if row['id'] == document['id'])

    def delete_attachment(self, parent_id, document_id):
        with self.lock:
            with self.connection() as db:
                self._require_root(db, parent_id)
                if document_id == parent_id:
                    raise ValueError('原始 PDF 请通过删除文献条目移除。')
                if not db.execute('SELECT 1 FROM desktop_attachment_links WHERE parent_document_id=? AND child_document_id=?',
                                  (parent_id, document_id)).fetchone():
                    raise ValueError('附件不属于该文献。')
            return self.trash_document(document_id)
