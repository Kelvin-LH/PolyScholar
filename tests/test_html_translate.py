# SPDX-License-Identifier: AGPL-3.0-only
"""Rebuilder checks: tables/figures/math preserved, zh-only swap, references stop."""
import unittest
from polyscholar import html_translate as ht

PAGE = """<!DOCTYPE html>
<html><head><title>t</title></head><body>
<div class="ltx_abstract"><h2>Abstract</h2>
<p>We propose <math><mi>ORION</mi></math> achieving <math><mn>77.74</mn></math> DS on nuScene. This is a novel framework.</p>
</div>
<figure class="ltx_table"><table><tr><td>1.33</td><td>2.11</td></tr></table>
<figcaption>Table 1: Benchmark results across three datasets with details.</figcaption></figure>
<img src="x1.png">
<h2>References</h2>
<p>Smith et al. Journal of Anything 2024. All remaining bibliographic entries appear here.</p>
</body></html>"""

def run(echo_marker='【中文】'):
    from unittest.mock import patch
    with patch.object(ht, 'fetch_html', return_value=('https://arxiv.org/html/2503.19755v1/', PAGE)), patch.object(ht, 'translate_unit', side_effect=lambda text, *a, **k: echo_marker + text):
        return ht.translate_paper('2503.19755', 'https://example.invalid', 'm', 'k', figure_fetcher=lambda *args, **kwargs: b'')

class RebuildTests(unittest.TestCase):
    def test_tables_figures_math_preserved(self):
        out, total, zh = run()
        self.assertIn('<table>', out); self.assertIn('1.33', out)
        self.assertNotIn('src="x1.png"', out)
        self.assertIn('原文图片', out)
        self.assertIn('<mi>ORION</mi>', out)
        self.assertNotIn('⟦M', out)

    def test_translated_containers_are_chinese_only(self):
        out, total, zh = run()
        self.assertIn('【中文】We propose', out)
        self.assertEqual(out.count('<p>We propose'), 0)
        self.assertGreater(zh, 0)

    def test_references_section_left_untranslated(self):
        out, total, zh = run()
        self.assertIn('bibliographic entries appear here', out)
        self.assertNotIn('【中文】Smith', out)

import unittest
if __name__ == '__main__':
    unittest.main()


class BoundaryTests(unittest.TestCase):
    def test_model_text_is_escaped_and_missing_math_falls_back(self):
        unit = dict(tag='p', text='original', maths=['<math><mi>x</mi></math>'])
        self.assertIsNone(ht._render_unit(unit, 'translation omitted math'))
        rendered = ht._render_unit(unit, '<img src="https://invalid"> ⟦M0⟧')
        self.assertIn('&lt;img', rendered)
        self.assertIn('<math>', rendered)

    def test_active_content_and_remote_resources_are_removed(self):
        raw = '<body onload="leak()"><script>alert(1)</script><img src="https://invalid"><iframe src="https://invalid"></iframe><p style="background:url(https://invalid)">Safe</p></body>'
        safe = ht.sanitize_html(raw)
        for token in ('https://', 'onload', 'script', 'iframe', 'style='):
            self.assertNotIn(token, safe)
        self.assertIn('Safe', safe)

    def test_cancelled_paper_does_not_fetch(self):
        import threading
        from unittest.mock import patch
        event = threading.Event()
        event.set()
        with patch.object(ht, 'fetch_html') as fetch:
            with self.assertRaisesRegex(InterruptedError, '取消'):
                ht.translate_paper('2312.04567', 'https://example.invalid', 'm', 'k', cancel_event=event)
            fetch.assert_not_called()

    def test_expired_deadline_does_not_fetch(self):
        from unittest.mock import patch
        with patch.object(ht, 'fetch_html') as fetch:
            with self.assertRaisesRegex(TimeoutError, '超时'):
                ht.translate_paper('2312.04567', 'https://example.invalid', 'm', 'k', deadline=0)
            fetch.assert_not_called()


