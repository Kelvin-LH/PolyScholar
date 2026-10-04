# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded, local citation parsing and authenticated import previews.

受限本地引文解析与可信预览；不会执行 LaTeX、访问链接或推断缺失姓名。
Parsing never executes LaTeX, follows links, or guesses missing name parts.
"""
from collections import OrderedDict
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import json
import multiprocessing
import math
import os
import time
from pathlib import Path
import re
import threading
import uuid

from bibtexparser.bparser import BibTexParser
import rispy
from .bibliographic import BibliographicPolicy
from .metadata import ITEM_TYPES, retained_metadata_fields, validate_date, validate_metadata_url


class CitationImportPolicy:
    max_file_bytes = 8 * 1024 * 1024
    max_records = 1000
    max_cached_previews = 8
    max_cache_bytes = 32 * 1024 * 1024
    formats = ('bibtex', 'ris', 'csl-json')
    parse_timeout_seconds = 30

    @classmethod
    def read(cls, path, format):
        if format not in cls.formats:
            raise ValueError('引文格式必须是 BibTeX、RIS 或 CSL-JSON。')
        try:
            source = Path(path).resolve(strict=True)
            if not source.is_file():
                raise ValueError('请选择本地引文文件。')
            with source.open('rb') as stream:
                data = stream.read(cls.max_file_bytes + 1)
        except (OSError, TypeError, RuntimeError):
            raise ValueError('本地引文文件不存在或无法读取。') from None
        if len(data) > cls.max_file_bytes:
            raise ValueError('引文文件不能超过 8 MiB。')
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('引文文件必须使用 UTF-8 编码，可带 BOM。') from None
        if '\x00' in text:
            raise ValueError('引文文件不能包含 NUL。')
        return source, text, hashlib.sha256(data).hexdigest()

    @staticmethod
    def text(value):
        if isinstance(value, str):
            return value
        if type(value) in (int, float):
            if type(value) is float and not math.isfinite(value):
                raise ValueError('书目数字必须有限。')
            return str(value)
        raise ValueError('书目字段应为文本或数字。')

    @staticmethod
    def normalize(metadata):
        base = dict(title='', authors='', doi='', year='', tags=[], notes='')
        return BibliographicPolicy.metadata_change(base, metadata)

    @staticmethod
    def date(value, allow_ris_slash=False):
        value = CitationImportPolicy.text(value).strip()
        if not allow_ris_slash or '/' not in value:
            validate_date(value)
            return value
        if allow_ris_slash and '/' in value:
            if '-' in value:
                raise ValueError('来源日期不能混用斜线与破折号。')
            parts = value.split('/')
            if len(parts) > 3:
                raise ValueError('RIS 日期包含过多分量。')
            # Only RIS permits trailing empty precision components; ISO remains strict.
            # 仅 RIS 允许尾部空精度段；原生 ISO 破折号格式保持严格校验。
            while len(parts) > 1 and parts[-1] == '':
                parts.pop()
        else:
            parts = value.split('-')
        if not 1 <= len(parts) <= 3 or any(not re.fullmatch(r'[0-9]+', p) for p in parts):
            raise ValueError('来源日期无法转换为 YYYY、YYYY-MM 或 YYYY-MM-DD。')
        value = parts[0] + ''.join('-' + p.zfill(2) for p in parts[1:])
        validate_date(value)
        return value


class StrictBibTexParser(BibTexParser):
    """Guard the pinned parser's field reduction without replacing BibTeX grammar.

    只在已固定版本的成熟语法归并字段时拒绝重复键，不自行重写 BibTeX 语法。
    """
    def __init__(self):
        super().__init__(ignore_nonstandard_types=False, common_strings=False,
                         interpolate_strings=False, add_missing_from_crossref=False)
        stack, visited, guarded = [self._expr.entry], set(), 0
        while stack:
            expression = stack.pop()
            if id(expression) in visited:
                continue
            visited.add(id(expression))
            if expression.resultsName == 'Fields':
                expression.set_parse_action(self._unique_fields)
                guarded += 1
            stack.extend(expression.recurse())
        if not guarded:
            raise ValueError('BibTeX 解析器版本与字段校验不兼容。')

    @staticmethod
    def _unique_fields(source, location, tokens):
        fields = list(tokens.get('Fields'))
        keys = [key.casefold() for key, _ in fields]
        if len(set(keys)) != len(keys):
            raise ValueError('BibTeX 条目包含重复字段。')
        return dict(reversed(fields))


def _parse_worker(text, format, connection):
    # Parser diagnostics may contain private source lines; discard them in this child.
    # 解析器诊断可能包含私人来源行，只在子进程内丢弃这些输出。
    with open(os.devnull, 'w') as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
        if os.name == 'posix':
            os.dup2(quiet.fileno(), 1)
            os.dup2(quiet.fileno(), 2)
        _parse_worker_result(text, format, connection)


def _parse_worker_result(text, format, connection):
    """Pure spawned parser: no GUI, store, network or executable citation content.

    独立解析进程只处理字符串，不接触界面、数据库、网络或可执行引文内容。
    """
    try:
        items, warnings = CitationImporter()._items(text, format)
        payload = json.dumps(dict(items=items, warnings=warnings), ensure_ascii=False).encode('utf-8')
        if len(payload) > CitationImportPolicy.max_cache_bytes:
            raise ValueError('引文解析结果超过本地 32 MiB 大小上限。')
    except BaseException:
        payload = json.dumps({'error': '引文文件无效或超出解析资源边界；未导入任何条目。'}, ensure_ascii=False).encode('utf-8')
    try:
        connection.send_bytes(payload)
    except (BrokenPipeError, OSError):
        pass  # Parent closed or timed out. 父进程已关闭或超时，不再发送结果。
    finally:
        connection.close()


class CitationImporter:
    def __init__(self):
        self._previews = OrderedDict()
        self._lock = threading.RLock()
        self._workers = set()
        self._closed = False

    @staticmethod
    def _literal(value, role, warnings):
        warnings.append('完整作者名称无法可靠区分个人与机构，按完整个人姓名保留；请在导入后核对类型。')
        return dict(type='person', role=role, literal=value)

    @staticmethod
    def _latex(value, warnings):
        for command, replacement in (('textbackslash', '\\'), ('textasciitilde', '~'), ('textasciicircum', '^')):
            value = value.replace('\\' + command + '{}', replacement)
        value = re.sub(r'\\([{}%&#_$])', r'\1', value)
        if re.search(r'\\[A-Za-z]+', value):
            warnings.append('LaTeX 命令按文本保留，未执行或完整转换；请核对显示。')
        return value

    @staticmethod
    def _assign(metadata, target, value):
        if target in metadata and metadata[target] != value:
            raise ValueError('来源别名字段内容冲突：' + target)
        metadata[target] = value

    @staticmethod
    def _json_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('CSL-JSON 包含重复字段。')
            result[key] = value
        return result

    @staticmethod
    def _nonfinite(value):
        raise ValueError('CSL-JSON 不能包含非有限数字。')

    @staticmethod
    def _finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('CSL-JSON 不能包含非有限数字。')
        return result

    def _bib_names(self, value, role, warnings):
        # Split only top-level name delimiters, preserving protected corporate words.
        # 只分割顶层姓名分隔符，保留花括号中机构名称的 and。
        names, start, depth, offset = [], 0, 0, 0
        while offset < len(value):
            if value[offset] == '\\':
                offset += 2
                continue
            if value[offset] == '{':
                depth += 1
            if value[offset] == '}':
                depth -= 1
            match = re.compile(r'\s+and\s+', re.I).match(value,offset) if depth == 0 else None
            if match:
                names.append(value[start:offset].strip())
                offset = match.end()
                start = offset
            else:
                offset += 1
        names.append(value[start:].strip())
        result = []
        for name in names:
            protected = False
            depth = 0
            comma_positions = []
            outer_closes = []
            for offset, character in enumerate(name):
                if offset and name[offset-1] == '\\':
                    continue
                if character == '{':
                    depth += 1
                elif character == '}':
                    depth -= 1
                    if depth == 0:
                        outer_closes.append(offset)
                    if depth == 0 and offset == len(name)-1 and name.startswith('{'):
                        protected = not comma_positions
                    elif depth == 0:
                        protected = False
                elif character == ',' and depth == 0:
                    comma_positions.append(offset)
            # A whole protected group closes for the first time only at the end.
            # 整体保护名称的首个外层结束括号必须在末尾。
            protected = name.startswith('{') and outer_closes == [len(name)-1] and not comma_positions
            if protected:
                result.append(self._literal(self._latex(name[1:-1], warnings), role, warnings))
            elif len(comma_positions) == 1:
                family, given = [self._latex(part.strip().strip('{}'), warnings) for part in name.split(',', 1)]
                result.append(dict(type='person', role=role, family=family, given=given))
            else:
                result.append(self._literal(self._latex(name, warnings), role, warnings))
        return result

    @staticmethod
    def _unknown(record, used, warnings):
        for key in sorted(set(record) - used):
            warnings.append('未导入来源字段：' + str(key))

    def _bibtex(self, record, warnings):
        source_type = record.get('ENTRYTYPE', '').lower()
        kinds = {'article':'article-journal', 'inproceedings':'paper-conference',
                 'conference':'paper-conference', 'book':'book', 'phdthesis':'thesis',
                 'mastersthesis':'thesis', 'thesis':'thesis'}
        if source_type == 'misc' and record.get('polyscholaritemtype') == 'arxiv-preprint':
            kind = 'arxiv-preprint'
        elif source_type == 'misc' and record.get('archiveprefix', '').casefold() == 'arxiv':
            kind = 'arxiv-preprint'
        elif source_type == 'misc' and record.get('polyscholaritemtype') == 'thesis':
            kind = 'thesis'
        elif source_type == 'misc' and isinstance(record.get('type'), str) and re.search(r'\b(thesis|dissertation|master|doctor|phd)\b',record['type'],re.I):
            kind = 'thesis'
            warnings.append('misc 按来源明确的论文类型导入为学位论文，请核对类型。')
        else:
            kind = kinds.get(source_type)
        if kind is None:
            raise ValueError('不支持的 BibTeX 文献类型：' + source_type)
        metadata = dict(itemType=kind, creators=[])
        mapping = {'title':'title','doi':'doi','year':'year','date':'date','journal':'publicationTitle',
                   'booktitle':'publicationTitle','publisher':'publisher','address':'place','volume':'volume',
                   'number':'issue','pages':'pages','isbn':'isbn','edition':'edition','eventtitle':'eventTitle',
                   'school':'institution','institution':'institution','type':'thesisType','note':'notes','url':'url','abstract':'abstract'}
        for source, target in mapping.items():
            if source in record:
                if not isinstance(record[source],str):
                    warnings.append('未展开的 BibTeX 宏字段：'+source)
                    raise ValueError('BibTeX 宏字段需转换为明确文本。')
                value = self._latex(record[source], warnings)
                if target == 'pages':
                    value = value.replace('--', '-')
                self._assign(metadata, target, CitationImportPolicy.date(value) if target == 'date' else value)
        for role in ('author', 'editor'):
            if role in record:
                metadata['creators'] += self._bib_names(record[role], role, warnings)
        if source_type in ('phdthesis','mastersthesis'):
            metadata.setdefault('thesisType', 'phd' if source_type == 'phdthesis' else 'master')
        if 'keywords' in record:
            metadata['tags'] = [tag.strip() for tag in re.split(r'[,;]', record['keywords']) if tag.strip()]
        self._unknown(record, {'ID','ENTRYTYPE','author','editor','keywords','polyscholaritemtype','archiveprefix',*mapping}, warnings)
        return metadata

    def _ris(self, record, warnings):
        kind = {'JOUR':'article-journal','CPAPER':'paper-conference','CONF':'paper-conference',
                'BOOK':'book','THES':'thesis'}.get(record.get('TY'))
        if record.get('TY') == 'UNPB' and record.get('M3') == 'arxiv-preprint':
            kind = 'arxiv-preprint'
        if kind is None:
            raise ValueError('不支持的 RIS 文献类型。')
        metadata = dict(itemType=kind, creators=[])
        mapping = {'TI':'title','T1':'title','DO':'doi','PY':'year','Y1':'year','DA':'date',
                   'T2':'publicationTitle','JF':'publicationTitle','JO':'publicationTitle','PB':'publisher',
                   'CY':'place','VL':'volume','IS':'issue','SN':'isbn','ET':'edition','T3':'eventTitle','M3':'thesisType','UR':'url','AB':'abstract'}
        if kind == 'thesis':
            mapping['PB'] = 'institution'
        if kind == 'arxiv-preprint':
            mapping.pop('M3')
        if kind == 'article-journal':
            mapping.pop('SN')
            warnings.extend(['期刊 SN 可能为 ISSN，未作为 ISBN 导入。'] if 'SN' in record else [])
        for source, target in mapping.items():
            if source in record:
                value = record[source]
                if isinstance(value,list):
                    raise ValueError('来源单值字段重复：' + source)
                if target in ('year','date'):
                    date = CitationImportPolicy.date(value, allow_ris_slash=True)
                    if target == 'year':
                        self._assign(metadata,'year',date[:4])
                        if '-' in date:
                            self._assign(metadata,'date',date)
                    else:
                        self._assign(metadata,'date',date)
                else:
                    self._assign(metadata,target,value)
        for tags, role in ((('AU',),'author'),(('A2',),'editor')):
            for tag in tags:
                for name in record.get(tag,[]):
                    if name.count(',') == 1:
                        family,given = [p.strip() for p in name.split(',',1)]
                        metadata['creators'].append(dict(type='person',role=role,family=family,given=given))
                    else:
                        metadata['creators'].append(self._literal(name,role,warnings))
        if 'SP' in record:
            metadata['pages'] = record['SP'] + ('-'+record['EP'] if record.get('EP') else '')
        elif 'EP' in record:
            raise ValueError('RIS 结束页缺少开始页。')
        if 'KW' in record:
            metadata['tags'] = record['KW']
        if 'N1' in record:
            metadata['notes'] = '\n'.join(record['N1'])
        if 'UK' in record:
            warnings.extend('未导入来源字段：'+key for key in sorted(record['UK']))
        self._unknown(record, {'TY','ID','AU','A1','A2','SP','EP','KW','N1','UK','M3',*mapping}, warnings)
        return metadata

    def _csl(self, record, warnings):
        kind = record.get('type')
        if kind == 'article' and record.get('polyscholar-item-type') == 'arxiv-preprint':
            kind = 'arxiv-preprint'
        if kind not in ITEM_TYPES:
            raise ValueError('不支持的 CSL 文献类型。')
        metadata = dict(itemType=kind, creators=[])
        mapping = {'title':'title','DOI':'doi','container-title':'publicationTitle','publisher':'publisher',
                   'publisher-place':'place','volume':'volume','issue':'issue','page':'pages','ISBN':'isbn',
                   'edition':'edition','event-title':'eventTitle','genre':'thesisType','note':'notes','URL':'url','abstract':'abstract'}
        if kind == 'thesis':
            mapping['publisher'] = 'institution'
        for source,target in mapping.items():
            if source in record:
                metadata[target] = CitationImportPolicy.text(record[source])
        for role in ('author','editor'):
            names = record.get(role, [])
            if not isinstance(names,list):
                raise ValueError('CSL 作者或编者必须是列表。')
            for name in names:
                if not isinstance(name,dict):
                    raise ValueError('CSL 作者字段无效。')
                if 'literal' in name:
                    if 'family' in name or 'given' in name:
                        raise ValueError('CSL 完整名称与姓、名不能混用。')
                    creator=self._literal(CitationImportPolicy.text(name['literal']),role,warnings)
                else:
                    creator=dict(type='person',role=role,family=name.get('family',''),given=name.get('given',''))
                self._unknown(name, {'literal','family','given'}, warnings)
                metadata['creators'].append(creator)
        if 'issued' in record:
            issued=record['issued']
            if not isinstance(issued,dict):
                raise ValueError('CSL 日期字段无效。')
            if 'date-parts' in issued:
                parts=issued['date-parts']
                if not isinstance(parts,list) or len(parts)!=1 or not isinstance(parts[0],list) or not 1<=len(parts[0])<=3 or any(type(p) is not int for p in parts[0]):
                    raise ValueError('CSL 日期范围或日期分量无法无损导入。')
                metadata['date']=CitationImportPolicy.date('-'.join(str(p).zfill(4 if index == 0 else 2) for index,p in enumerate(parts[0])))
            elif 'raw' in issued:
                metadata['date']=CitationImportPolicy.date(issued['raw'])
            elif 'literal' in issued:
                metadata['date']=CitationImportPolicy.date(issued['literal'])
            else:
                raise ValueError('CSL 缺少可导入的日期。')
            self._unknown(issued, {'date-parts'} if 'date-parts' in issued else {'raw'} if 'raw' in issued else {'literal'}, warnings)
        if 'categories' in record:
            metadata['tags']=record['categories']
        self._unknown(record, {'id','type','author','editor','issued','categories','polyscholar-item-type',*mapping}, warnings)
        return metadata

    def _items(self, text, format):
        warnings = []
        try:
            if format == 'csl-json':
                records = json.loads(text,object_pairs_hook=self._json_object,parse_constant=self._nonfinite,parse_float=self._finite_float)
                if not isinstance(records,list):
                    raise ValueError('CSL-JSON 顶层必须是条目列表。')
            elif format == 'bibtex':
                parser = StrictBibTexParser()
                database = parser.parse(text,partial=False)
                records = database.entries
                if database.preambles:
                    warnings.append('BibTeX preamble 未执行或导入。')
                if any(re.match(r'^\s*@\w+\s*[({]', comment) for comment in database.comments):
                    raise ValueError('BibTeX 含无法解析的条目，未截断导入。')
                if database.comments:
                    warnings.append('BibTeX 注释未作为书目导入。')
                if database.strings:
                    warnings.append('BibTeX string 宏定义未展开；引用宏的字段需要改为明确文本后导入。')
            else:
                # rispy discards unfinished records; enforce framing before its mature parser.
                # rispy 会忽略未结束记录，因此先核对 TY/ER 边界，防止静默丢条目。
                active = False
                count = 0
                for line in text.splitlines():
                    if re.match(r'^TY\s+-',line):
                        if active:
                            raise ValueError('RIS 条目缺少 ER 结束标记。')
                        active=True
                        count+=1
                    elif re.match(r'^ER\s+-',line):
                        if not active:
                            raise ValueError('RIS 结束标记没有对应条目。')
                        active=False
                    elif line.strip() and not active:
                        raise ValueError('RIS 条目之外存在未识别内容。')
                if active:
                    raise ValueError('RIS 条目缺少 ER 结束标记。')
                # Alias tags share one list so rispy retains their source order.
                # 两个作者别名共用列表，由成熟解析器保留混用时的来源顺序。
                mapping = {key:key for key in rispy.TAG_KEY_MAPPING}
                mapping['A1'] = 'AU'
                records=rispy.loads(text,mapping=mapping,
                    list_tags=['AU','A1','A2','KW','N1'],delimiter_tags_mapping={},ignore=[],enforce_list_tags=False)
                if len(records)!=count:
                    raise ValueError('RIS 解析条目数与边界标记不一致。')
        except ValueError:
            raise
        except Exception:
            raise ValueError('引文文件语法无效，无法完整解析；未导入任何条目。') from None
        if not records:
            raise ValueError('引文文件没有书目条目。')
        if len(records)>CitationImportPolicy.max_records:
            raise ValueError('引文文件不能超过 1000 条，不会截断导入。')
        converter={'bibtex':self._bibtex,'ris':self._ris,'csl-json':self._csl}[format]
        items=[]
        for index,record in enumerate(records):
            messages=[]
            errors=[]
            metadata=None
            try:
                if not isinstance(record,dict):
                    raise ValueError('书目条目必须是对象。')
                for field_value in record.values():
                    if isinstance(field_value,str) and len(field_value.encode('utf-8'))>65536:
                        raise ValueError('来源单字段超过 64 KiB。')
                converted = converter(record,messages)
                if converted.get('url'):
                    try:
                        validate_metadata_url(converted['url'])
                    except ValueError:
                        # Invalid external URLs were historically ignored, never opened.
                        # 历史无效外链仍忽略并提示，绝不打开私人路径或活动协议。
                        converted.pop('url')
                        messages.append('未导入无效或非网页网址字段。')
                metadata=CitationImportPolicy.normalize(converted)
                retained=retained_metadata_fields(metadata)
                if retained:
                    messages.append('已保留但当前类型导出不使用字段：'+', '.join(retained))
            except (ValueError,TypeError,KeyError,AttributeError):
                # Failure details must not expose parser internals or private file paths.
                # 错误不暴露解析器内部信息或私人路径，只提示核对该条的类型及字段。
                errors.append('条目类型、标题、姓名或日期字段无效；请修正来源文件后重新预览。')
                metadata=None
            source_id=str(record.get('ID',record.get('id',''))) if isinstance(record,dict) else ''
            items.append(dict(index=index,sourceId=source_id,title=metadata['title'] if metadata else (str(next((record[key] for key in ('title','TI','T1') if key in record),''))[:65536] if isinstance(record,dict) else ''),valid=not errors,
                metadata=metadata,warnings=list(dict.fromkeys(messages)),errors=errors))
        return items,warnings

    @staticmethod
    def _stop_worker(worker):
        if worker.is_alive():
            worker.terminate()
        worker.join(timeout=1)
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=1)

    def _parse(self, text, format):
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        worker = context.Process(target=_parse_worker, args=(text, format, sender), daemon=True)
        deadline = time.monotonic() + CitationImportPolicy.parse_timeout_seconds
        received = threading.Event()
        channel_result = []
        reader = None

        def receive():
            try:
                channel_result.append(receiver.recv_bytes(maxlength=CitationImportPolicy.max_cache_bytes))
            except (EOFError, OSError):
                channel_result.append(None)
            finally:
                received.set()

        try:
            with self._lock:
                if self._closed:
                    raise ValueError('应用正在关闭，无法解析引文。')
                worker.start()
                self._workers.add(worker)
            sender.close()
            # A packet header alone is not a completed response. Supervise the entire read.
            # 数据包头不代表完整结果；父进程监督完整接收，并在超时后终止发送端。
            reader = threading.Thread(target=receive, daemon=True)
            reader.start()
            while not received.wait(timeout=min(0.05, max(0, deadline-time.monotonic()))):
                with self._lock:
                    if self._closed:
                        raise ValueError('应用正在关闭，解析结果未导入。')
                if time.monotonic() >= deadline:
                    raise ValueError('引文解析超过 30 秒，请简化来源文件后重试。')
            if time.monotonic() > deadline:
                raise ValueError('引文解析超过 30 秒，请简化来源文件后重试。')
            if not channel_result or channel_result[0] is None:
                raise ValueError('引文解析进程未完整返回结果，未导入任何条目。')
            result = json.loads(channel_result[0].decode('utf-8'))
            if 'error' in result:
                raise ValueError(result['error'])
            with self._lock:
                if self._closed:
                    raise ValueError('应用正在关闭，解析结果未导入。')
            return result['items'], result['warnings']
        except (EOFError, OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError('引文解析进程未完整返回结果，未导入任何条目。') from None
        finally:
            sender.close()
            if worker.pid is not None:
                self._stop_worker(worker)
            # Closing the child writer causes EOF even for an incomplete frame.
            # 先关闭子进程写端，使不完整数据包的接收线程通过 EOF 退出。
            if reader is not None:
                reader.join(timeout=1)
            receiver.close()
            with self._lock:
                self._workers.discard(worker)

    def preview(self, path, format):
        source, text, fingerprint = CitationImportPolicy.read(path, format)
        items, warnings = self._parse(text, format)
        preview = dict(
            token=str(uuid.uuid4()), format=format, sourceName=source.name,
            fingerprint=fingerprint, total=len(items),
            validCount=sum(item['valid'] for item in items),
            invalidCount=sum(not item['valid'] for item in items),
            warnings=warnings, items=items,
        )
        size = len(json.dumps(preview, ensure_ascii=False).encode('utf-8'))
        if size > CitationImportPolicy.max_cache_bytes:
            raise ValueError('引文预览超过本地 32 MiB 大小上限。')
        with self._lock:
            if self._closed:
                raise ValueError('应用正在关闭，预览未保留。')
            while self._previews:
                cached_bytes = sum(entry[2] for entry in self._previews.values())
                if (len(self._previews) < CitationImportPolicy.max_cached_previews
                        and cached_bytes + size <= CitationImportPolicy.max_cache_bytes):
                    break
                self._previews.popitem(last=False)
            self._previews[preview['token']] = (deepcopy(preview), source, size)
        return preview

    def ensure_preview(self, preview):
        with self._lock:
            if self._closed or not isinstance(preview, dict) or not isinstance(preview.get('token'), str):
                raise ValueError('请重新生成可信引文预览。')
            cached = self._previews.get(preview['token'])
            if cached is None or preview != cached[0]:
                raise ValueError('引文预览已失效或被修改，请重新预览。')
            return cached

    def selected(self, preview, indices):
        cached = self.ensure_preview(preview)
        if (not isinstance(indices, list) or not indices or len(indices) > CitationImportPolicy.max_records
                or any(type(i) is not int or not 0 <= i < len(preview['items']) for i in indices)
                or len(set(indices)) != len(indices)):
            raise ValueError('请选择不重复的有效书目条目。')
        _, text, fingerprint = CitationImportPolicy.read(cached[1], preview['format'])
        if fingerprint != preview['fingerprint']:
            raise ValueError('来源文件已变化，请重新预览后导入。')
        items, _ = self._parse(text, preview['format'])
        if items != cached[0]['items']:
            raise ValueError('引文解析结果已变化，请重新预览。')
        if any(not items[index]['valid'] for index in indices):
            raise ValueError('不能导入含错误的条目。')
        self.ensure_preview(preview)
        return [CitationImportPolicy.normalize(items[index]['metadata']) for index in indices]

    def consumed(self, preview):
        with self._lock:
            self._previews.pop(preview['token'], None)

    def clear(self):
        with self._lock:
            self._previews.clear()

    def close(self):
        with self._lock:
            self._closed = True
            self._previews.clear()
            workers = list(self._workers)
        for worker in workers:
            self._stop_worker(worker)
