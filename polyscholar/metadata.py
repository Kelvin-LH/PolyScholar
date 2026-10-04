# SPDX-License-Identifier: AGPL-3.0-only
"""Validated local bibliographic records and portable metadata exchange.

校验本地书目记录，并生成可交换的元数据；不推断姓名或学位类型。
Names and degree types are preserved without inference.
"""
from datetime import date as calendar_date
import json
import re
from urllib.parse import urlsplit

ITEM_TYPE_LABELS = {
    'article-journal': '期刊论文', 'paper-conference': '会议论文',
    'book': '图书', 'thesis': '学位论文', 'arxiv-preprint': 'arXiv 预印本',
}
ITEM_TYPES = tuple(ITEM_TYPE_LABELS)
BIB_FIELDS = ('publicationTitle', 'publisher', 'place', 'date', 'volume', 'issue', 'pages', 'isbn', 'edition', 'eventTitle', 'institution', 'thesisType', 'url', 'abstract')
APPLICABLE = {
    'article-journal': {'publicationTitle', 'date', 'volume', 'issue', 'pages'},
    'paper-conference': {'publicationTitle', 'publisher', 'place', 'date', 'pages', 'isbn', 'eventTitle'},
    'book': {'publisher', 'place', 'date', 'volume', 'isbn', 'edition'},
    'thesis': {'institution', 'place', 'date', 'thesisType'},
    'arxiv-preprint': {'date'},
}
# URL and abstract are shared metadata, not a journal-specific field.
# 网址及摘要属于通用元数据，不随切换条目类型丢失。
for _fields in APPLICABLE.values():
    _fields.update({'url', 'abstract'})

def _text(value):
    if not isinstance(value, str) or len(value.encode('utf-8')) > 65536 or '\x00' in value:
        raise ValueError('文献元数据必须是最多 64 KiB 的文本。')
    return value

def validate_metadata_url(value):
    """Store web references without fetching or accepting active URL schemes.
    只保存网页引用，不联网读取，也不接受活动协议或嵌入凭据。
    """
    _text(value)
    if not value:
        return value
    try:
        parsed = urlsplit(value)
        parsed.port
        valid = (parsed.scheme in ('https', 'http') and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not any(ord(char) < 32 or char.isspace() for char in value))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('文献网址必须是无内嵌凭据的 HTTP 或 HTTPS 地址。')
    return value


def validate_date(value):
    _text(value)
    if not value:
        return value
    if not re.fullmatch(r'[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?', value):
        raise ValueError('日期格式应为 YYYY、YYYY-MM 或 YYYY-MM-DD。')
    parts = [int(p) for p in value.split('-')]
    try:
        calendar_date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError:
        raise ValueError('日期无效。') from None
    return value

def legacy_creators(authors):
    return [dict(role='author', type='person', literal=value.strip(), family='', given='')
            for value in authors.split(';') if value.strip()]

def validate_creators(creators):
    if not isinstance(creators, list) or len(creators) > 1000:
        raise ValueError('作者或编者列表无效。')
    result = []
    for creator in creators:
        if not isinstance(creator, dict) or set(creator) - {'role', 'type', 'literal', 'family', 'given'}:
            raise ValueError('作者或编者字段无效。')
        role, kind = creator.get('role', 'author'), creator.get('type', 'person')
        if role not in ('author', 'editor') or kind not in ('person', 'organization'):
            raise ValueError('作者或编者类型无效。')
        literal, family, given = [_text(creator.get(key, '')).strip() for key in ('literal', 'family', 'given')]
        if not (literal or family or given) or (literal and (family or given)) or (kind == 'organization' and (not literal or family or given)):
            raise ValueError('个人姓名请选择完整姓名或姓与名；机构名称使用完整名称。')
        result.append(dict(role=role, type=kind, literal=literal, family=family, given=given))
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 65536:
        raise ValueError('作者或编者列表不能超过 64 KiB。')
    return result

def creator_display(creator):
    return creator.get('literal') or ' '.join(p for p in (creator.get('given'), creator.get('family')) if p)

def normalize_metadata(document):
    result = dict(document)
    result.setdefault('itemType', 'article-journal')
    result.setdefault('creators', legacy_creators(result.get('authors', '')))
    for field in BIB_FIELDS:
        result.setdefault(field, '')
    return result

def retained_metadata_fields(document):
    document = normalize_metadata(document)
    return [field for field in BIB_FIELDS if document[field] and field not in APPLICABLE[document['itemType']]]

def metadata_patch(document, patch):
    result = normalize_metadata(document)
    if 'itemType' in patch and patch['itemType'] not in ITEM_TYPES:
        raise ValueError('文献类型无效。')
    for field in BIB_FIELDS:
        if field in patch:
            _text(patch[field])
    if 'date' in patch:
        validate_date(patch['date'])
    if 'url' in patch:
        validate_metadata_url(patch['url'])
    result.update(patch)
    if 'creators' in patch:
        result['creators'] = validate_creators(patch['creators'])
        result['authors'] = '; '.join(creator_display(c) for c in result['creators'] if c['role'] == 'author')
    elif 'authors' in patch:
        result['creators'] = validate_creators(legacy_creators(patch['authors']))
    if result['date'] and 'year' in patch and patch['year'] != result['date'][:4]:
        raise ValueError('年份与日期中的年份不一致。')
    if 'date' in patch and 'year' not in patch:
        result['year'] = patch['date'][:4]
    return result

def _line(value):
    return re.sub(r'[\r\n\x00-\x1f\x7f\u0085\u2028\u2029]', ' ', str(value))

def _bib(value):
    return ''.join('\\textbackslash{}' if c == '\\' else '\\' + c if c in '{}%&#_$' else '\\textasciitilde{}' if c == '~' else '\\textasciicircum{}' if c == '^' else c for c in _line(value))

