# SPDX-License-Identifier: AGPL-3.0-only
"""Local duplicate suggestions and explicit bibliographic merges.

本地书目重复候选与人工合并；候选不代表同一作品，不自动改动文献。
Candidate matching does not establish work identity or automatically mutate data.
"""
from collections import defaultdict
from contextlib import contextmanager
import sqlite3
from datetime import datetime, timezone
import hashlib
from itertools import combinations
import json
import re
import time
import uuid
from .metadata import BIB_FIELDS, normalize_metadata, metadata_patch
from .library import normalize_tags

MERGE_FIELDS = ('title', 'creators', 'doi', 'year', *BIB_FIELDS)
MAX_CANDIDATE_PAIRS = 100000
MAX_CANDIDATE_ROOTS = 20000
MAX_AUTHOR_BUCKET_ENTRIES = 200000
MAX_MERGE_FAMILY = 2000
MAX_CANDIDATE_BYTES = 64 * 1024 * 1024
MAX_HISTORY_BYTES = 32 * 1024 * 1024
MERGE_SCHEMA = '''CREATE TABLE IF NOT EXISTS desktop_merge_history(
 id TEXT PRIMARY KEY, master_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
 document_ids TEXT NOT NULL, snapshot TEXT NOT NULL, counts TEXT NOT NULL, created_at TEXT NOT NULL);'''


def _fold(value):
    return ' '.join(value.split()).casefold()


def normalized_doi(value):
    if not isinstance(value, str):
        return None
    if any(ord(char)<32 or 127 <= ord(char) <= 159 for char in value):
        return None
    value = value.strip().casefold()
    value = re.sub(r'^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)', '', value)
    if re.fullmatch(r'10\.[0-9]{4,9}/[^\s\x00-\x1f\x7f]+', value):
        return value
    return None


def normalized_isbn(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r'[\s-]', '', value).upper()
    if re.fullmatch(r'[0-9]{9}[0-9X]', value):
        digits = [10 if char == 'X' else int(char) for char in value]
        if sum((10-index)*digit for index, digit in enumerate(digits)) % 11:
            return None
        prefix = '978'+value[:9]
        return prefix+str((-sum(int(char)*(1 if index%2==0 else 3) for index,char in enumerate(prefix)))%10)
    if re.fullmatch(r'97[89][0-9]{10}', value) and sum(int(char)*(1 if index%2==0 else 3) for index,char in enumerate(value))%10==0:
        return value
    return None


def _author_identities(document):
    # Match the stored identity, never infer family/given from a display name.
    # 按已存身份匹配，不从显示姓名猜测姓或名；编者不作为作者候选依据。
    identities = set()
    for creator in normalize_metadata(document)['creators']:
        if creator.get('role', 'author') != 'author':
            continue
        literal = _fold(creator.get('literal', ''))
        family, given = _fold(creator.get('family','')), _fold(creator.get('given',''))
        identity = (creator.get('type','person'), 'literal', literal) if literal else ('person','structured',family,given)
        if literal or family or given:
            identities.add(identity)
    return identities


def format_note_contributions(contributions):
    return '\n\n'.join('[文献 '+entry['documentId']+']\n'+entry['text'] for entry in contributions)


def note_contributions(document):
    """Expand only our verified representation; edited notes remain literal text.

    只展开已核对的内部表示；手工修改后的笔记作为真实新原文，不猜测头部。
    """
    note = document.get('notes','')
    stored = document.get('mergeNoteSources')
    valid = isinstance(stored,list) and len(stored) <= MAX_MERGE_FAMILY
    if valid:
        byte_count = 0
        for entry in stored:
            if not isinstance(entry,dict) or set(entry) != {'documentId','text'} or not isinstance(entry['documentId'],str) or not entry['documentId'] or not isinstance(entry['text'],str):
                valid = False
                break
            try:
                encoded = entry['text'].encode('utf-8')
                identity = entry['documentId'].encode('utf-8')
            except UnicodeEncodeError:
                valid = False
                break
            byte_count += len(encoded)+len(identity)+len('[文献 ]\n'.encode('utf-8'))+(2 if byte_count else 0)
            if byte_count>65536 or len(encoded)>65536 or len(identity)>1024 or '\x00' in entry['documentId'] or not entry['text'].strip():
                valid = False
                break
    if valid:
        formatted = format_note_contributions(stored)
        try:
            valid = len(formatted.encode('utf-8')) <= 65536
        except UnicodeEncodeError:
            valid = False
        if valid and note == formatted:
            return [dict(entry) for entry in stored]
    return [dict(documentId=document['id'],text=note)] if note.strip() else []


