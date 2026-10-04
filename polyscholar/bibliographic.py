# SPDX-License-Identifier: AGPL-3.0-only
"""Bibliographic identities are independent from real PDF identities.

书目身份与真实 PDF 身份分开；无文件条目不生成 PDF、哈希或解析文本。
A fileless item never creates a PDF, synthetic digest or parsed text.
"""
import re
from .metadata import BIB_FIELDS, metadata_patch
from .library import normalize_tags


class BibliographicPolicy:
    editable_fields = frozenset({
        'title', 'authors', 'doi', 'year', 'tags', 'notes',
        'itemType', 'creators', *BIB_FIELDS,
    })

    @staticmethod
    def is_pdf(document):
        digest = document.get('sha256')
        return (document.get('fileKind') != 'bibliographic'
                and isinstance(digest, str)
                and re.fullmatch(r'[0-9a-f]{64}', digest) is not None)

    @classmethod
    def require_pdf(cls, document):
        if not cls.is_pdf(document):
            raise ValueError('此条目没有 PDF，请先添加并选择真实 PDF 后操作。')

    @classmethod
    def identity(cls, document):
        result = dict(document)
        result['hasPdf'] = cls.is_pdf(document)
        result['fileKind'] = 'pdf' if result['hasPdf'] else 'bibliographic'
        return result

    @classmethod
    def metadata_change(cls, document, patch):
        if not isinstance(patch, dict) or set(patch)-cls.editable_fields:
            raise ValueError('文献修改字段无效。')
        patch = dict(patch)
        for key, value in patch.items():
            if key == 'creators':
                continue
            if key == 'tags':
                patch[key] = normalize_tags(value)
            elif not isinstance(value, str):
                raise ValueError('文献元数据必须是文本。')
            else:
                try:
                    size = len(value.encode('utf-8'))
                except UnicodeEncodeError:
                    raise ValueError('文献元数据包含无效文本编码。') from None
                if size > 65536:
                    raise ValueError('每个文献元数据字段不能超过 64 KiB。')
        try:
            result = metadata_patch(document, patch)
        except UnicodeEncodeError:
            raise ValueError('文献元数据包含无效文本编码。') from None
        if not result['title'].strip():
            raise ValueError('标题不能为空。')
        return result