def exchange_metadata(document, format):
    doc = normalize_metadata(document)
    kind = doc['itemType']
    fields = {key: doc[key] for key in APPLICABLE[kind] if doc[key]}
    issued = fields.get('date') or (doc.get('year') if re.fullmatch(r'[0-9]{4}', doc.get('year', '')) else '')
    year = issued[:4] if issued else ''
    creators = validate_creators(doc['creators'])
    if format == 'csl-json':
        item = dict(id=doc['id'], type='article' if kind == 'arxiv-preprint' else kind, title=doc['title'])
        if kind == 'arxiv-preprint':
            # Standard CSL type plus explicit identity extension; 标准 CSL 类型与显式身份扩展。
            item['polyscholar-item-type'] = kind
        for role in ('author', 'editor'):
            names = []
            for creator in creators:
                if creator['role'] == role:
                    names.append({'literal': creator['literal']} if creator['literal'] else {k: creator[k] for k in ('family', 'given') if creator[k]})
            if names:
                item[role] = names
        mapping = {'publicationTitle':'container-title', 'publisher':'publisher', 'place':'publisher-place', 'volume':'volume', 'issue':'issue', 'pages':'page', 'isbn':'ISBN', 'edition':'edition', 'eventTitle':'event-title', 'institution':'publisher', 'thesisType':'genre', 'url':'URL', 'abstract':'abstract'}
        for source, target in mapping.items():
            if source in fields:
                item[target] = fields[source]
        if doc.get('doi'):
            item['DOI'] = doc['doi']
        if issued:
            item['issued'] = {'date-parts': [[int(p) for p in issued.split('-')]]}
        return json.dumps([item], ensure_ascii=False, indent=2)
    if format == 'bibtex':
        entry = {'article-journal':'article', 'paper-conference':'inproceedings', 'book':'book', 'thesis':'phdthesis', 'arxiv-preprint':'misc'}[kind]
        # Generic theses do not assert a doctorate; BibTeX uses @misc + type.
        # 普通学位论文不推断博士学位；BibTeX 用 @misc + type，CSL/RIS 保留论文类型。
        if kind == 'thesis':
            entry = 'misc'
        values = [('title', _bib(doc['title']))]
        for role in ('author', 'editor'):
            names = []
            for creator in creators:
                if creator['role'] == role:
                    if creator['literal']:
                        names.append('{' + _bib(creator['literal']) + '}')
                    else:
                        names.append('{' + _bib(creator['family']) + '}' + (', {' + _bib(creator['given']) + '}' if creator['given'] else ''))
            if names:
                values.append((role, ' and '.join(names)))
        mapping = {'publicationTitle':'journal' if kind == 'article-journal' else 'booktitle', 'publisher':'publisher', 'place':'address', 'volume':'volume', 'issue':'number', 'pages':'pages', 'isbn':'isbn', 'edition':'edition', 'eventTitle':'eventtitle', 'institution':'school', 'date':'date', 'url':'url', 'abstract':'abstract'}
        values += [(target, _bib(fields[source])) for source, target in mapping.items() if source in fields]
        if kind == 'thesis':
            values.append(('type', _bib(fields.get('thesisType') or 'Thesis')))
            # 通用 @misc 不推断学位；显式标记保证本软件回读时保留论文类型。
            # Generic @misc must not imply a degree; an explicit marker preserves thesis round trips.
            values.append(('polyscholaritemtype', 'thesis'))
        if kind == 'arxiv-preprint':
            values.append(('polyscholaritemtype', 'arxiv-preprint'))
            values.append(('archiveprefix', 'arXiv'))
        if year:
            values.append(('year', year))
        if doc.get('doi'):
            values.append(('doi', _bib(doc['doi'])))
        return '@' + entry + '{polyscholar_' + re.sub(r'[^A-Za-z0-9_]', '_', doc['id']) + ',\n' + ',\n'.join('  ' + key + ' = {' + value + '}' for key, value in values) + '\n}\n'
    if format == 'ris':
        lines = ['TY  - ' + {'article-journal':'JOUR', 'paper-conference':'CPAPER', 'book':'BOOK', 'thesis':'THES', 'arxiv-preprint':'UNPB'}[kind], 'TI  - ' + _line(doc['title'])]
        for creator in creators:
            name = creator['literal'] or creator['family'] + (', ' + creator['given'] if creator['given'] else '')
            lines.append(('AU' if creator['role'] == 'author' else 'A2') + '  - ' + _line(name))
        mapping = {'publicationTitle':'T2', 'publisher':'PB', 'place':'CY', 'volume':'VL', 'issue':'IS', 'isbn':'SN', 'edition':'ET', 'eventTitle':'T3', 'institution':'PB', 'thesisType':'M3', 'url':'UR', 'abstract':'AB'}
        lines += [target + '  - ' + _line(fields[source]) for source, target in mapping.items() if source in fields]
        if kind == 'arxiv-preprint':
            lines.append('M3  - arxiv-preprint')
        if fields.get('pages'):
            match = re.fullmatch(r'([^\s-]+)\s*[-–]\s*([^\s-]+)', fields['pages'])
            lines += ['SP  - ' + _line(match[1]), 'EP  - ' + _line(match[2])] if match else ['SP  - ' + _line(fields['pages'])]
        if year:
            lines.append('PY  - ' + year)
        if issued:
            lines.append('DA  - ' + issued.replace('-', '/'))
        if doc.get('doi'):
            lines.append('DO  - ' + _line(doc['doi']))
        return '\n'.join([*lines, 'ER  -']) + '\n'
    raise ValueError('不支持的元数据格式。')
