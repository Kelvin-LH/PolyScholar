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
