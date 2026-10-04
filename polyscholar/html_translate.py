# SPDX-License-Identifier: AGPL-3.0-only
"""arXiv HTML translation: rebuild the page in place, replacing English
paragraph text with Chinese while preserving tables, figures and formulas.

Fetches the official arXiv (or ar5iv for older papers) HTML rendering, walks
the document once, translates text of translatable containers (p, headings,
figcaption) through the user's configured OpenAI-compatible endpoint, and
emits a Chinese-only page. Math (MathML) never reaches the LLM: inline math
is captured verbatim and re-inserted into the translated paragraph.
"""
import base64
import binascii
import html as html_module
import json
import re
import time as time_module
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, build_opener

from .arxiv import normalize
from .summary_model import NoRedirect
from .model_endpoint import endpoint_url

USER_AGENT = 'PolyScholar-local/0.1 (personal desktop research tool)'
MAX_HTML_BYTES = 24 * 1024 * 1024
MAX_FIGURE_BYTES = 8 * 1024 * 1024
MAX_FIGURES_TOTAL = 24 * 1024 * 1024
MAX_FIGURES = 32
FIGURE_HOSTS = {'arxiv.org', 'ar5iv.labs.arxiv.org'}
FETCH_TIMEOUT = 30
LLM_TIMEOUT = 120
MAX_WORKERS = 4
MIN_TEXT_LENGTH = 24
TRANSLATE_TAGS = ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'figcaption')
STOP_HEADING = re.compile(r'^\s*(References|Bibliography|参考文献|文献)\s*$')
MATH_PLACEHOLDER = '\u27e6M{}\u27e7'

