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

from build_help import ACCESS_RE, convert, inline, slug  # noqa: E402


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
        self.assertRegex(body, r'<h2[^>]*id="one"[^>]*>One</h2>')
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
        self.assertRegex(body, r'<figure[^>]*>')

    def test_lists_open_and_close(self):
        body, _ = convert("- one\n- two\n\ntext\n\n1. first\n2. second\n")
        self.assertEqual(len(re.findall(r"<ul[^>]*>", body)), 1)
        self.assertEqual(body.count("</ul>"), 1)
        self.assertEqual(len(re.findall(r"<ol[^>]*>", body)), 1)
        self.assertEqual(body.count("</ol>"), 1)
        self.assertEqual(len(re.findall(r"<li[^>]*>", body)), 4)

    def test_wrapped_bullet_keeps_its_continuation(self):
        body, _ = convert("- a bullet that\n  wraps onto another line\n")
        self.assertRegex(body, r'<li[^>]*>a bullet that wraps onto another line</li>')

    def test_blockquote_joins(self):
        body, _ = convert("> one\n> two\n")
        self.assertRegex(body, r'<blockquote[^>]*>one two</blockquote>')

    def test_paragraph_joins_wrapped_lines(self):
        body, _ = convert("a sentence that\nwraps\n")
        self.assertRegex(body, r'<p[^>]*>a sentence that wraps</p>')

    def test_rule(self):
        body, _ = convert("a\n\n---\n\nb\n")
        self.assertRegex(body, r'<hr[^>]*>')


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
        ids = set(re.findall(r'<h[1-4][^>]*id="([^"]+)"', self.body))
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


class ChapterAccess(unittest.TestCase):
    """Each chapter carries the grant key that its content needs."""

    SAMPLE = """## 1. Getting started
<!-- access: general -->

anyone may read this

## 3. Salary & Attendance
<!-- access: salary -->

pay stuff

### 3.1 Advances

- a bullet

## 5. Inventory
<!-- access: inventory -->

| A | B |
| --- | --- |
| 1 | 2 |
"""

    def test_marker_is_consumed_not_rendered(self):
        body, _ = convert(self.SAMPLE)
        self.assertNotIn("access:", body)
        self.assertNotIn("<!--", body)

    def test_every_block_is_stamped(self):
        body, _ = convert(self.SAMPLE)
        for line in body.splitlines():
            if line.startswith("<") and not line.startswith("</"):
                self.assertIn("data-access=", line, line)

    def test_heading_belongs_to_the_chapter_it_opens(self):
        """The marker sits AFTER the heading; the heading must still take it,
        or hiding a chapter would leave its title on screen."""
        body, toc = convert(self.SAMPLE)
        self.assertIn('<h2 data-access="salary" id="3-salary--attendance">', body)
        self.assertIn('<h2 data-access="inventory"', body)
        by_text = {t["text"]: t["access"] for t in toc}
        self.assertEqual(by_text["3. Salary & Attendance"], "salary")
        self.assertEqual(by_text["5. Inventory"], "inventory")

    def test_subsections_inherit_the_chapter(self):
        body, toc = convert(self.SAMPLE)
        self.assertIn('<h3 data-access="salary"', body)
        self.assertEqual([t["access"] for t in toc if t["text"].startswith("3.1")],
                         ["salary"])

    def test_lists_and_tables_are_stamped(self):
        body, _ = convert(self.SAMPLE)
        self.assertIn('<ul data-access="salary">', body)
        self.assertIn("data-access=\"inventory\"", body.split("<h2 data-access=\"inventory\"")[1])

    def test_no_escaped_quotes_in_the_attribute(self):
        body, _ = convert(self.SAMPLE)
        self.assertNotIn('\\"', body)

    def test_content_before_any_chapter_is_general(self):
        body, _ = convert("# Title\n\nintro text\n")
        self.assertIn('<p data-access="general">intro text</p>', body)


class TheRealGuideIsFullyTagged(unittest.TestCase):
    """Every chapter must declare its access — a new one must not default in."""

    @classmethod
    def setUpClass(cls):
        cls.guide = Path(__file__).resolve().parents[3] / "docs" / "USER_GUIDE.md"
        if not cls.guide.exists():
            raise unittest.SkipTest("guide not present")
        cls.lines = cls.guide.read_text(encoding="utf-8").splitlines()

    def test_every_chapter_declares_access(self):
        missing = []
        for i, line in enumerate(self.lines):
            if not line.startswith("## "):
                continue
            nxt = [l for l in self.lines[i + 1:i + 4] if l.strip()]
            if not (nxt and ACCESS_RE.match(nxt[0].strip())):
                missing.append(line)
        self.assertEqual(missing, [], "chapters with no <!-- access: --> marker")

    def test_keys_are_real_grants(self):
        import re as _re
        keys = {m.group(1) for m in
                (ACCESS_RE.match(l.strip()) for l in self.lines) if m}
        registry = (Path(__file__).resolve().parents[1]
                    / "backend" / "core" / "registry.py").read_text()
        real = set(_re.findall(r'"key":\s*"([a-z]+)"', registry))
        self.assertTrue(keys - {"general", "admin"} <= real,
                        f"unknown grant keys: {keys - {'general', 'admin'} - real}")

    def test_the_generated_page_carries_the_filter(self):
        page = (Path(__file__).resolve().parents[1]
                / "frontend" / "help" / "index.html")
        if not page.exists():
            raise unittest.SkipTest("help page not built")
        html_text = page.read_text(encoding="utf-8")
        self.assertIn("applyScope", html_text)
        self.assertIn("/api/modules", html_text)
        self.assertIn('data-access="salary"', html_text)
        # signing out must not be a way around it
        self.assertIn("granted === null", html_text)
