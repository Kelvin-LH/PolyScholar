# SPDX-License-Identifier: AGPL-3.0-only
"""arXiv HTML translation: rebuild the page in place, replacing English
paragraph text with Chinese while preserving tables, figures and formulas.

Fetches the official arXiv (or ar5iv for older papers) HTML rendering, walks
the document once, translates text of translatable containers (p, headings,
figcaption) through the user's configured OpenAI-compatible endpoint, and
emits a Chinese-only page. Math (MathML) never reaches the LLM: inline math
is captured verbatim and re-inserted into the translated paragraph.
"""
import html as html_module
import json
import re
import time as time_module
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener

USER_AGENT = 'PolyScholar-local/0.1 (personal desktop research tool)'
MAX_HTML_BYTES = 24 * 1024 * 1024
FETCH_TIMEOUT = 30
LLM_TIMEOUT = 120
MAX_WORKERS = 4
MIN_TEXT_LENGTH = 24
TRANSLATE_TAGS = ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'figcaption')
STOP_HEADING = re.compile(r'^\s*(References|Bibliography|参考文献|文献)\s*$')
MATH_PLACEHOLDER = '\u27e6M{}\u27e7'

def fetch_html(identifier):
    """Latest arXiv HTML first, ar5iv for older papers; returns (base_url, html)."""
    sources = [('https://arxiv.org/html/' + identifier, True),
               ('https://ar5iv.labs.arxiv.org/html/' + quote(identifier), False)]
    errors = []
    for url, is_arxiv in sources:
        request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html'}, method='GET')
        try:
            with build_opener().open(request, timeout=FETCH_TIMEOUT) as response:
                raw = response.read(MAX_HTML_BYTES + 1)
            if len(raw) > MAX_HTML_BYTES:
                raise ValueError('arXiv HTML 超过 24 MiB 上限。')
            text = raw.decode('utf-8', errors='replace')
            if '<body' in text:
                # Browser base semantics: directory of the successful page URL.
                base = url.rsplit('/', 1)[0] + '/'
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

    def handle_decl(self, decl):
        if self.math_depth is not None and self.unit is None and not self.math_depth:
            self.out.append('<!%s>' % decl)

    def handle_pi(self, data):
        if self.unit is None and not self.math_depth:
            self.out.append('<?%s>' % data)

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

def _clean_text(parts):
    text = html_module.unescape(''.join(parts))
    return re.sub(r'\s+', ' ', text).strip()

def translate_unit(text, endpoint, model, api_key, timeout=LLM_TIMEOUT):
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
        with build_opener().open(request, timeout=timeout) as response:
            payload = json.loads(response.read(4 * 1024 * 1024))
        content = payload['choices'][0]['message']['content']
    except (HTTPError, URLError, OSError, KeyError, IndexError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    return content.strip()

def _render_unit(unit, zh):
    """Chinese paragraph with inline math re-inserted at placeholders."""
    body = zh
    for index, math_html in enumerate(unit['maths']):
        body = body.replace(MATH_PLACEHOLDER.format(index), math_html)
    if MATH_PLACEHOLDER[:2] in body:  # a placeholder survived: translation untrusted
        return None
    return '<p class="ps-zh">%s</p>' % body

def _fallback_original(unit):
    """Original container for a block whose translation failed."""
    body = html_module.escape(unit.get('text', ''))
    for index, math_html in enumerate(unit['maths']):
        body = body.replace(MATH_PLACEHOLDER.format(index), math_html)
    return '<%s>%s</%s>' % (unit['tag'], body, unit['tag'])

def translate_paper(identifier, endpoint, model, api_key, progress=None, timeout=LLM_TIMEOUT, max_workers=MAX_WORKERS, on_partial=None):
    """Translate one arXiv paper to a Chinese-only HTML string.

    progress(current,total) reports translatable blocks; per-block failures keep
    the original English block (one retry); on_partial(html) receives the growing
    page during translation for progressive reading.
    """
    base_url, page = fetch_html(identifier)
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
        for index, unit in enumerate(units):
            futures[pool.submit(translate_unit, unit['text'], endpoint, model, api_key, timeout)] = index
        for future in as_completed(futures):
            index = futures[future]
            first = future.result()
            if first is None:
                first = translate_unit(units[index]['text'], endpoint, model, api_key, timeout)
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
        replacement = _render_unit(unit, zh) if zh is not None else _fallback_original(unit)
        body_inner = body_inner.replace(marker, replacement, 1)
    meta = ('<p class="poly-meta">PolyScholar 中文译文 · arXiv:' + html_module.escape(identifier) +
            ' · 模型:' + html_module.escape(model) + ' · 图表/公式来自 arXiv 原页,数学未翻译</p>')
    return ('<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
            '<title>' + html_module.escape(identifier) + ' · PolyScholar 中文译文</title>'
            '<base href="' + base_url + '">' + STYLE + '</head>'
            '<body>' + meta + body_inner + '</body></html>')
