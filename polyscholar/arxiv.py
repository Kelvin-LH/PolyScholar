# SPDX-License-Identifier: AGPL-3.0-only
"""arXiv metadata and PDF retrieval. Only paper identifiers are sent; no local data leaves the machine."""
import re
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

API_URL = 'https://export.arxiv.org/api/query'
USER_AGENT = 'PolyScholar-local/0.1 (personal desktop research tool)'
MAX_ENTRIES = 50
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_PDF_BYTES = 100 * 1024 * 1024
DOWNLOAD_TIMEOUT = 120
FETCH_TIMEOUT = 20
ENTRY_KEYS = {'identifier', 'title', 'abstract', 'date', 'authors', 'doi', 'url', 'pdf_url'}

_NEW_ID = r'[0-9]{4}\.[0-9]{4,5}(?:v[0-9]+)?'
_OLD_ID = r'[a-z-]+(?:\.[A-Z]{2})?/[0-9]{7}(?:v[0-9]+)?'
_ID_PATTERN = re.compile(r'(?:%s|%s)' % (_NEW_ID, _OLD_ID), re.IGNORECASE)
# Bare old-style identifiers are accepted only for real arXiv archives,
# so arbitrary text like "README/x1234567" is never mistaken for a paper.
ARCHIVES = {'astro-ph', 'cond-mat', 'cs', 'econ', 'eess', 'gr-qc', 'hep-ex', 'hep-lat', 'hep-ph',
            'hep-th', 'math', 'math-ph', 'nlin', 'nucl-ex', 'nucl-th', 'physics', 'q-bio', 'q-fin',
            'quant-ph', 'stat'}


class NetworkError(ValueError):
    """连接 arXiv 失败;CLI 以独立退出码区分网络失败与一般输入错误。"""

def normalize(identifier):
    return re.sub(r'v[0-9]+$', '', identifier.strip())

def parse_identifier(text):
    if not isinstance(text, str):
        return None
    value = text.strip()
    if not value or len(value) > 300:
        return None
    match = re.search(r'(?:arxiv\.org|export\.arxiv\.org)/(?:abs|pdf|format)/(' + _ID_PATTERN.pattern + r')', value, re.IGNORECASE)
    if match:
        return normalize(match.group(1))
    if re.fullmatch(_NEW_ID, value, re.IGNORECASE):
        return normalize(value)
    if re.fullmatch(_OLD_ID, value):
        archive = re.split(r'[./]', value, maxsplit=1)[0].lower()
        if archive in ARCHIVES:
            return normalize(value)
    return None

def parse_identifiers(text):
    result = []
    for line in (text or '').splitlines():
        for candidate in re.split(r'[，,;\s]+', line):
            identifier = parse_identifier(candidate)
            if identifier and identifier not in result:
                result.append(identifier)
    return result

def _validated_url(url, allow_query=False):
    parsed = urlsplit(url if isinstance(url, str) else '')
    local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
    valid = parsed.hostname and (parsed.scheme == 'https' or (local and parsed.scheme == 'http'))
    if not valid or parsed.username or parsed.password or (not allow_query and (parsed.query or parsed.fragment)):
        raise ValueError('arXiv 地址无效。')
    return url

TIMEOUT_HINT = ('连接 arXiv 超时。国内网络对 arXiv 的直连可能不稳定，请稍后重试；'
                '如需代理可设置 HTTPS_PROXY/HTTP_PROXY 环境变量后重启应用（MCP 场景写入 MCP 配置的 env）。')

def _open(url, cap, timeout):
    _validated_url(url, allow_query=True)
    request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'}, method='GET')
    try:
        with build_opener().open(request, timeout=timeout) as response:
            return response.read(cap + 1)
    except HTTPError as error:
        raise NetworkError('arXiv 请求失败（HTTP %d）。' % error.code) from None
    except TimeoutError:
        raise NetworkError(TIMEOUT_HINT) from None
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise NetworkError(TIMEOUT_HINT) from None
        raise NetworkError('无法连接 arXiv，请检查网络后重试；需要代理时设置 HTTPS_PROXY 环境变量。') from None
    except OSError:
        raise NetworkError('无法连接 arXiv，请检查网络后重试；需要代理时设置 HTTPS_PROXY 环境变量。') from None