def merge_note_contributions(documents):
    contributions = []
    seen = set()
    byte_count = 0
    for document in documents:
        for entry in note_contributions(document):
            if entry['text'].strip() and entry['text'] not in seen:
                byte_count += len(entry['text'].encode('utf-8'))+len(entry['documentId'].encode('utf-8'))+len('[文献 ]\n'.encode('utf-8'))+(2 if contributions else 0)
                if byte_count>65536:
                    raise ValueError('合并后的笔记超过 64 KiB，请先整理笔记。')
                seen.add(entry['text'])
                contributions.append(entry)
                if len(contributions)>MAX_MERGE_FAMILY:
                    raise ValueError('合并后的笔记来源数量超过本地上限。')
    formatted = format_note_contributions(contributions)
    if len(formatted.encode('utf-8'))>65536:
        raise ValueError('合并后的笔记超过 64 KiB，请先整理笔记。')
    return contributions,formatted


def _check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise ValueError('重复检查或合并预览超过 30 秒，请减少所选范围后重试。')


class DuplicateLibrary:
    @staticmethod
    @contextmanager
    def _bounded_duplicate_sql(db, deadline):
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        try:
            yield
        except sqlite3.OperationalError:
            _check_deadline(deadline)
            raise
        finally:
            db.set_progress_handler(None, 0)

    def list_duplicate_candidates(self, limit=200):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('重复候选显示上限必须是 1–1000。')
        deadline = time.monotonic()+30
        documents = []
        with self.lock, self.connection() as db, self._bounded_duplicate_sql(db, deadline):
            db.execute('BEGIN')
            root_scope = '''NOT EXISTS(SELECT 1 FROM desktop_attachment_links a WHERE a.child_document_id=d.id)
                AND NOT EXISTS(SELECT 1 FROM desktop_trash t WHERE t.document_id=d.id)'''
            count = db.execute('SELECT COUNT(*) FROM desktop_documents d WHERE '+root_scope).fetchone()[0]
            if count > MAX_CANDIDATE_ROOTS:
                raise ValueError('重复检查最多支持 20000 个活动条目。')
            # Read only candidate fields; notes/source paths never enter buckets.
            # 只读取候选所需字段；笔记和私人来源路径不进入候选索引。
            rows = db.execute("SELECT id,json_extract(data,'$.title'),json_extract(data,'$.year'),"
                "json_extract(data,'$.doi'),json_extract(data,'$.isbn'),json_extract(data,'$.itemType'),"
                "json_extract(data,'$.authors'),json_extract(data,'$.creators') FROM desktop_documents d WHERE "+root_scope+' ORDER BY d.rowid')
            byte_count = 0
            for row in rows:
                _check_deadline(deadline)
                byte_count += sum(len(value.encode('utf-8')) for value in row if isinstance(value,str))
                if byte_count > MAX_CANDIDATE_BYTES:
                    raise ValueError('重复检查候选字段超过 64 MiB，本次未返回部分结果。')
                identifier,title,year,doi,isbn,kind,authors,creators = row
                document = dict(id=identifier,title=title or '',year=year or '',doi=doi or '',isbn=isbn or '',itemType=kind or 'article-journal',authors=authors or '')
                if creators is not None:
                    document['creators'] = json.loads(creators)
                documents.append(normalize_metadata(document))
        identifiers = {doc['id']:doc for doc in documents}
        buckets = {'doi':defaultdict(set), 'isbn':defaultdict(set)}
        title_buckets = defaultdict(lambda:defaultdict(set))
        entries = 0
        for document in documents:
            _check_deadline(deadline)
            kind = document['itemType']
            for field, normalizer in (('doi',normalized_doi),('isbn',normalized_isbn)):
                key = normalizer(document.get(field,''))
                if key:
                    buckets[field][(kind,key)].add(document['id'])
            title,year = _fold(document['title']), document.get('year','')
            if title and re.fullmatch(r'[0-9]{4}',year) and 1 <= int(year) <= 9999:
                for identity in _author_identities(document):
                    entries += 1
                    if entries > MAX_AUTHOR_BUCKET_ENTRIES:
                        raise ValueError('作者匹配索引超过本地重复检查资源上限。')
                    title_buckets[(kind,title,identity)][int(year)].add(document['id'])
        reasons = defaultdict(set)
        def add_pair(left, right, reason):
            if left == right:
                return
            _check_deadline(deadline)
            pair = tuple(sorted((left,right)))
            reasons[pair].add(reason)
            if len(reasons) > MAX_CANDIDATE_PAIRS:
                raise ValueError('重复候选超过 100000 对，请先减少重复条目；未返回截断计数。')
        for reason, groups in buckets.items():
            for group in groups.values():
                if len(group)*(len(group)-1)//2 > MAX_CANDIDATE_PAIRS:
                    raise ValueError('单个重复分组超过 100000 对候选。')
                for left,right in combinations(sorted(group),2):
                    add_pair(left,right,reason)
        for years in title_buckets.values():
            for year, group in years.items():
                for left,right in combinations(sorted(group),2):
                    add_pair(left,right,'title_creator_year')
                for left in group:
                    for right in years.get(year+1,()):
                        add_pair(left,right,'title_creator_year')
        items = []
        for pair in sorted(reasons)[:limit]:
            items.append(dict(documentIds=list(pair),reasons=[r for r in ('doi','isbn','title_creator_year') if r in reasons[pair]],
                documents=[dict(id=identifier,title=identifiers[identifier]['title'][:512],itemType=identifiers[identifier]['itemType'],year=identifiers[identifier].get('year','')) for identifier in pair]))
        _check_deadline(deadline)
        return dict(items=items,total=len(reasons),truncated=len(reasons)>limit)

    @staticmethod
    def validate_merge_selection(document_ids, master_id):
        if not isinstance(document_ids,list) or not 2 <= len(document_ids) <= 20 or any(not isinstance(identifier,str) or not identifier for identifier in document_ids) or len(set(document_ids))!=len(document_ids):
            raise ValueError('请选择 2–20 个不同的文献条目。')
        master_id = document_ids[0] if master_id is None else master_id
        if not isinstance(master_id,str) or master_id not in document_ids:
            raise ValueError('主文献必须来自所选条目。')
        return master_id

    def _merge_state(self, db, document_ids, master_id):
        deadline = time.monotonic()+30
        with self._bounded_duplicate_sql(db, deadline):
            result = self._build_merge_state(db, document_ids, master_id, deadline)
            _check_deadline(deadline)
            return result

    def _build_merge_state(self, db, document_ids, master_id, deadline):
        for identifier in document_ids:
            self._require_root(db, identifier)
        documents = [normalize_metadata(json.loads(db.execute('SELECT data FROM desktop_documents WHERE id=?',(identifier,)).fetchone()[0])) for identifier in document_ids]
        if len({document['itemType'] for document in documents}) != 1:
            raise ValueError('只能人工合并相同文献类型的条目。')
        family = sorted({identifier for root in document_ids for identifier in self._family_ids(db,root)})
        if len(family) > MAX_MERGE_FAMILY:
            raise ValueError('所选文献包含超过 2000 个 PDF，无法安全预览合并。')
        db.execute('CREATE TEMP TABLE IF NOT EXISTS merge_scope(id TEXT PRIMARY KEY)')
        db.execute('DELETE FROM merge_scope')
        db.executemany('INSERT INTO merge_scope VALUES(?)',[(identifier,) for identifier in family])
        conditions = {
            'desktop_documents':'id IN (SELECT id FROM merge_scope)',
            'desktop_attachment_links':'parent_document_id IN (SELECT id FROM merge_scope) OR child_document_id IN (SELECT id FROM merge_scope)',
            'desktop_trash':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_memberships':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_jobs':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_ir_revisions':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_ir_current':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_ir_pages':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_ir_blocks':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_claims':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_claim_evidence':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_model_summaries':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_claim_provenance':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_fulltext_blocks':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_fulltext_status':'document_id IN (SELECT id FROM merge_scope)',
            'desktop_merge_history':'master_id IN (SELECT id FROM merge_scope)',
        }
        digest = hashlib.sha256()
        digest.update(json.dumps(dict(documentIds=document_ids,masterId=master_id),sort_keys=True).encode())
        snapshot = {'documents':documents, 'links':[], 'trash':[], 'memberships':[]}
        snapshot_tables={'desktop_attachment_links':'links','desktop_trash':'trash','desktop_memberships':'memberships'}
        # Hash rows incrementally; do not duplicate full IR text in history.
        # 逐行计算摘要，历史只保存书目及关系快照，不复制大块 IR 原文。
        for table,condition in conditions.items():
            digest.update(table.encode()+b'\0')
            for row in db.execute('SELECT * FROM '+table+' WHERE '+condition+' ORDER BY rowid'):
                _check_deadline(deadline)
                digest.update(json.dumps(list(row),ensure_ascii=False,separators=(',',':')).encode('utf-8')+b'\n')
                if table in snapshot_tables:
                    snapshot[snapshot_tables[table]].append(list(row))
        if len(json.dumps(snapshot,ensure_ascii=False).encode('utf-8')) > MAX_HISTORY_BYTES:
            raise ValueError('合并书目关系快照超过本地安全大小上限。')
        jobs = [json.loads(row[0]) for row in db.execute('SELECT data FROM desktop_jobs WHERE document_id IN (SELECT id FROM merge_scope)')]
        notes_count = sum(bool((row[0] or '').strip()) for row in db.execute("SELECT json_extract(data,'$.notes') FROM desktop_documents WHERE id IN (SELECT id FROM merge_scope)"))
        counts = dict(pdfs=len(family),attachments=len(family)-len(document_ids),notes=notes_count,
            claims=db.execute('SELECT COUNT(*) FROM desktop_claims WHERE document_id IN (SELECT id FROM merge_scope)').fetchone()[0],jobs=len(jobs),
            artifacts=sum(len(job.get('artifacts',[])) for job in jobs),collections=len({row[1] for row in snapshot['memberships']}),trashedChildren=len(snapshot['trash']))
        return dict(documentIds=list(document_ids),masterId=master_id,documents=documents,fields={field:[dict(documentId=doc['id'],value=doc.get(field,'')) for doc in documents] for field in MERGE_FIELDS},
            counts=counts,revision=digest.hexdigest(),activeJobs=any(job['state'] in ('queued','running') for job in jobs)),snapshot

    def merge_preview(self, document_ids, master_id=None):
        master_id = self.validate_merge_selection(document_ids,master_id)
        with self.lock, self.connection() as db:
            db.execute('BEGIN')
            return self._merge_state(db,document_ids,master_id)[0]

    def merge_documents(self, document_ids, master_id, field_sources, expected_revision):
        master_id = self.validate_merge_selection(document_ids,master_id)
        if not isinstance(field_sources,dict) or any(field not in MERGE_FIELDS or source not in document_ids for field,source in field_sources.items()):
            raise ValueError('合并字段来源无效。')
        if not isinstance(expected_revision,str) or not re.fullmatch(r'[0-9a-f]{64}',expected_revision):
            raise ValueError('请先查看并确认当前合并预览。')
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            preview,snapshot = self._merge_state(db,document_ids,master_id)
            if preview['revision'] != expected_revision:
                raise ValueError('文献或关联数据已变化，请刷新合并预览。')
            if preview['activeJobs']:
                raise ValueError('请等待所有相关翻译任务结束后合并。')
            documents={document['id']:document for document in preview['documents']}
            master=documents[master_id]
            selected={field:documents[field_sources.get(field,master_id)].get(field,'') for field in MERGE_FIELDS}
            merged=metadata_patch(master,selected)
            if not merged['title'].strip():
                raise ValueError('合并后的标题不能为空。')
            merged['tags']=normalize_tags(list(dict.fromkeys(tag for document in documents.values() for tag in document.get('tags',[]))))
            contributions,notes = merge_note_contributions(documents.values())
            merged['mergeNoteSources'] = contributions
            merged['notes'] = notes
            for source in document_ids:
                if source==master_id:
                    continue
                # Move children first, preserving the single-level shape trigger.
                # 先迁移子附件，再将原主 PDF 作为附件，保持单层父子约束。
                db.execute('UPDATE desktop_attachment_links SET parent_document_id=? WHERE parent_document_id=?',(master_id,source))
                label='合并前主PDF · '+documents[source]['filename']
                db.execute('INSERT INTO desktop_attachment_links VALUES(?,?,?,?)',(master_id,source,'supplement',label[:1024]))
                db.execute('INSERT OR IGNORE INTO desktop_memberships(document_id,collection_id) SELECT ?,collection_id FROM desktop_memberships WHERE document_id=?',(master_id,source))
                db.execute('DELETE FROM desktop_memberships WHERE document_id=?',(source,))
            db.execute('UPDATE desktop_documents SET data=? WHERE id=?',(json.dumps(merged,ensure_ascii=False),master_id))
            history_id=str(uuid.uuid4());created=datetime.now(timezone.utc).isoformat()
            db.execute('INSERT INTO desktop_merge_history VALUES(?,?,?,?,?,?)',(history_id,master_id,json.dumps(document_ids),json.dumps(snapshot,ensure_ascii=False),json.dumps(preview['counts']),created))
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',('documents_merged','succeeded',created))
        return dict(masterId=master_id,documentIds=list(document_ids),mergedDocumentIds=[identifier for identifier in document_ids if identifier!=master_id],historyId=history_id)

    def list_merge_history(self, master_id):
        self.require_active(master_id)
        with self.connection() as db:
            family = self._family_ids(db,master_id)
            result = []
            for identifier in family:
                for row in db.execute('SELECT id,master_id,document_ids,snapshot,counts,created_at FROM desktop_merge_history WHERE master_id=? ORDER BY created_at,id',(identifier,)):
                    result.append(dict(id=row[0],masterId=row[1],documentIds=json.loads(row[2]),snapshot=json.loads(row[3]),counts=json.loads(row[4]),createdAt=row[5]))
            return sorted(result,key=lambda item:(item['createdAt'],item['id']))