class RequestBoundaryTests(unittest.TestCase):
    def test_authenticated_request_rejects_redirects(self):
        from contextlib import contextmanager
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                seen.append(self.path)
                self.send_response(307)
                self.send_header('Location', '/credential-sink')
                self.send_header('Content-Length', '0')
                self.end_headers()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            endpoint = 'http://127.0.0.1:%d' % server.server_address[1]
            self.assertIsNone(ht.translate_unit('synthetic text', endpoint, 'm', 'synthetic-key'))
            self.assertEqual(seen, ['/chat/completions'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_invalid_endpoint_rejected_before_request(self):
        from unittest.mock import patch
        with patch.object(ht, 'build_opener') as opener:
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                ht.translate_unit('synthetic', 'http://remote.invalid', 'm', 'synthetic-key')
            opener.assert_not_called()


class FigureCacheTests(unittest.TestCase):
    def test_same_origin_raster_embedded_without_reader_network(self):
        import time
        seen = []
        raster = b'\x89PNG\r\n\x1a\n' + b'synthetic raster'
        def fetch(url, **kwargs):
            seen.append(url)
            return raster
        page = '<figure><img src="x1.png" alt="结果图"><figcaption>Result</figcaption></figure>'
        figures = ht.cache_figures(page, 'https://arxiv.org/html/2312.04567/', deadline=time.monotonic() + 5, fetcher=fetch)
        safe = ht.sanitize_html(page, figures)
        self.assertEqual(seen, ['https://arxiv.org/html/2312.04567/x1.png'])
        self.assertIn('src="data:image/png;base64,', safe)
        self.assertNotIn('https://', safe)
        self.assertNotIn('原文图片', safe)
        self.assertEqual(ht.sanitize_html(safe), safe)

    def test_cross_origin_active_and_oversized_sources_do_not_embed(self):
        import time
        seen = []
        def fetch(url, **kwargs):
            seen.append(url)
            return b'\x89PNG\r\n\x1a\n' + b'x' * kwargs['max_bytes']
        page = '<img src="https://evil.invalid/x"><img src="file:///private/x"><img src="javascript:alert(1)"><img src="x.png">'
        figures = ht.cache_figures(page, 'https://arxiv.org/html/2312.04567/', deadline=time.monotonic() + 5, fetcher=fetch)
        self.assertEqual(figures, {})
        self.assertEqual(len(seen), 1)

    def test_image_count_and_total_budget_bound_requests(self):
        import time
        from unittest.mock import patch
        seen = []
        def fetch(url, **kwargs):
            seen.append(kwargs['max_bytes'])
            return b'\xff\xd8\xff' + b'xxx'
        page = ''.join('<img src="x%d.jpg">' % index for index in range(8))
        with patch.object(ht, 'MAX_FIGURES', 3), patch.object(ht, 'MAX_FIGURES_TOTAL', 12):
            figures = ht.cache_figures(page, 'https://arxiv.org/html/2312.04567/', deadline=time.monotonic() + 5, fetcher=fetch)
        self.assertEqual(len(figures), 2)
        self.assertEqual(seen, [12, 6])

    def test_unsupported_svg_remains_visible_placeholder(self):
        import time
        page = '<img src="x.svg"><svg><script>unsafe</script></svg>'
        figures = ht.cache_figures(page, 'https://arxiv.org/html/2312.04567/', deadline=time.monotonic() + 5, fetcher=lambda *args, **kwargs: b'<svg>unsafe</svg>')
        safe = ht.sanitize_html(page, figures)
        self.assertIn('原文图片', safe)
        self.assertIn('矢量图暂不可用', safe)
        self.assertNotIn('unsafe', safe)


class EmbeddedFigureBoundaryTests(unittest.TestCase):
    def test_data_url_mime_must_match_raster_signature(self):
        import base64
        data = base64.b64encode(b'<svg onload="unsafe">').decode()
        output = ht.sanitize_html('<img src="data:image/png;base64,' + data + '">')
        self.assertNotIn('src=', output)
        self.assertIn('原文图片', output)

    def test_cached_injection_cannot_bypass_sanitizer(self):
        output = ht.sanitize_html('<img src="x.png">', {'x.png': 'https://evil.invalid/x'})
        self.assertNotIn('https://', output)
        self.assertIn('原文图片', output)
