"""Spec for the in-app help generator (tools/build_help.py).

The page it writes is what a shop-floor user reads, so a conversion bug shows up
as raw markdown on screen. Run: python -m unittest tests.test_help_build
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from build_help import convert, inline, slug  # noqa: E402


class Slugs(unittest.TestCase):
    def test_matches_github(self):
        # one hyphen PER SPACE — dropped punctuation leaves a double space, and
        # the guide's own cross-links depend on the doubled hyphen surviving
        self.assertEqual(slug("5.3 Can we actually make it? — the Material Check"),
                         "53-can-we-actually-make-it--the-material-check")
        self.assertEqual(slug("8.1 Planning a long order"), "81-planning-a-long-order")
        self.assertEqual(slug("Home — your modules"), "home--your-modules")


class Inline(unittest.TestCase):
    def test_bold_italic_code(self):
        self.assertEqual(inline("**bold**"), "<strong>bold</strong>")
        self.assertEqual(inline("*just italic*"), "<em>just italic</em>")
        self.assertEqual(inline("`code`"), "<code>code</code>")
        self.assertEqual(inline("~~gone~~"), "<del>gone</del>")

    def test_nested_emphasis(self):
        """The real bug: '**a *b* c**' used to leave the ** on screen."""
        self.assertEqual(inline("a green **★ *customer* rate** badge"),
                         "a green <strong>★ <em>customer</em> rate</strong> badge")

    def test_escapes_html(self):
        self.assertIn("&lt;script&gt;", inline("<script>alert(1)</script>"))
        self.assertNotIn("<script>", inline("<script>alert(1)</script>"))

    def test_links(self):
        self.assertEqual(inline("[jump](#a-b)"), '<a href="#a-b">jump</a>')
        self.assertIn('target="_blank"', inline("[x](https://example.com)"))
        # a link to another repo file means nothing inside the app: keep the words
        self.assertEqual(inline("see [ROADMAP.md](ROADMAP.md) for more"),
                         "see ROADMAP.md for more")


class Blocks(unittest.TestCase):
    def test_headings_make_contents(self):
        body, toc = convert("# Title\n\n## One\n\ntext\n\n### One A\n")
        self.assertIn('<h2 id="one">One</h2>', body)
        self.assertEqual([t["text"] for t in toc], ["One", "One A"])
        self.assertEqual([t["level"] for t in toc], [2, 3])   # h1 is not a nav entry

    def test_table(self):
        body, _ = convert("| A | B |\n| --- | --- |\n| 1 | 2 |\n")
        self.assertIn("<th>A</th>", body)
        self.assertIn("<td>1</td>", body)
        self.assertNotIn("---", body)          # the separator row is not data

    def test_image_points_at_the_local_copy(self):
        body, _ = convert("![Stock list](guide-images/inv-stock.jpg)\n")
        self.assertIn('src="images/inv-stock.jpg"', body)
        self.assertIn("<figcaption>Stock list</figcaption>", body)

    def test_lists_open_and_close(self):
        body, _ = convert("- one\n- two\n\ntext\n\n1. first\n2. second\n")
        self.assertEqual(body.count("<ul>"), 1)
        self.assertEqual(body.count("</ul>"), 1)
        self.assertEqual(body.count("<ol>"), 1)
        self.assertEqual(body.count("<li>"), 4)

    def test_wrapped_bullet_keeps_its_continuation(self):
        body, _ = convert("- a bullet that\n  wraps onto another line\n")
        self.assertIn("<li>a bullet that wraps onto another line</li>", body)

    def test_blockquote_joins(self):
        body, _ = convert("> one\n> two\n")
        self.assertIn("<blockquote>one two</blockquote>", body)

    def test_paragraph_joins_wrapped_lines(self):
        body, _ = convert("a sentence that\nwraps\n")
        self.assertIn("<p>a sentence that wraps</p>", body)

    def test_rule(self):
        body, _ = convert("a\n\n---\n\nb\n")
        self.assertIn("<hr>", body)


class TheRealGuide(unittest.TestCase):
    """Convert the actual guide and assert nothing leaks."""

    @classmethod
    def setUpClass(cls):
        guide = Path(__file__).resolve().parents[3] / "docs" / "USER_GUIDE.md"
        if not guide.exists():
            raise unittest.SkipTest("guide not present")
        cls.body, cls.toc = convert(guide.read_text(encoding="utf-8"))

    def test_no_raw_markdown_survives(self):
        text = re.sub(r"<[^>]+>", "", self.body)
        for marker in ("**", "](", "!["):
            self.assertNotIn(marker, text, f"{marker!r} left in the rendered text")

    def test_every_in_page_anchor_resolves(self):
        ids = set(re.findall(r'<h[1-4] id="([^"]+)"', self.body))
        for href in re.findall(r'href="#([^"]+)"', self.body):
            self.assertIn(href, ids)

    def test_every_image_exists_on_disk(self):
        images = Path(__file__).resolve().parents[3] / "docs" / "guide-images"
        for name in set(re.findall(r'src="images/([^"]+)"', self.body)):
            self.assertTrue((images / name).exists(), name)

    def test_contents_is_not_empty(self):
        self.assertGreater(len(self.toc), 30)


if __name__ == "__main__":
    unittest.main()
