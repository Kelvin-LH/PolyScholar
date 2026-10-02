# SPDX-License-Identifier: AGPL-3.0-only
"""Recoverable local deletion and durable managed-file cleanup.

可恢复的本地删除与持久化托管文件清理；不删除外部来源文件。
SQLite and filesystem commits are distinct: unfinished cleanup remains explicit.
SQLite 与文件系统不能共同提交；未完成清理必须明确保留并可重试。
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import re
import stat
import uuid

TRASH_SCHEMA = '''CREATE TABLE IF NOT EXISTS desktop_trash(
 document_id TEXT PRIMARY KEY REFERENCES desktop_documents(id) ON DELETE CASCADE,
 trashed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS desktop_purge_cleanup(
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL, title TEXT NOT NULL,
 paths TEXT NOT NULL, created_at TEXT NOT NULL);'''

def _now():
    return datetime.now(timezone.utc).isoformat()

class TrashLibrary:
    def is_active(self, document_id, db=None):
        if db is None:
            with self.connection() as connection:
                return self.is_active(document_id, connection)
        return bool(db.execute('''SELECT 1 FROM desktop_documents d WHERE d.id=?
            AND NOT EXISTS(SELECT 1 FROM desktop_trash t WHERE t.document_id=d.id)
            AND NOT EXISTS(SELECT 1 FROM desktop_attachment_links a JOIN desktop_trash t ON t.document_id=a.parent_document_id WHERE a.child_document_id=d.id)''',(document_id,)).fetchone())

    def require_active(self, document_id, db=None):
        if not self.is_active(document_id, db):
            raise ValueError('文献已在回收站或不存在，请先恢复后操作。')

    def _family_ids(self, db, document_id):
        if not db.execute('SELECT 1 FROM desktop_documents WHERE id=?',(document_id,)).fetchone():
            raise ValueError('文献不存在。')
        return [document_id, *[row[0] for row in db.execute('SELECT child_document_id FROM desktop_attachment_links WHERE parent_document_id=?',(document_id,))]]

    @staticmethod
    def _require_idle(db, identifiers):
        for identifier in identifiers:
            if db.execute("SELECT 1 FROM desktop_jobs WHERE document_id=? AND state IN ('queued','running')",(identifier,)).fetchone():
                raise ValueError('请等待当前翻译任务结束后删除或恢复。')

    def trash_document(self, document_id):
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            identifiers = self._family_ids(db, document_id)
            self.require_active(document_id, db)
            self._require_idle(db, identifiers)
            now = _now()
            db.execute('INSERT INTO desktop_trash VALUES(?,?)',(document_id,now))
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',('document_trashed','succeeded',now))
        return dict(documentId=document_id,trashedAt=now)

    def restore_document(self, document_id):
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            identifiers=self._family_ids(db,document_id)
            self._require_idle(db,identifiers)
            if not db.execute('SELECT 1 FROM desktop_trash WHERE document_id=?',(document_id,)).fetchone():
                raise ValueError('该文献没有独立回收站记录。')
            if db.execute('SELECT 1 FROM desktop_attachment_links a JOIN desktop_trash t ON t.document_id=a.parent_document_id WHERE a.child_document_id=?',(document_id,)).fetchone():
                raise ValueError('请先恢复所属文献，再恢复此附件。')
            db.execute('DELETE FROM desktop_trash WHERE document_id=?',(document_id,))
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',('document_restored','succeeded',_now()))
        return dict(documentId=document_id)

    def list_trash(self):
        with self.connection() as db:
            rows=db.execute('''SELECT d.data,t.trashed_at,a.parent_document_id,
                EXISTS(SELECT 1 FROM desktop_trash pt WHERE pt.document_id=a.parent_document_id)
                FROM desktop_trash t JOIN desktop_documents d ON d.id=t.document_id
                LEFT JOIN desktop_attachment_links a ON a.child_document_id=d.id ORDER BY t.trashed_at,d.id''')
            result=[]
            for raw,created,parent,blocked in rows:
                document=json.loads(raw)
                result.append(dict(id=document['id'],documentId=document['id'],title=document['title'],filename=document['filename'],parentDocumentId=parent or document['id'],scope='child' if parent else 'root',trashedAt=created,restoreBlocked=bool(blocked),explicitMarker=True))
            return result

    def deletion_preview(self, document_id):
        with self.lock, self.connection() as db:
            identifiers=self._family_ids(db,document_id)
            documents=[json.loads(db.execute('SELECT data FROM desktop_documents WHERE id=?',(identifier,)).fetchone()[0]) for identifier in identifiers]
            jobs=[json.loads(row[0]) for identifier in identifiers for row in db.execute('SELECT data FROM desktop_jobs WHERE document_id=?',(identifier,))]
            marker=bool(db.execute('SELECT 1 FROM desktop_trash WHERE document_id=?',(document_id,)).fetchone())
            active_jobs=any(job['state'] in ('queued','running') for job in jobs)
            counts=dict(pdfs=len(documents),notes=sum(bool(doc.get('notes','').strip()) for doc in documents),
                claims=sum(db.execute('SELECT COUNT(*) FROM desktop_claims WHERE document_id=?',(identifier,)).fetchone()[0] for identifier in identifiers),
                jobs=len(jobs),artifacts=sum(len(job.get('artifacts',[])) for job in jobs),
                collections=sum(db.execute('SELECT COUNT(*) FROM desktop_memberships WHERE document_id=?',(identifier,)).fetchone()[0] for identifier in identifiers))
            managed=[str(self.object_path(document)) for document in documents]
            managed += [str(self.output_path(job)) for job in jobs]
            return dict(documentId=document_id,title=documents[0]['title'],documentIds=identifiers,counts=counts,
                managedFiles=list(dict.fromkeys(managed)),sourceFiles=list(dict.fromkeys(path for document in documents for path in document.get('sourcePaths',[]))),
                explicitMarker=marker,activeJobs=active_jobs,purgeAllowed=marker and not active_jobs)

    def purge_document(self, document_id):
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            identifiers=self._family_ids(db,document_id)
            if not db.execute('SELECT 1 FROM desktop_trash WHERE document_id=?',(document_id,)).fetchone():
                raise ValueError('永久删除必须选择有独立回收站记录的文献。')
            self._require_idle(db,identifiers)
            documents=[json.loads(db.execute('SELECT data FROM desktop_documents WHERE id=?',(identifier,)).fetchone()[0]) for identifier in identifiers]
            jobs=[json.loads(row[0]) for identifier in identifiers for row in db.execute('SELECT data FROM desktop_jobs WHERE document_id=?',(identifier,))]
            for identifier in identifiers:db.execute('DELETE FROM desktop_jobs WHERE document_id=?',(identifier,))
            for identifier in identifiers:db.execute('DELETE FROM desktop_documents WHERE id=?',(identifier,))
            paths=[]
            for document in documents:
                if not db.execute('SELECT 1 FROM desktop_documents WHERE sha256=?',(document['sha256'],)).fetchone():
                    paths.append(self._capture_cleanup_path(dict(kind='object',path=str(self.object_path(document)))))
            for job in jobs:
                output=self.output_path(job)
                if output.name != job['id'] or output.parent.name != 'jobs':
                    raise ValueError('任务清理范围无效。')
                paths.append(self._capture_cleanup_path(dict(kind='job',path=str(output),jobId=job['id'],jobsRoot=str(output.parent))))
            cleanup_id=str(uuid.uuid4())
            db.execute('INSERT INTO desktop_purge_cleanup VALUES(?,?,?,?,?)',(cleanup_id,document_id,documents[0]['title'],json.dumps(paths),_now()))
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',('document_deleted','succeeded',_now()))
        # Commit the durable plan before unlinking anything. 清理前先提交持久化计划。
        return self.retry_cleanup(cleanup_id)

    def list_pending_cleanup(self):
        with self.connection() as db:
            return [dict(id=row[0],documentId=row[1],title=row[2],pathCount=len(json.loads(row[3])),remainingCleanup=len(json.loads(row[3])),errorCode='cleanup_failed' if any(entry.get('errorCode') for entry in json.loads(row[3])) else None,createdAt=row[4]) for row in db.execute('SELECT * FROM desktop_purge_cleanup ORDER BY created_at,id')]

    def retry_cleanup(self, cleanup_id=None):
        if cleanup_id is None:
            with self.lock:
                results = [self.retry_cleanup(row['id']) for row in self.list_pending_cleanup()]
                remaining = sum(row['remainingCleanup'] for row in results)
                return dict(cleanupComplete=not remaining,remainingCleanup=remaining,pendingCount=remaining,results=results)
        with self.lock:
            with self.connection() as db:
                row=db.execute('SELECT document_id,paths FROM desktop_purge_cleanup WHERE id=?',(cleanup_id,)).fetchone()
            if not row:raise ValueError('待清理记录不存在。')
            entries=json.loads(row[1]);remaining=[]
            for entry in entries:
                try:self._remove_managed_path(entry)
                except (OSError,ValueError):
                    remaining.append({**entry,'errorCode':'cleanup_failed'})
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                if remaining:
                    db.execute('UPDATE desktop_purge_cleanup SET paths=? WHERE id=?',(json.dumps(remaining),cleanup_id))
                else:
                    db.execute('DELETE FROM desktop_purge_cleanup WHERE id=?',(cleanup_id,))
                db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',('purge_cleanup','failed' if remaining else 'succeeded',_now()))
            return dict(documentId=row[0],cleanupId=cleanup_id,purged=True,cleanupComplete=not remaining,pendingCount=len(remaining),remainingCleanup=len(remaining))

    @staticmethod
    def _identity(path):
        info = path.lstat()
        return [info.st_dev, info.st_ino]

    def _capture_cleanup_path(self, entry):
        path = Path(entry['path'])
        if entry['kind'] == 'object' and not re.fullmatch(r'[0-9a-f]{64}\.pdf',path.name):
            raise ValueError('托管对象清理范围无效。')
        if entry['kind'] == 'job':
            try:
                uuid.UUID(entry['jobId'])
            except (ValueError, TypeError, AttributeError):
                raise ValueError('任务清理标识无效。') from None
        return {**entry, 'existed':path.exists(), 'identity':self._identity(path) if path.exists() else None,
                'parentIdentity':self._identity(path.parent) if path.parent.exists() else None}

    def _remove_managed_path(self, entry):
        path = Path(entry['path'])
        # Revalidate identities on every retry; never delete a replacement path.
        # 每次重试重新核对身份；不能删除后来替换到相同路径的新文件或目录。
        if not path.is_absolute() or path.resolve()!=path:
            raise ValueError('托管清理路径无效。')
        for ancestor in (path,*path.parents):
            if ancestor.is_symlink() or getattr(ancestor,'is_junction',lambda:False)():
                raise ValueError('托管清理路径不能包含链接。')
        if entry['kind']=='object':
            if path.parent!=self.objects or not re.fullmatch(r'[0-9a-f]{64}\.pdf',path.name):
                raise ValueError('托管对象清理范围无效。')
            with self.connection() as db:
                if db.execute('SELECT 1 FROM desktop_documents WHERE sha256=?',(path.stem,)).fetchone():
                    return
        elif entry['kind']=='job':
            if path.name!=entry['jobId'] or path.parent.name!='jobs' or str(path.parent)!=entry['jobsRoot']:
                raise ValueError('任务清理范围无效。')
        else:
            raise ValueError('托管清理类型无效。')
        if not path.exists():
            return
        if os.name == 'posix' and entry['kind'] == 'job':
            parent_stat = path.parent.stat()
            if parent_stat.st_uid != os.getuid() or parent_stat.st_mode & 0o022:
                raise ValueError('任务目录由其他用户控制或可被替换。')
        if not entry['existed'] or self._identity(path)!=entry['identity'] or self._identity(path.parent)!=entry['parentIdentity']:
            raise ValueError('托管清理文件或目录身份已变化。')
        if entry['kind']=='object':
            if os.name=='nt':
                path.chmod(stat.S_IWRITE)
            path.unlink()
        else:
            shutil.rmtree(path)