def _read_bounded(response, cap, deadline):
    """Bound slow bodies as well as total bytes / 同时限制慢速响应与总字节数。"""
    chunks = []
    size = 0
    while size <= cap:
        budget = deadline - time_module.monotonic()
        if budget <= 0:
            raise TimeoutError('HTML 网络请求超时。')
        socket = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        if socket is not None:
            socket.settimeout(budget)
        chunk = getattr(response, 'read1', response.read)(min(16384, cap + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b''.join(chunks)


def fetch_html(identifier, timeout=FETCH_TIMEOUT):
    """Latest arXiv HTML first, ar5iv for older papers; returns (base_url, html)."""
    identifier = normalize(identifier)
    sources = [('https://arxiv.org/html/' + identifier, True),
               ('https://ar5iv.labs.arxiv.org/html/' + quote(identifier), False)]
    errors = []
    for url, is_arxiv in sources:
        request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html'}, method='GET')
        try:
            deadline = time_module.monotonic() + min(FETCH_TIMEOUT, timeout)
            with build_opener(NoRedirect()).open(request, timeout=min(FETCH_TIMEOUT, timeout)) as response:
                raw = _read_bounded(response, MAX_HTML_BYTES, deadline)
            if len(raw) > MAX_HTML_BYTES:
                raise ValueError('arXiv HTML 超过 24 MiB 上限。')
            text = raw.decode('utf-8', errors='replace')
            if '<body' in text:
                # arXiv figure assets belong to this paper / 图片资源归属于当前论文路径。
                base = url.rstrip('/') + '/'
                return base, text
            errors.append(url.split('/')[2] + ':非论文页面')
        except HTTPError as error:
            errors.append(url.split('/')[2] + ':HTTP %d' % error.code)
        except (URLError, OSError):
            errors.append(url.split('/')[2] + ':网络错误')
    raise ValueError('未找到可用的 arXiv HTML 版本(' + ';'.join(errors) + ')。老论文可等待 ar5iv 转换。')

def _clean_text(parts):
    text = html_module.unescape(''.join(parts))
    return re.sub(r'\s+', ' ', text).strip()

class _Rebuilder(HTMLParser):
    """Emit the page verbatim, replacing translatable containers with Chinese.

    Tables (numbers/data) and figures pass through untouched; inline math is
    captured and re-inserted into the translated paragraph at its placeholder.
    """
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self.units = []          # completed units awaiting translation
        self.math_depth = 0
        self.table_depth = 0
        self.unit = None         # {'tag','parts','text_parts','maths'}
        self.stopped = False     # references reached: stop translating

    # -- token routing ----------------------------------------------------
    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        if tag == 'table' and self.math_depth == 0:
            self.table_depth += 1
        if self.math_depth:
            self._math_token(raw)
            if tag == 'math':
                self.math_depth += 1
            return
        if tag == 'math':
            self.math_depth = 1
            self._math_start(raw)
            return
        if (not self.stopped and self.table_depth == 0 and self.unit is None
                and tag in TRANSLATE_TAGS):
            self.unit = dict(tag=tag, parts=[], text_parts=[], maths=[])
            return  # original container suppressed; zh paragraph emitted on close
        self.out.append(raw)

    def handle_startendtag(self, tag, attrs):
        raw = self.get_starttag_text()
        if self.math_depth:
            self._math_token(raw)
        elif self.unit is None:
            self.out.append(raw)

    def handle_endtag(self, tag):
        if self.math_depth:
            self._math_token('</%s>' % tag)
            if tag == 'math':
                self.math_depth = 0
            return
        if self.unit is not None and tag == self.unit['tag']:
            self._close_unit()
            return
        if tag == 'table' and self.table_depth:
            self.table_depth -= 1
        self.out.append('</%s>' % tag)

    def handle_data(self, data):
        if self.math_depth:
            self._math_token(data)
        elif self.unit is not None:
            self.unit['text_parts'].append(data)
        else:
            self.out.append(data)

    def handle_entityref(self, name):
        token = '&%s;' % name
        if self.math_depth: self._math_token(token)
        elif self.unit is not None: self.unit['text_parts'].append(token)
        else: self.out.append(token)

    def handle_charref(self, name):
        token = '&#%s;' % name
        if self.math_depth: self._math_token(token)
        elif self.unit is not None: self.unit['text_parts'].append(token)
        else: self.out.append(token)

    def handle_comment(self, data):
        if self.math_depth: self._math_token('<!--%s-->' % data)
        elif self.unit is None: self.out.append('<!--%s-->' % data)

    # -- unit lifecycle ----------------------------------------------------
    def _math_start(self, raw):
        if self.unit is not None:
            marker = MATH_PLACEHOLDER.format(len(self.unit['maths']))
            self.unit['maths'].append(raw)  # completed by _math_token
            self.unit['text_parts'].append(' ' + marker + ' ')
        else:
            self.out.append(raw)  # standalone math stays verbatim

    def _math_token(self, token):
        if self.unit is not None and self.unit['maths']:
            self.unit['maths'][-1] += token
        elif self.unit is None:
            self.out.append(token)

    def _close_unit(self):
        unit = self.unit; self.unit = None
        text = _clean_text(unit['text_parts'])
        if STOP_HEADING.match(text):
            self.stopped = True
            self.out.append(self._unit_original(unit, text))
            return
        if len(text) < MIN_TEXT_LENGTH or not any(ch.isalpha() for ch in text):
            # Too short to translate: restore the original container verbatim.
            self.out.append(self._unit_original(unit, text))
            return
        unit['text'] = text
        self.out.append('<!--ps-unit-%d-->' % len(self.units))
        self.units.append(unit)

    def handle_decl(self, decl):
        if not self.math_depth and self.unit is None:
            self.out.append('<!%s>' % decl)

    def handle_pi(self, data):
        if self.unit is None and not self.math_depth:
            self.out.append('<?%s>' % data)

    def _unit_original(self, unit, text=None):
        """Best-effort original for untranslated units."""
        if text is not None:
            unit = {**unit, 'text': text}
        return _fallback_original(unit)

def rebuild(page, stopped_ok=True):
    parser = _Rebuilder()
    parser.feed(page)
    parser.close()
    return parser.out, parser.units, parser.stopped

def translate_unit(text, endpoint, model, api_key, timeout=LLM_TIMEOUT):
    endpoint = endpoint_url(endpoint)
    body = json.dumps(dict(
        model=model, temperature=0.1,
        messages=[
            dict(role='system', content='你是专业的学术翻译。将英文学术段落翻译成简体中文。'
                 '要求:保留 ⟦Mn⟧ 形式的数学占位符原样不译;数字、单位、引用编号、专有名词保持原样;'
                 '术语准确,语句通顺;只输出译文,不要解释。'),
            dict(role='user', content=text)]),
        ensure_ascii=False).encode('utf-8')
    request = Request(endpoint.rstrip('/') + '/chat/completions', data=body,
                      headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json',
                               'User-Agent': USER_AGENT}, method='POST')
    try:
        deadline = time_module.monotonic() + timeout
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = _read_bounded(response, 4 * 1024 * 1024, deadline)
            if len(raw) > 4 * 1024 * 1024:
                return None
            payload = json.loads(raw)
        content = payload['choices'][0]['message']['content']
    except (HTTPError, URLError, OSError, KeyError, IndexError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    return content.strip()

def _render_unit(unit, zh):
    """Chinese paragraph with inline math re-inserted at placeholders."""
    # Model output is text, never HTML / 模型输出始终作为文本处理。
    body = html_module.escape(zh)
    for index, math_html in enumerate(unit['maths']):
        marker = MATH_PLACEHOLDER.format(index)
        if body.count(marker) != 1:
            return None
        body = body.replace(marker, math_html)
    if MATH_PLACEHOLDER[:2] in body:  # a placeholder survived: translation untrusted
        return None
    return '<p class="ps-zh">%s</p>' % body

def _fallback_original(unit):
    """Original container for a block whose translation failed."""
    body = html_module.escape(unit.get('text', ''))
    for index, math_html in enumerate(unit['maths']):
        body = body.replace(MATH_PLACEHOLDER.format(index), math_html)
    return '<%s>%s</%s>' % (unit['tag'], body, unit['tag'])

def translate_paper(identifier, endpoint, model, api_key, progress=None, timeout=LLM_TIMEOUT, max_workers=MAX_WORKERS, on_partial=None, cancel_event=None, deadline=None, figure_fetcher=None):
    """Translate one arXiv paper to a Chinese-only HTML string.

    progress(current,total) reports translatable blocks; per-block failures keep
    the original English block; on_partial(html) receives the growing
    page during translation for progressive reading.
    """
    deadline = deadline if deadline is not None else time_module.monotonic() + timeout

    def remaining():
        # One budget covers the paper, not each block / 全文共用一个期限。
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('HTML 翻译已取消。')
        budget = deadline - time_module.monotonic()
        if budget <= 0:
            raise TimeoutError('HTML 翻译超时。')
        return min(timeout, budget)

    remaining()
    base_url, page = fetch_html(identifier, timeout=remaining())
    remaining()
    figures = cache_figures(page, base_url, deadline=deadline, cancel_event=cancel_event, fetcher=figure_fetcher)
    remaining()
    page = sanitize_html(page, figures)
    rebuilder = _Rebuilder()
    rebuilder.base_url = base_url
    rebuilder.feed(page)
    rebuilder.close()
    units = rebuilder.units
    total = len(units)
    if total == 0:
        raise ValueError('该论文的 HTML 中没有可翻译的正文段落。')

    zh_map = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        def translate_checked(unit):
            # Check before queued work sends any paid request / 排队任务发请求前检查取消。
            return translate_unit(unit['text'], endpoint, model, api_key, remaining())

        for index, unit in enumerate(units):
            remaining()
            futures[pool.submit(translate_checked, unit)] = index
        for future in as_completed(futures):
            index = futures[future]
            remaining()
            first = future.result()
            if first is not None:
                zh_map[index] = first
            done += 1
            if progress:
                progress(done, total)
            if on_partial:
                on_partial(_assemble(rebuilder, zh_map, identifier, model))

    final_html = _assemble(rebuilder, zh_map, identifier, model)
    return final_html, total, len(zh_map)

STYLE = ('<style>body{font-family:system-ui,"Microsoft YaHei",sans-serif;max-width:920px;margin:0 auto;padding:24px;line-height:1.8;color:#1c2532;}'
         'p.ps-zh{margin:10px 0 14px;}'
         '.poly-meta{color:#5f6c65;font-size:13px;border-bottom:1px solid #e4eae6;padding-bottom:12px;}</style>')

def _assemble(rebuilder, zh_map, identifier, model):
    """Build ONE clean document: our head (charset/base/style) + the original
    body content (with arXiv page chrome stripped, zh paragraphs swapped in).

    Concatenating our wrapper with the original full document produced two
    stacked documents and broke every relative URL.
    """
    base_url = getattr(rebuilder, 'base_url', '')
    html = ''.join(rebuilder.out)
    body_match = re.search(r'<body[^>]*>(.*)</body>', html, re.DOTALL | re.IGNORECASE)
    body_inner = body_match.group(1) if body_match else html
    # Drop the arXiv page chrome (site header/nav) for a clean reading page.
    body_inner = re.sub(r'<header\b.*?</header>', '', body_inner, flags=re.DOTALL | re.IGNORECASE)
    # arXiv site chrome: TOC nav, announcement banner (smiley), report dialog, scripts.
    body_inner = re.sub(r'<nav\b.*?</nav>', '', body_inner, flags=re.DOTALL | re.IGNORECASE)
    body_inner = re.sub(r'<div[^>]*class="[^"]*ds-announcement[^"]*"[^>]*>.*?</div>', '', body_inner, flags=re.DOTALL | re.IGNORECASE)
    body_inner = re.sub(r'<dialog\b.*?</dialog>', '', body_inner, flags=re.DOTALL | re.IGNORECASE)
    body_inner = re.sub(r'<script\b.*?</script>', '', body_inner, flags=re.DOTALL | re.IGNORECASE)
    for index, unit in enumerate(rebuilder.units):
        marker = '<!--ps-unit-%d-->' % index
        zh = zh_map.get(index)
        replacement = (_render_unit(unit, zh) if zh is not None else None) or _fallback_original(unit)
        body_inner = body_inner.replace(marker, replacement, 1)
    meta = ('<p class="poly-meta">PolyScholar 中文译文 · arXiv:' + html_module.escape(identifier) +
            ' · 模型:' + html_module.escape(model) + ' · 图表/公式来自 arXiv 原页,数学未翻译</p>')
    return ('<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
            '<title>' + html_module.escape(identifier) + ' · PolyScholar 中文译文</title>'
            '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; img-src data:">' + STYLE + '</head>'
            '<body>' + meta + body_inner + '</body></html>')


class _SafeHTML(HTMLParser):
    """Keep document structure without active or remote content.

    保留论文结构，禁用脚本、表单、远程资源及事件属性。
    """
    BLOCKED = {'script', 'style', 'iframe', 'object', 'embed', 'form', 'link', 'base', 'meta', 'svg'}
    ATTRIBUTES = {'class', 'id', 'title', 'alt', 'colspan', 'rowspan', 'display', 'mathvariant'}

    def __init__(self, figures=None):
        super().__init__(convert_charrefs=True)
        self.figures = figures or {}
        self.figure_bytes = 0
        self.figure_count = 0
        self.output = []
        self.blocked = []

    def handle_starttag(self, tag, attrs):
        if self.blocked:
            if tag in self.BLOCKED and tag not in {'link', 'base', 'meta', 'embed'}:
                self.blocked.append(tag)
            return
        if tag in self.BLOCKED:
            if tag == 'svg':
                self.output.append(' [矢量图暂不可用] ')
            if tag not in {'link', 'base', 'meta', 'embed'}:
                self.blocked.append(tag)
            return
        safe = []
        for name, value in attrs:
            if name in self.ATTRIBUTES and value is not None:
                safe.append('%s="%s"' % (name, html_module.escape(value, quote=True)))
            elif name == 'href' and value and value.startswith('#'):
                safe.append('href="%s"' % html_module.escape(value, quote=True))
        source = dict(attrs).get('src', '')
        candidate = (self.figures.get(source) or source) if tag == 'img' else None
        cached, size = _safe_data_image(candidate)
        if (cached and self.figure_count < MAX_FIGURES
                and self.figure_bytes + size <= MAX_FIGURES_TOTAL):
            self.figure_bytes += size
            self.figure_count += 1
            safe.append('src="' + cached + '"')
        else:
            cached = None
        self.output.append('<' + tag + (' ' + ' '.join(safe) if safe else '') + '>')
        if tag == 'img' and not cached:
            # Visible fallback avoids hidden remote requests / 图片提供本地占位提示。
            self.output.append(html_module.escape(dict(attrs).get('alt') or ' [原文图片] '))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {'img', 'br', 'hr', 'input'}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.blocked:
            if tag == self.blocked[-1]:
                self.blocked.pop()
            return
        if tag not in self.BLOCKED:
            self.output.append('</' + tag + '>')

    def handle_data(self, data):
        if not self.blocked:
            self.output.append(html_module.escape(data))


def sanitize_html(page, figures=None):
    """Offline-safe structure / 可离线安全阅读的论文结构。"""
    parser = _SafeHTML(figures)
    parser.feed(page)
    parser.close()
    return ''.join(parser.output)


class _FigureSources(HTMLParser):
    """Collect image references only / 仅收集图片引用。"""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            source = dict(attrs).get('src')
            if (isinstance(source, str) and len(self.sources) < MAX_FIGURES
                    and source not in self.sources):
                self.sources.append(source)

    handle_startendtag = handle_starttag


def _figure_mime(raw):
    """Accept raster magic bytes, never SVG/HTML / 只接受位图签名，不接受 SVG/HTML。"""
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _safe_data_image(value):
    """Retain only bounded raster data URLs on repeated sanitization.

    再次净化时仅保留有大小上限及位图签名的数据 URL。
    """
    if not isinstance(value, str) or len(value) > 4 * ((MAX_FIGURE_BYTES + 2) // 3) + 64:
        return None, 0
    match = re.fullmatch(r'data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)', value)
    if not match:
        return None, 0
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error):
        return None, 0
    if len(raw) > MAX_FIGURE_BYTES or _figure_mime(raw) != match.group(1):
        return None, 0
    return value, len(raw)


def _fetch_figure(url, *, timeout, max_bytes):
    request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'image/png,image/jpeg,image/webp'}, method='GET')
    deadline = time_module.monotonic() + timeout
    # Public image requests carry no API token; redirects remain disabled.
    # 公共图片请求不携带 API 密钥，也禁止重定向。
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        return _read_bounded(response, max_bytes, deadline)


