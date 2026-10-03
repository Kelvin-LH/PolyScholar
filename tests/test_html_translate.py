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
    ht.fetch_html = lambda i: ('https://arxiv.org/html/2503.19755v1/', PAGE)
    ht.translate_unit = lambda text, *a, **k: echo_marker + text
    return ht.translate_paper('2503.19755', 'http://x', 'm', 'k')

class RebuildTests(unittest.TestCase):
    def test_tables_figures_math_preserved(self):
        out, total, zh = run()
        self.assertIn('<table>', out); self.assertIn('1.33', out)
        self.assertIn('x1.png', out)
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
