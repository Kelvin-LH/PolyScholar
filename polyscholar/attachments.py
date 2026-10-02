# SPDX-License-Identifier: AGPL-3.0-only
"""Bibliographic roots select real, independently managed PDF identities.

书目根条目选择真实、独立管理的 PDF；无文件来源合并记录不作为附件显示。
"""
import json
from .bibliographic import BibliographicPolicy
from datetime import datetime, timezone

ATTACHMENT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS desktop_attachment_links(
    parent_document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
    child_document_id TEXT PRIMARY KEY REFERENCES desktop_documents(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('translation','supplement','merged_record')),
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
    def require_pdf(self, document_id, db=None):
        if db is None:
            with self.connection() as connection:
                return self.require_pdf(document_id, connection)
        self.require_active(document_id, db)
        row = db.execute('SELECT data FROM desktop_documents WHERE id=?',(document_id,)).fetchone()
        document = json.loads(row[0])
        BibliographicPolicy.require_pdf(document)
        return document

    def _primary_pdf_id(self, db, root):
        # A hidden explicit primary remains selected for restoration; never substitute another file.
        # 被隐藏的主 PDF 保留选择供恢复，不自动改读另一份文件。
        identifier = root.get('primaryPdfId')
        if 'primaryPdfId' not in root and BibliographicPolicy.is_pdf(root):
            identifier = root['id']
        if identifier is None or not self.is_active(identifier,db):
            return None
        row=db.execute('SELECT data FROM desktop_documents WHERE id=?',(identifier,)).fetchone()
        if not row or not BibliographicPolicy.is_pdf(json.loads(row[0])):
            return None
        if identifier != root['id'] and not db.execute('SELECT 1 FROM desktop_attachment_links WHERE parent_document_id=? AND child_document_id=?',(root['id'],identifier)).fetchone():
            return None
        return identifier

    def primary_pdf_id(self, root_id):
        with self.connection() as db:
            self._require_root(db,root_id)
            root=json.loads(db.execute('SELECT data FROM desktop_documents WHERE id=?',(root_id,)).fetchone()[0])
            return self._primary_pdf_id(db,root)

    def _file_identity(self, document, db=None):
        if db is None:
            with self.connection() as connection:
                return self._file_identity(document,connection)
        result=BibliographicPolicy.identity(document)
        if not db.execute('SELECT 1 FROM desktop_attachment_links WHERE child_document_id=?',(document['id'],)).fetchone():
            result['primaryPdfId']=self._primary_pdf_id(db,document)
            result['hasAnyPdf']=result['primaryPdfId'] is not None
        return result

    def set_primary_pdf(self, root_id, pdf_id):
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._require_root(db,root_id)
            self.require_pdf(pdf_id,db)
            if pdf_id != root_id and not db.execute('SELECT 1 FROM desktop_attachment_links WHERE parent_document_id=? AND child_document_id=?',(root_id,pdf_id)).fetchone():
                raise ValueError('主 PDF 必须属于当前文献。')
            root=json.loads(db.execute('SELECT data FROM desktop_documents WHERE id=?',(root_id,)).fetchone()[0])
            root['primaryPdfId']=pdf_id
            db.execute('UPDATE desktop_documents SET data=? WHERE id=?',(json.dumps(root,ensure_ascii=False),root_id))
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',('primary_pdf_changed','succeeded',datetime.now(timezone.utc).isoformat()))
        return self.document(root_id)

    def _require_root(self, db, parent_id):
        self.require_active(parent_id, db)
        if not db.execute('SELECT 1 FROM desktop_documents WHERE id=?', (parent_id,)).fetchone():
            raise ValueError('文献不存在。')
        if db.execute('SELECT 1 FROM desktop_attachment_links WHERE child_document_id=?', (parent_id,)).fetchone():
            raise ValueError('请在所属文献条目下管理附件。')

    def list_root_documents(self):
        with self.connection() as db:
            return [self._file_identity(json.loads(row[0]),db) for row in db.execute('''SELECT d.data FROM desktop_documents d
                WHERE NOT EXISTS(SELECT 1 FROM desktop_attachment_links a WHERE a.child_document_id=d.id)
                AND NOT EXISTS(SELECT 1 FROM desktop_trash t WHERE t.document_id=d.id)
                ORDER BY d.rowid DESC''')]

    def list_attachments(self, parent_id):
        with self.connection() as db:
            self._require_root(db, parent_id)
            root = json.loads(db.execute('SELECT data FROM desktop_documents WHERE id=?', (parent_id,)).fetchone()[0])
            primary=self._primary_pdf_id(db,root)
            result = []
            if BibliographicPolicy.is_pdf(root):
                result.append({
                    **self._file_identity(root, db), 'documentId': parent_id,
                    'parentDocumentId': parent_id, 'role': 'original',
                    'label': root['filename'], 'isPrimary': primary == parent_id,
                })
            for raw, role, label in db.execute('''SELECT d.data,a.role,a.label FROM desktop_attachment_links a
                    JOIN desktop_documents d ON d.id=a.child_document_id WHERE a.parent_document_id=?
                    AND NOT EXISTS(SELECT 1 FROM desktop_trash t WHERE t.document_id=d.id)
                    ORDER BY d.rowid''', (parent_id,)):
                document = json.loads(raw)
                if not BibliographicPolicy.is_pdf(document):
                    continue
                result.append({**self._file_identity(document,db), 'documentId': document['id'], 'parentDocumentId': parent_id,
                               'role': role, 'label': label, 'isPrimary': primary==document['id']})
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
