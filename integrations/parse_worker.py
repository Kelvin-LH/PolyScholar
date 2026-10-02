# SPDX-License-Identifier: AGPL-3.0-only
"""Local, bounded PDF text extraction. No network, OCR or model calls."""
import hashlib
import json
from pathlib import Path
import sys

MAX_PDF = 100 * 1024 * 1024
MAX_OUTPUT = 16 * 1024 * 1024
MAX_TEXT = 8 * 1024 * 1024


def extract(source, expected_hash):
    import pymupdf
    if pymupdf.VersionBind != '1.28.2':
        raise ValueError('parser_unavailable')
    with Path(source).open('rb') as stream:
        raw = stream.read(MAX_PDF + 1)
    if len(raw) > MAX_PDF or not raw.startswith(b'%PDF-') or hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('source_changed')
    pages = []
    text_bytes = 0
    blocks_count = 0
    with pymupdf.open(stream=raw, filetype='pdf') as document:
        if document.needs_pass:
            raise ValueError('encrypted_pdf')
        if not 1 <= document.page_count <= 2000:
            raise ValueError('parse_limit')
        for page in document:
            visible = page.rect
            if visible.is_empty or visible.is_infinite:
                raise ValueError('invalid_pdf')
            blocks = []
            # get_text coordinates are unrotated CropBox-relative points.
            # Rotate them into the same visible page geometry as the reader.
            for item in page.get_text('blocks', sort=True):
                if item[6] != 0 or not item[4].strip():
                    continue  # Image OCR is deliberately not fabricated.
                text = item[4]
                text_bytes += len(text.encode('utf-8'))
                blocks_count += 1
                if text_bytes > MAX_TEXT or blocks_count > 50000:
                    raise ValueError('parse_limit')
                rectangle = (pymupdf.Rect(item[:4]) * page.rotation_matrix) & visible
                if rectangle.is_empty:
                    continue
                bbox = [(rectangle.x0-visible.x0)/visible.width, (rectangle.y0-visible.y0)/visible.height,
                        (rectangle.x1-visible.x0)/visible.width, (rectangle.y1-visible.y0)/visible.height]
                blocks.append(dict(text=text, bbox=bbox, kind='text', order=len(blocks)))
            pages.append(dict(number=page.number+1, width=visible.width, height=visible.height,
                rotation=page.rotation, cropBox=list(page.cropbox),
                transform=list(page.rotation_matrix), blocks=blocks))
    return dict(ok=True, parser='pymupdf-1.28.2/native-text-v1', sha256=expected_hash, pages=pages)


def main():
    try:
        request = json.loads(sys.stdin.buffer.readline(32769))
        if not isinstance(request, dict) or set(request) != {'source', 'sha256'} or not Path(request['source']).is_absolute():
            raise ValueError('invalid_request')
        result = extract(request['source'], request['sha256'])
        output = json.dumps(result, ensure_ascii=True, allow_nan=False).encode('ascii')
        if len(output) > MAX_OUTPUT:
            raise ValueError('parse_limit')
    except Exception as error:
        allowed = {'parser_unavailable','source_changed','encrypted_pdf','parse_limit','invalid_pdf','invalid_request'}
        code = str(error) if isinstance(error, ValueError) and str(error) in allowed else 'parse_failed'
        output = json.dumps({'ok': False, 'code': code}).encode('ascii')
    sys.stdout.buffer.write(output + b'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