def cache_figures(page, base_url, *, deadline, cancel_event=None, fetcher=None):
    """Embed bounded public raster figures from the successful page's origin.

    从实际成功页面的同源公开地址缓存有限位图，阅读器无需联网。
    Unsupported, cross-origin or failed images remain explicit placeholders.
    不支持、跨域或失败的图片保留明确占位提示。
    """
    try:
        base = urlsplit(base_url)
        if (base.scheme != 'https' or base.hostname not in FIGURE_HOSTS
                or base.username or base.password or base.port not in (None, 443)):
            return {}
    except (ValueError, TypeError):
        return {}
    collector = _FigureSources()
    collector.feed(page)
    fetcher = fetcher or _fetch_figure
    figures = {}
    used = 0
    for source in collector.sources[:MAX_FIGURES]:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('HTML 翻译已取消。')
        remaining = deadline - time_module.monotonic()
        if remaining <= 0:
            raise TimeoutError('HTML 翻译超时。')
        try:
            url = urljoin(base_url, source)
            target = urlsplit(url)
            if (target.scheme != 'https' or target.hostname != base.hostname
                    or target.username or target.password or target.fragment
                    or target.port not in (None, 443)):
                continue
            cap = min(MAX_FIGURE_BYTES, MAX_FIGURES_TOTAL - used)
            if cap <= 0:
                break
            raw = fetcher(url, timeout=min(FETCH_TIMEOUT, remaining), max_bytes=cap)
            if not isinstance(raw, bytes) or len(raw) > cap:
                continue
            mime = _figure_mime(raw)
            if mime is None:
                continue
            used += len(raw)
            figures[source] = 'data:' + mime + ';base64,' + base64.b64encode(raw).decode('ascii')
        except (ValueError, HTTPError, URLError, OSError):
            continue
    return figures
