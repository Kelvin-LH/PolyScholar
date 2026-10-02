# SPDX-License-Identifier: AGPL-3.0-only
"""Persisted local source geometry and quote provenance; never a truth judgement."""
from datetime import datetime, timezone
import json
import math
import uuid
from .fulltext import index_current_ir

MAX_IR_PAGES = 2000
MAX_IR_BLOCKS = 50000
MAX_IR_TEXT_BYTES = 8 * 1024 * 1024
MAX_CLAIM_BYTES = 65536
MAX_PARSER_BYTES = 1024

IR_SCHEMA = '''
CREATE TABLE IF NOT EXISTS desktop_ir_revisions(
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
 sha256 TEXT NOT NULL, parser TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(document_id,id));
CREATE TABLE IF NOT EXISTS desktop_ir_current(
 document_id TEXT PRIMARY KEY REFERENCES desktop_documents(id) ON DELETE CASCADE,
 revision_id TEXT NOT NULL,
 FOREIGN KEY(document_id,revision_id) REFERENCES desktop_ir_revisions(document_id,id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS desktop_ir_pages(
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 number INTEGER NOT NULL CHECK(number>0), transform TEXT NOT NULL,
 UNIQUE(document_id,revision_id,id), UNIQUE(revision_id,number),
 FOREIGN KEY(document_id,revision_id) REFERENCES desktop_ir_revisions(document_id,id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS desktop_ir_blocks(
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL, revision_id TEXT NOT NULL, page_id TEXT NOT NULL,
 text TEXT NOT NULL, kind TEXT NOT NULL, reading_order INTEGER NOT NULL CHECK(reading_order>=0),
 bbox TEXT NOT NULL, UNIQUE(document_id,revision_id,id), UNIQUE(page_id,reading_order),
 FOREIGN KEY(document_id,revision_id,page_id) REFERENCES desktop_ir_pages(document_id,revision_id,id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS desktop_claims(
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES desktop_documents(id) ON DELETE CASCADE,
 revision_id TEXT, text TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(document_id,id),
 FOREIGN KEY(document_id,revision_id) REFERENCES desktop_ir_revisions(document_id,id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS desktop_claim_evidence(
 claim_id TEXT NOT NULL, document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 block_id TEXT NOT NULL, quote TEXT NOT NULL, PRIMARY KEY(claim_id,block_id),
 FOREIGN KEY(document_id,claim_id) REFERENCES desktop_claims(document_id,id) ON DELETE CASCADE,
 FOREIGN KEY(document_id,revision_id,block_id) REFERENCES desktop_ir_blocks(document_id,revision_id,id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS desktop_model_summaries(
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 model TEXT NOT NULL, usage TEXT NOT NULL, input_blocks TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(document_id,id),
 FOREIGN KEY(document_id,revision_id) REFERENCES desktop_ir_revisions(document_id,id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS desktop_claim_provenance(
 claim_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, summary_id TEXT NOT NULL,
 category TEXT NOT NULL, attribution TEXT NOT NULL,
 FOREIGN KEY(document_id,claim_id) REFERENCES desktop_claims(document_id,id) ON DELETE CASCADE,
 FOREIGN KEY(document_id,summary_id) REFERENCES desktop_model_summaries(document_id,id) ON DELETE CASCADE);
'''


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _integer(value, minimum):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _validated_pages(pages):
    if not isinstance(pages, list) or not pages:
        raise ValueError('DocumentIR 必须包含实际页面。')
    if len(pages) > MAX_IR_PAGES:
        raise ValueError('文档页数超过本地解析上限。')
    result = []
    seen = set()
    total_blocks = total_text = 0
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError('页面格式无效。')
        number = page.get('number')
        if not _integer(number, 1) or number in seen:
            raise ValueError('页码必须是唯一的正整数。')
        seen.add(number)
        width, height, rotation = page.get('width'), page.get('height'), page.get('rotation', 0)
        if not all(_number(v) and v > 0 for v in (width, height)) or not _integer(rotation, 0) or rotation not in (0, 90, 180, 270):
            raise ValueError('页面尺寸或旋转角度无效。')
        crop = page.get('cropBox', [0, 0, width, height])
        if not isinstance(crop, (list, tuple)) or len(crop) != 4 or not all(_number(v) for v in crop) or crop[2] <= crop[0] or crop[3] <= crop[1]:
            raise ValueError('页面裁剪坐标无效。')
        transform = dict(width=width, height=height, rotation=rotation, cropBox=list(crop), coordinateSpace='normalized_display', origin='top-left')
        # Retain an actual parser transform when provided, rather than inventing one.
        matrix = page.get('transform')
        if matrix is not None:
            if not isinstance(matrix, (list, tuple)) or len(matrix) != 6 or not all(_number(v) for v in matrix):
                raise ValueError('页面变换矩阵无效。')
            transform['matrix'] = list(matrix)
        blocks = page.get('blocks')
        if not isinstance(blocks, list):
            raise ValueError('页面文本块必须是列表。')
        total_blocks += len(blocks)
        if total_blocks > MAX_IR_BLOCKS:
            raise ValueError('文本块总数超过本地解析上限。')
        normalized = []
        orders = set()
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError('文本块格式无效。')
            text, bbox, order = block.get('text'), block.get('bbox'), block.get('order')
            kind = block.get('kind', 'text')
            if not isinstance(text, str) or kind not in ('text', 'heading', 'caption', 'figure', 'table', 'formula'):
                raise ValueError('文本块内容或类型无效。')
            total_text += len(text.encode('utf-8'))
            if total_text > MAX_IR_TEXT_BYTES:
                raise ValueError('文档文字超过本地解析上限。')
            if not _integer(order, 0) or order in orders:
                raise ValueError('阅读顺序必须是页内唯一的非负整数。')
            orders.add(order)
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or not all(_number(v) for v in bbox) or not (0 <= bbox[0] < bbox[2] <= 1 and 0 <= bbox[1] < bbox[3] <= 1):
                raise ValueError('文本块坐标必须是有限的归一化页面坐标。')
            normalized.append(dict(text=text, bbox=list(bbox), kind=kind, order=order))
        result.append(dict(number=number, transform=transform, blocks=normalized))
    if seen != set(range(1, len(result) + 1)):
        raise ValueError('DocumentIR 必须连续保存全部实际页面。')
    return sorted(result, key=lambda p: p['number'])


