# SPDX-License-Identifier: AGPL-3.0-only
"""Current-IR substring index. 当前 IR 的本地子串索引。

Index operations do not open PDFs, run OCR or access the network.
索引操作不读取 PDF、不运行 OCR、不联网。
"""
from datetime import datetime, timezone
from contextlib import contextmanager
import sqlite3
import time
import json
from .validation import QUERY_TEXT_POLICY

FULLTEXT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS desktop_fulltext_blocks(
 rowid INTEGER PRIMARY KEY, block_id TEXT NOT NULL UNIQUE,
 document_id TEXT NOT NULL, revision_id TEXT NOT NULL, folded TEXT NOT NULL,
 FOREIGN KEY(document_id,revision_id,block_id) REFERENCES desktop_ir_blocks(document_id,revision_id,id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS desktop_fulltext_document ON desktop_fulltext_blocks(document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS desktop_fulltext_fts USING fts5(
 folded, content='desktop_fulltext_blocks', content_rowid='rowid', tokenize='trigram case_sensitive 1');
CREATE TRIGGER IF NOT EXISTS desktop_fulltext_insert AFTER INSERT ON desktop_fulltext_blocks BEGIN
 INSERT INTO desktop_fulltext_fts(rowid,folded) VALUES(new.rowid,new.folded); END;
CREATE TRIGGER IF NOT EXISTS desktop_fulltext_delete AFTER DELETE ON desktop_fulltext_blocks BEGIN
 INSERT INTO desktop_fulltext_fts(desktop_fulltext_fts,rowid,folded) VALUES('delete',old.rowid,old.folded); END;
CREATE TRIGGER IF NOT EXISTS desktop_fulltext_update AFTER UPDATE ON desktop_fulltext_blocks BEGIN
 INSERT INTO desktop_fulltext_fts(desktop_fulltext_fts,rowid,folded) VALUES('delete',old.rowid,old.folded);
 INSERT INTO desktop_fulltext_fts(rowid,folded) VALUES(new.rowid,new.folded); END;
CREATE TABLE IF NOT EXISTS desktop_fulltext_status(
 document_id TEXT PRIMARY KEY REFERENCES desktop_documents(id) ON DELETE CASCADE,
 index_status TEXT NOT NULL CHECK(index_status IN ('indexed','no_text','cleared','unparsed')),
 last_parse_status TEXT CHECK(last_parse_status IN ('succeeded','failed')),
 last_error TEXT CHECK(last_error IS NULL OR last_error='parse_failed'), updated_at TEXT NOT NULL);
'''

def _now():
    return datetime.now(timezone.utc).isoformat()

def index_current_ir(db, document_id, parsed=False):
    """Caller owns the transaction: replace index atomically with current source IR.

    调用者拥有事务：索引与当前原文 IR 原子替换，FTS 触发器同步随事务回滚。
    """
    db.execute('DELETE FROM desktop_fulltext_blocks WHERE document_id=?', (document_id,))
    current = db.execute('SELECT revision_id FROM desktop_ir_current WHERE document_id=?', (document_id,)).fetchone()
    rows = db.execute('SELECT id,text FROM desktop_ir_blocks WHERE document_id=? AND revision_id=? ORDER BY rowid',
                      (document_id,current[0])).fetchall() if current else []
    db.executemany('INSERT INTO desktop_fulltext_blocks(block_id,document_id,revision_id,folded) VALUES(?,?,?,?)',
                   [(block_id,document_id,current[0],text.casefold()) for block_id,text in rows if text.strip()])
    status = 'indexed' if any(text.strip() for _,text in rows) else 'no_text' if current else 'unparsed'
    # Rebuilding an index is not a successful reparse; retain the real last error.
    # 重建索引不等于重新解析成功；保留真实的最近解析失败状态。
    previous = db.execute('SELECT last_parse_status,last_error FROM desktop_fulltext_status WHERE document_id=?', (document_id,)).fetchone()
    parse_status, error = ('succeeded',None) if parsed else previous if previous else ('succeeded',None) if current else (None,None)
    db.execute('INSERT INTO desktop_fulltext_status VALUES(?,?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET '
               'index_status=excluded.index_status,last_parse_status=excluded.last_parse_status,last_error=excluded.last_error,updated_at=excluded.updated_at',
               (document_id,status,parse_status,error,_now()))

def _check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise ValueError('本地全文查询超过 30 秒，请缩小筛选范围。')

def _snippet(text, needle, deadline=None):
    # Map casefold expansion back to original offsets (e.g. ß -> ss).
    # 将 casefold 扩展映射回原文坐标（如 ß -> ss），保留真实原文摘录。
    offset = text.casefold().find(needle)
    start = end = 0
    folded_offset = 0
    for i,char in enumerate(text):
        if deadline is not None and i % 2048 == 0:
            _check_deadline(deadline)
        next_offset = folded_offset + len(char.casefold())
        if folded_offset <= offset < next_offset:
            start = i
        if next_offset >= offset+len(needle):
            end = i+1
            break
        folded_offset = next_offset
    left = max(0,start-60)
    right = min(len(text),max(end,start+1)+100,left+510)
    return ('…' if left else '')+text[left:right]+('…' if right<len(text) else '')

class FulltextLibrary:
    @contextmanager
    def _fulltext_connection(self, deadline):
        with self.connection() as db:
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            try:
                yield db
            except sqlite3.OperationalError:
                _check_deadline(deadline)
                raise
            finally:
                db.set_progress_handler(None, 0)

    def note_parse_failure(self, document_id):
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM desktop_documents WHERE id=?',(document_id,)).fetchone():
                return
            db.execute("INSERT INTO desktop_fulltext_status VALUES(?,'unparsed','failed','parse_failed',?) ON CONFLICT(document_id) DO UPDATE SET last_parse_status='failed',last_error='parse_failed',updated_at=excluded.updated_at",(document_id,_now()))

    def clear_fulltext_index(self, document_id=None):
        return self._modify_fulltext_index(document_id, False)

    def rebuild_fulltext_index(self, document_id=None):
        return self._modify_fulltext_index(document_id, True)

    def _modify_fulltext_index(self, document_id, rebuild):
        if document_id is not None and (not isinstance(document_id,str) or not document_id):
            raise ValueError('文献标识无效。')
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            identifiers = [document_id] if document_id else [r[0] for r in db.execute('SELECT id FROM desktop_documents') if self.is_active(r[0], db)]
            for identifier in identifiers:
                self.require_active(identifier, db)
                if not db.execute('SELECT 1 FROM desktop_documents WHERE id=?',(identifier,)).fetchone():
                    raise ValueError('文献不存在。')
                if rebuild:
                    index_current_ir(db,identifier)
                else:
                    db.execute('DELETE FROM desktop_fulltext_blocks WHERE document_id=?',(identifier,))
                    db.execute("INSERT INTO desktop_fulltext_status VALUES(?,'cleared',NULL,NULL,?) ON CONFLICT(document_id) DO UPDATE SET index_status='cleared',updated_at=excluded.updated_at",(identifier,_now()))
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',
                       ('fulltext_index_rebuilt' if rebuild else 'fulltext_index_cleared','succeeded',_now()))
        return dict(documentCount=len(identifiers))

    def search_fulltext(self,text,collection_id=None,unfiled=False,tags=None,include_descendants=False,query=None,limit=200,metadata_text=''):
        text = QUERY_TEXT_POLICY.validate(text, '全文搜索值必须是最多 4 KiB 的单行文本。')
        if type(limit) is not int or not 1<=limit<=1000:
            raise ValueError('全文搜索结果上限必须是 1–1000。')
        deadline=time.monotonic()+30
        needle = text.casefold()
        with self.lock:
            roots=self.search_documents(text=metadata_text,collection_id=collection_id,unfiled=unfiled,tags=tags,include_descendants=include_descendants,query=query)
            _check_deadline(deadline)
            root_ids={doc['id'] for doc in roots}
            with self._fulltext_connection(deadline) as db:
                db.execute('BEGIN')
                coverage=[];family={};titles={}
                rows=db.execute('''SELECT d.id,d.data,a.parent_document_id,c.revision_id,s.index_status,s.last_parse_status,s.last_error,
                    (SELECT COUNT(*) FROM desktop_ir_blocks b WHERE b.document_id=d.id AND b.revision_id=c.revision_id),
                    (SELECT COUNT(*) FROM desktop_ir_pages p WHERE p.document_id=d.id AND p.revision_id=c.revision_id),
                    (SELECT COUNT(*) FROM desktop_fulltext_blocks i WHERE i.document_id=d.id AND i.revision_id=c.revision_id)
                    FROM desktop_documents d LEFT JOIN desktop_attachment_links a ON a.child_document_id=d.id
                    LEFT JOIN desktop_ir_current c ON c.document_id=d.id LEFT JOIN desktop_fulltext_status s ON s.document_id=d.id ORDER BY d.rowid''')
                for identifier,raw,parent,revision,status,last_parse,error,blocks,pages,indexed in rows:
                    _check_deadline(deadline)
                    parent=parent or identifier
                    if parent not in root_ids or not self.is_active(identifier, db):continue
                    doc=json.loads(raw);family[identifier]=parent;titles[identifier]=doc
                    actual_status = status or ('unparsed' if not revision else 'cleared')
                    if not revision and last_parse=='failed':actual_status='parse_failed'
                    coverage.append(dict(documentId=identifier,parentDocumentId=parent,revisionId=revision,status=actual_status,
                        lastParseStatus=last_parse,lastError=error,previousCurrent=bool(revision and last_parse=='failed'),blockCount=blocks,
                        pageCount=pages,indexedBlockCount=indexed,title=doc['title'],filename=doc['filename']))
                items=[];total=0
                if needle and family:
                    db.execute('CREATE TEMP TABLE fulltext_scope(id TEXT PRIMARY KEY)')
                    db.executemany('INSERT INTO fulltext_scope VALUES(?)',[(identifier,) for identifier in family])
                    candidate = 'JOIN desktop_fulltext_fts f ON f.rowid=i.rowid' if len(needle)>=3 else ''
                    predicate = 'desktop_fulltext_fts MATCH ? AND instr(i.folded,?)>0' if len(needle)>=3 else 'instr(i.folded,?)>0'
                    parameters = ('"'+needle.replace('"','""')+'"',needle) if len(needle)>=3 else (needle,)
                    base = ' FROM desktop_fulltext_blocks i '+candidate+''' JOIN fulltext_scope scope ON scope.id=i.document_id
                        JOIN desktop_ir_current c ON c.document_id=i.document_id AND c.revision_id=i.revision_id
                        JOIN desktop_ir_blocks b ON b.id=i.block_id AND b.revision_id=c.revision_id
                        JOIN desktop_ir_pages p ON p.id=b.page_id WHERE '''+predicate
                    total=db.execute('SELECT COUNT(*)'+base,parameters).fetchone()[0]
                    rows=db.execute('SELECT b.document_id,b.revision_id,b.id,p.number,b.bbox,b.text,p.transform,b.kind,b.reading_order'+base+
                                    ' ORDER BY b.document_id,p.number,b.reading_order LIMIT ?',(*parameters,limit))
                    for identifier,revision,block,page,bbox,text,transform,kind,order in rows:
                        items.append(dict(documentId=identifier,parentDocumentId=family[identifier],revisionId=revision,blockId=block,id=block,
                            page=page,pageNumber=page,bbox=json.loads(bbox),pageTransform=json.loads(transform),snippet=_snippet(text,needle,deadline),
                            title=titles[identifier]['title'],documentTitle=titles[identifier]['title'],filename=titles[identifier]['filename'],kind=kind,order=order))
        _check_deadline(deadline)
        return dict(items=items,total=total,truncated=total>limit,coverage=coverage)