def _entry_text(element):
    return ' '.join((element.text or '').split()) if element is not None else ''

def fetch_metadata(identifiers, endpoint=API_URL):
    identifiers = [normalize(i) for i in identifiers if isinstance(i, str) and len(i) <= 64][:MAX_ENTRIES]
    if not identifiers:
        raise ValueError('没有可查询的 arXiv 编号。')
    url = endpoint + '?' + urlencode({'id_list': ','.join(identifiers), 'max_results': str(len(identifiers))})
    raw = _open(url, MAX_METADATA_BYTES, FETCH_TIMEOUT)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError('arXiv 返回数据过大。')
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        raise ValueError('无法解析 arXiv 返回的元数据。') from None
    ns = {'a': 'http://www.w3.org/2005/Atom', 'x': 'http://arxiv.org/schemas/atom'}
    entries = []
    for node in root.findall('a:entry', ns):
        identifier = normalize(_entry_text(node.find('a:id', ns)).rsplit('/', 1)[-1])
        title = _entry_text(node.find('a:title', ns))
        if not identifier or not title:
            continue
        authors = [name.strip() for name in (_entry_text(author.find('a:name', ns)) for author in node.findall('a:author', ns)) if name]
        published = _entry_text(node.find('a:published', ns))
        date = published[:10] if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', published[:10]) else ''
        doi = _entry_text(node.find('x:doi', ns))
        pdf_url = next((link.get('href', '') for link in node.findall('a:link', ns) if link.get('title') == 'pdf'), '')
        entries.append(dict(identifier=identifier, title=title, abstract=_entry_text(node.find('a:summary', ns)),
                            date=date, authors=authors[:1000], doi=doi,
                            url='https://arxiv.org/abs/' + identifier,
                            pdf_url=pdf_url or 'https://arxiv.org/pdf/' + identifier))
    return entries

def download_pdf(pdf_url, directory, identifier):
    _validated_url(pdf_url)
    raw = _open(pdf_url, MAX_PDF_BYTES, DOWNLOAD_TIMEOUT)
    if len(raw) > MAX_PDF_BYTES:
        raise ValueError('PDF 正文超过 100 MiB 上限。')
    if not raw.startswith(b'%PDF-'):
        raise ValueError('下载内容不是有效的 PDF。')
    target = Path(directory) / (re.sub(r'[^A-Za-z0-9_.-]', '_', identifier) + '.pdf')
    with target.open('xb') as stream:
        stream.write(raw)
    return target

def import_batch(service, entries, collection_id=None, delay=1.0):
    """Download checked entries and import them; returns {'imported': [...], 'errors': [...]}."""
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise ValueError('导入列表无效。')
    imported, errors = [], []
    with tempfile.TemporaryDirectory(prefix='polyscholar-arxiv-') as temporary:
        directory = Path(temporary)
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) - ENTRY_KEYS:
                errors.append('条目数据无效，已跳过。')
                continue
            label = str(entry.get('identifier') or '未知编号')
            try:
                title = entry.get('title')
                if not isinstance(title, str) or not title.strip():
                    raise ValueError('元数据缺少标题。')
                authors = entry.get('authors')
                if not isinstance(authors, list) or any(not isinstance(a, str) or not a.strip() or len(a) > 512 for a in authors):
                    raise ValueError('作者信息无效。')
                if index:
                    time.sleep(delay)
                path = download_pdf(entry['pdf_url'], directory, entry['identifier'])
                document = service.import_pdf(path)
                creators = [dict(role='author', type='person', literal=name.strip(), family='', given='') for name in authors]
                patch = dict(title=title.strip(), itemType='arxiv-preprint', creators=creators,
                             abstract=entry.get('abstract', ''), url=entry.get('url', ''), doi=entry.get('doi', ''),
                             year=entry.get('date', '')[:4], date=entry.get('date', ''))
                service.update_document(document['id'], patch)
                if collection_id:
                    service.set_membership(document['id'], collection_id)
                imported.append(dict(id=document['id'], title=title.strip()))
            except Exception as error:
                errors.append(label + '：' + (str(error) if isinstance(error, ValueError) else '下载或导入失败。'))
    return dict(imported=imported, errors=errors)