class DocumentIRLibrary:
    def replace_document_ir(self, document_id, parser, pages):
        if not isinstance(parser, str) or not parser.strip() or len(parser.encode('utf-8')) > MAX_PARSER_BYTES:
            raise ValueError('解析器标识不能为空。')
        pages = _validated_pages(pages)
        revision_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_pdf(document_id, db)
            source = db.execute('SELECT sha256 FROM desktop_documents WHERE id=?', (document_id,)).fetchone()
            if source is None:
                raise ValueError('文献不存在。')
            db.execute('INSERT INTO desktop_ir_revisions VALUES(?,?,?,?,?)', (revision_id, document_id, source[0], parser, created_at))
            for page in pages:
                page_id = str(uuid.uuid4())
                db.execute('INSERT INTO desktop_ir_pages VALUES(?,?,?,?,?)', (page_id, document_id, revision_id, page['number'], json.dumps(page['transform'], allow_nan=False)))
                for block in page['blocks']:
                    db.execute('INSERT INTO desktop_ir_blocks VALUES(?,?,?,?,?,?,?,?)', (str(uuid.uuid4()), document_id, revision_id, page_id, block['text'], block['kind'], block['order'], json.dumps(block['bbox'], allow_nan=False)))
            db.execute('INSERT INTO desktop_ir_current VALUES(?,?) ON CONFLICT(document_id) DO UPDATE SET revision_id=excluded.revision_id', (document_id, revision_id))
            index_current_ir(db, document_id, parsed=True)
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)', ('document_parsed', 'succeeded', created_at))
        blocks = [block for page in pages for block in page['blocks']]
        return dict(id=revision_id, documentId=document_id, sha256=source[0], parser=parser, createdAt=created_at,
                    pageCount=len(pages), blockCount=len(blocks), status='ready' if any(b['text'].strip() for b in blocks) else 'no_text')

    def current_document_ir(self, document_id):
        self.document(document_id)
        with self.connection() as db:
            row = db.execute('SELECT r.id,r.sha256,r.parser,r.created_at FROM desktop_ir_current c JOIN desktop_ir_revisions r ON r.id=c.revision_id WHERE c.document_id=?', (document_id,)).fetchone()
            if row is None:
                return None
            pages = db.execute('SELECT COUNT(*) FROM desktop_ir_pages WHERE revision_id=?', (row[0],)).fetchone()[0]
            texts = [item[0] for item in db.execute('SELECT text FROM desktop_ir_blocks WHERE revision_id=?', (row[0],))]
            blocks = len(texts)
        return dict(id=row[0], documentId=document_id, sha256=row[1], parser=row[2], createdAt=row[3], pageCount=pages, blockCount=blocks, status='ready' if any(t.strip() for t in texts) else 'no_text')

    def _ir_revision(self, document_id, revision_id):
        self.document(document_id)
        with self.connection() as db:
            if revision_id is None:
                row = db.execute('SELECT revision_id FROM desktop_ir_current WHERE document_id=?', (document_id,)).fetchone()
                return row[0] if row else None
            if not db.execute('SELECT 1 FROM desktop_ir_revisions WHERE document_id=? AND id=?', (document_id, revision_id)).fetchone():
                raise ValueError('解析版本不属于此文献。')
        return revision_id

    def document_pages(self, document_id, revision_id=None):
        revision_id = self._ir_revision(document_id, revision_id)
        with self.connection() as db:
            return [dict(id=r[0], documentId=document_id, revisionId=revision_id, number=r[1], pageTransform=json.loads(r[2])) for r in db.execute('SELECT id,number,transform FROM desktop_ir_pages WHERE document_id=? AND revision_id=? ORDER BY number', (document_id, revision_id))]

    def document_blocks(self, document_id, revision_id=None):
        revision_id = self._ir_revision(document_id, revision_id)
        with self.connection() as db:
            return [dict(id=r[0], documentId=document_id, revisionId=revision_id, pageNumber=r[1], text=r[2], kind=r[3], order=r[4], bbox=json.loads(r[5]), pageTransform=json.loads(r[6])) for r in db.execute('SELECT b.id,p.number,b.text,b.kind,b.reading_order,b.bbox,p.transform FROM desktop_ir_blocks b JOIN desktop_ir_pages p ON p.id=b.page_id WHERE b.document_id=? AND b.revision_id=? ORDER BY p.number,b.reading_order', (document_id, revision_id))]

    def save_claim(self, document_id, text, evidence):
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > MAX_CLAIM_BYTES or not isinstance(evidence, list) or len(evidence) > MAX_IR_BLOCKS:
            raise ValueError('结论文本或证据格式无效。')
        claim_id = str(uuid.uuid4())
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_pdf(document_id, db)
            if not db.execute('SELECT 1 FROM desktop_documents WHERE id=?', (document_id,)).fetchone():
                raise ValueError('文献不存在。')
            current = db.execute('SELECT revision_id FROM desktop_ir_current WHERE document_id=?', (document_id,)).fetchone()
            revision_id = current[0] if current else None
            checked = []
            seen = set()
            for item in evidence:
                if not isinstance(item, dict) or not isinstance(item.get('blockId'), str) or not isinstance(item.get('quote'), str) or not item['quote'].strip() or len(item['quote'].encode('utf-8')) > MAX_CLAIM_BYTES or item['blockId'] in seen:
                    raise ValueError('证据块或摘录格式无效。')
                seen.add(item['blockId'])
                row = db.execute('SELECT revision_id,text FROM desktop_ir_blocks WHERE document_id=? AND id=?', (document_id, item['blockId'])).fetchone()
                if row is None or item['quote'] not in row[1]:
                    raise ValueError('摘录必须逐字属于此文献的指定文本块。')
                checked.append((claim_id, document_id, row[0], item['blockId'], item['quote']))
            db.execute('INSERT INTO desktop_claims VALUES(?,?,?,?,?)', (claim_id, document_id, revision_id, text, datetime.now(timezone.utc).isoformat()))
            db.executemany('INSERT INTO desktop_claim_evidence VALUES(?,?,?,?,?)', checked)
            db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)', ('claim_created', 'succeeded', datetime.now(timezone.utc).isoformat()))
        return next(c for c in self.list_claims(document_id) if c['id'] == claim_id)

    def save_model_summary(self, document_id, revision_id, claims, model, usage, input_block_ids=None):
        """Save a validated model batch entirely or not at all, tied to its input revision."""
        if not isinstance(revision_id, str) or not isinstance(model, str) or not model.strip() or len(model.encode('utf-8')) > 1024:
            raise ValueError('模型或解析版本标识无效。')
        if not isinstance(usage, dict) or set(usage) - {'prompt_tokens', 'completion_tokens', 'total_tokens'} or any(not _integer(v, 0) for v in usage.values()):
            raise ValueError('模型用量格式无效。')
        if not isinstance(claims, list) or not 1 <= len(claims) <= 64:
            raise ValueError('模型摘要必须包含 1 至 64 项。')
        summary_id = str(uuid.uuid4())
        created = datetime.now(timezone.utc).isoformat()
        saved_ids = []
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_pdf(document_id, db)
            current = db.execute('SELECT revision_id FROM desktop_ir_current WHERE document_id=?', (document_id,)).fetchone()
            if current is None or current[0] != revision_id:
                raise ValueError('文献解析版本已变化，请重新生成摘要。')
            checked = []
            for claim in claims:
                if not isinstance(claim, dict) or not {'text', 'evidence'} <= set(claim) or set(claim) - {'text', 'evidence', 'category', 'attribution'}:
                    raise ValueError('模型摘要结构无效。')
                category = claim.get('category', 'other')
                attribution = claim.get('attribution', 'model_inference')
                if category not in ('question', 'method', 'data', 'result', 'limitation', 'reproducibility', 'other') or attribution not in ('author_report', 'model_inference'):
                    raise ValueError('模型摘要类别或归属无效。')
                text, evidence = claim['text'], claim['evidence']
                if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > MAX_CLAIM_BYTES or not isinstance(evidence, list) or len(evidence) > 64:
                    raise ValueError('模型摘要文本或证据格式无效。')
                claim_id = str(uuid.uuid4())
                seen, quotes = set(), []
                for item in evidence:
                    if not isinstance(item, dict) or set(item) != {'blockId', 'quote'} or not isinstance(item['blockId'], str) or not isinstance(item['quote'], str) or not item['quote'].strip() or len(item['quote'].encode('utf-8')) > MAX_CLAIM_BYTES or item['blockId'] in seen:
                        raise ValueError('模型证据块或摘录格式无效。')
                    seen.add(item['blockId'])
                    row = db.execute('SELECT text FROM desktop_ir_blocks WHERE document_id=? AND revision_id=? AND id=?', (document_id, revision_id, item['blockId'])).fetchone()
                    if row is None or item['quote'] not in row[0]:
                        raise ValueError('摘录必须逐字属于当前版本文献的指定文本块。')
                    quotes.append((claim_id, document_id, revision_id, item['blockId'], item['quote']))
                checked.append((claim_id, text, quotes, category, attribution))
            if input_block_ids is None:
                input_block_ids = list(dict.fromkeys(quote[3] for _, _, quotes, _, _ in checked for quote in quotes))
            if not isinstance(input_block_ids, list) or len(input_block_ids) > MAX_IR_BLOCKS or any(not isinstance(value, str) for value in input_block_ids) or len(set(input_block_ids)) != len(input_block_ids):
                raise ValueError('模型输入文本块范围无效。')
            selected = set(input_block_ids)
            for block_id in input_block_ids:
                if not db.execute('SELECT 1 FROM desktop_ir_blocks WHERE document_id=? AND revision_id=? AND id=?', (document_id, revision_id, block_id)).fetchone():
                    raise ValueError('模型输入文本块必须属于当前版本文献。')
            if any(quote[3] not in selected for _, _, quotes, _, _ in checked for quote in quotes):
                raise ValueError('模型证据必须属于本次发送的文本块。')
            db.execute('INSERT INTO desktop_model_summaries VALUES(?,?,?,?,?,?,?)', (summary_id, document_id, revision_id, model, json.dumps(usage, allow_nan=False), json.dumps(input_block_ids), created))
            for claim_id, text, quotes, category, attribution in checked:
                db.execute('INSERT INTO desktop_claims VALUES(?,?,?,?,?)', (claim_id, document_id, revision_id, text, created))
                db.executemany('INSERT INTO desktop_claim_evidence VALUES(?,?,?,?,?)', quotes)
                db.execute('INSERT INTO desktop_claim_provenance VALUES(?,?,?,?,?)', (claim_id, document_id, summary_id, category, attribution))
                db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)', ('claim_created', 'succeeded', created))
                saved_ids.append(claim_id)
            # Read while retaining the writer lock so the return matches the committed batch.
            result = [claim for claim in self._list_claims(db, document_id) if claim['id'] in saved_ids]
        return result

    def list_claims(self, document_id):
        self.document(document_id)
        with self.connection() as db:
            return self._list_claims(db, document_id)

    def _list_claims(self, db, document_id):
        current = db.execute('SELECT revision_id FROM desktop_ir_current WHERE document_id=?', (document_id,)).fetchone()
        current = current[0] if current else None
        result = []
        for claim_id, revision_id, text, created in db.execute('SELECT id,revision_id,text,created_at FROM desktop_claims WHERE document_id=? ORDER BY rowid', (document_id,)):
            evidence = [dict(blockId=r[0], quote=r[1], revisionId=r[2], pageNumber=r[3], exactQuoteValidated=True, stale=r[2] != current) for r in db.execute('SELECT e.block_id,e.quote,e.revision_id,p.number FROM desktop_claim_evidence e JOIN desktop_ir_blocks b ON b.id=e.block_id JOIN desktop_ir_pages p ON p.id=b.page_id WHERE e.claim_id=?', (claim_id,))]
            provenance = db.execute('SELECT s.id,s.model,s.usage,p.category,p.attribution,s.input_blocks FROM desktop_claim_provenance p JOIN desktop_model_summaries s ON s.id=p.summary_id WHERE p.claim_id=?', (claim_id,)).fetchone()
            origin = dict(source='model', summaryId=provenance[0], model=provenance[1], usage=json.loads(provenance[2]), category=provenance[3], attribution=provenance[4], inputBlocks=json.loads(provenance[5])) if provenance else dict(source='manual')
            result.append(dict(id=claim_id, documentId=document_id, revisionId=revision_id, text=text, createdAt=created, evidence=evidence, provenance=origin, status='evidence_linked' if evidence else 'insufficient_evidence', stale=revision_id != current or any(e['stale'] for e in evidence)))
        return result
