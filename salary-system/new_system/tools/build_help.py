"""Turn docs/USER_GUIDE.md into the in-app help page at frontend/help/.

Why a generator and not a markdown library: the app ships as a frozen .exe that
must work with no network and no build step, and the core deliberately depends
only on the standard library. So this converts the small, known subset of
markdown the guide actually uses (headings, paragraphs, images, tables, bullets,
ordered lists, blockquotes, rules, and inline bold/code/link) and writes ONE
self-contained HTML file. The output is committed, so nothing has to run at
install time.

Run after editing the guide:

    python tools/build_help.py

It rewrites frontend/help/index.html and refreshes frontend/help/images/.
"""

from __future__ import annotations

import html
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # …/new_system
REPO = ROOT.parent.parent                              # repo root
GUIDE = REPO / "docs" / "USER_GUIDE.md"
IMAGES = REPO / "docs" / "guide-images"
OUT_DIR = ROOT / "frontend" / "help"
OUT_HTML = OUT_DIR / "index.html"
OUT_IMAGES = OUT_DIR / "images"


# --------------------------------------------------------------------------- #
# Inline formatting
# --------------------------------------------------------------------------- #
def slug(text: str) -> str:
    """GitHub-style anchor, so links already written in the guide keep working."""
    s = re.sub(r"[^\w\s-]", "", text.lower())
    # ONE hyphen per space, not one per run: "it? — the" drops the punctuation
    # and leaves two spaces, which GitHub renders as "it--the". Collapsing them
    # would break the guide's own cross-links.
    return re.sub(r"\s", "-", s.strip())


def inline(text: str) -> str:
    """Escape first, then re-introduce the handful of inline markers."""
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    # Bold FIRST and non-greedy, so nested emphasis survives: "**a *b* c**"
    # must become <strong>a <em>b</em> c</strong>. A [^*]+ body would refuse the
    # inner asterisks and leave the ** visible in the page.
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    # single * only where it isn't part of a ** pair we've already consumed
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", t)

    def link(m):
        label, target = m.group(1), m.group(2)
        if target.startswith("#"):
            return f'<a href="{html.escape(target)}">{label}</a>'
        if target.startswith(("http://", "https://")):
            return f'<a href="{html.escape(target)}" target="_blank" rel="noopener">{label}</a>'
        # a link to another repo document is meaningless inside the app
        return label

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, t)


# --------------------------------------------------------------------------- #
# Block conversion
# --------------------------------------------------------------------------- #
def convert(md: str) -> tuple[str, list[dict]]:
    lines = md.splitlines()
    out: list[str] = []
    toc: list[dict] = []
    i = 0
    n = len(lines)

    def close(tag: str, open_flag: bool) -> bool:
        if open_flag:
            out.append(f"</{tag}>")
        return False

    ul_open = ol_open = False

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # blank
        if not stripped:
            ul_open = close("ul", ul_open)
            ol_open = close("ol", ol_open)
            i += 1
            continue

        # horizontal rule
        if stripped == "---":
            ul_open = close("ul", ul_open)
            ol_open = close("ol", ol_open)
            out.append("<hr>")
            i += 1
            continue

        # headings
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            ul_open = close("ul", ul_open)
            ol_open = close("ol", ol_open)
            level, text = len(m.group(1)), m.group(2)
            anchor = slug(text)
            out.append(f'<h{level} id="{anchor}">{inline(text)}</h{level}>')
            if level in (2, 3):
                toc.append({"level": level, "text": text, "anchor": anchor})
            i += 1
            continue

        # image on its own line
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
        if m:
            ul_open = close("ul", ul_open)
            ol_open = close("ol", ol_open)
            alt, src = html.escape(m.group(1)), m.group(2).split("/")[-1]
            out.append(
                f'<figure><img src="images/{html.escape(src)}" alt="{alt}" loading="lazy">'
                + (f"<figcaption>{alt}</figcaption>" if alt else "")
                + "</figure>"
            )
            i += 1
            continue

        # table
        if stripped.startswith("|"):
            ul_open = close("ul", ul_open)
            ol_open = close("ol", ol_open)
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            if len(rows) >= 2 and all(set(c) <= set("-: ") for c in rows[1]):
                head, body = rows[0], rows[2:]
            else:
                head, body = None, rows
            out.append("<div class='tablewrap'><table>")
            if head:
                out.append("<thead><tr>"
                           + "".join(f"<th>{inline(c)}</th>" for c in head)
                           + "</tr></thead>")
            out.append("<tbody>")
            for r in body:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            out.append("</tbody></table></div>")
            continue

        # blockquote (callout) — consecutive '>' lines join into one
        if stripped.startswith(">"):
            ul_open = close("ul", ul_open)
            ol_open = close("ol", ol_open)
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f"<blockquote>{inline(' '.join(buf))}</blockquote>")
            continue

        # ordered list
        m = re.match(r"^\d+\.\s+(.*)$", stripped)
        if m:
            ul_open = close("ul", ul_open)
            if not ol_open:
                out.append("<ol>")
                ol_open = True
            item, i = _gather_item(lines, i, m.group(1))
            out.append(f"<li>{inline(item)}</li>")
            continue

        # bullet list
        m = re.match(r"^[-*]\s+(.*)$", stripped)
        if m:
            ol_open = close("ol", ol_open)
            if not ul_open:
                out.append("<ul>")
                ul_open = True
            item, i = _gather_item(lines, i, m.group(1))
            out.append(f"<li>{inline(item)}</li>")
            continue

        # paragraph — join continuation lines
        ul_open = close("ul", ul_open)
        ol_open = close("ol", ol_open)
        buf = [stripped]
        i += 1
        while i < n and lines[i].strip() and not _starts_block(lines[i]):
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(buf))}</p>")

    close("ul", ul_open)
    close("ol", ol_open)
    return "\n".join(out), toc


def _starts_block(line: str) -> bool:
    s = line.strip()
    return (s.startswith(("#", ">", "|", "!["))
            or s == "---"
            or bool(re.match(r"^[-*]\s+", s))
            or bool(re.match(r"^\d+\.\s+", s)))


def _gather_item(lines: list[str], i: int, first: str) -> tuple[str, int]:
    """A list item plus any indented continuation lines."""
    buf = [first]
    i += 1
    while i < len(lines):
        nxt = lines[i]
        if not nxt.strip() or _starts_block(nxt) or not nxt.startswith(("  ", "\t")):
            break
        buf.append(nxt.strip())
        i += 1
    return " ".join(buf), i


# --------------------------------------------------------------------------- #
# Page template
# --------------------------------------------------------------------------- #
PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>User Guide — APEX THERMOCON</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI",
         Roboto, sans-serif; color: #1e293b; background: #f1f5f9; }}
  header {{ position: sticky; top: 0; z-index: 20; display: flex; align-items: center;
           gap: 1rem; padding: .75rem 1.5rem; background: #0f172a; color: #cbd5e1; }}
  header .home {{ display: inline-flex; align-items: center; gap: .4rem; background: #1e293b;
                 color: #e2e8f0; text-decoration: none; padding: .5rem .75rem;
                 border-radius: .5rem; font-size: .875rem; font-weight: 500; }}
  header .home:hover {{ background: #334155; color: #fff; }}
  header h1 {{ font-size: 1rem; margin: 0; color: #fff; }}
  header .sub {{ font-size: .6875rem; color: #94a3b8; }}
  header .search {{ margin-left: auto; }}
  header input {{ width: 17rem; padding: .5rem .75rem; border-radius: .5rem;
                 border: 1px solid #334155; background: #1e293b; color: #e2e8f0;
                 font-size: .875rem; }}
  header input::placeholder {{ color: #64748b; }}

  .layout {{ display: flex; align-items: flex-start; max-width: 78rem; margin: 0 auto;
            gap: 2rem; padding: 1.5rem; }}
  nav {{ position: sticky; top: 4.5rem; width: 17rem; flex: 0 0 17rem;
        max-height: calc(100vh - 6rem); overflow-y: auto; background: #fff;
        border-radius: .75rem; padding: 1rem; box-shadow: 0 1px 2px rgba(0,0,0,.05); }}
  nav a {{ display: block; padding: .3rem .5rem; border-radius: .375rem; font-size: .8125rem;
          color: #475569; text-decoration: none; }}
  nav a:hover {{ background: #f1f5f9; color: #0f172a; }}
  nav a.h2 {{ font-weight: 600; color: #0f172a; margin-top: .35rem; }}
  nav a.h3 {{ padding-left: 1.25rem; }}
  nav a.on {{ background: #1d4ed8; color: #fff; }}
  nav .none {{ font-size: .8125rem; color: #94a3b8; padding: .5rem; }}

  main {{ flex: 1; min-width: 0; background: #fff; border-radius: .75rem; padding: 2rem 2.5rem;
         box-shadow: 0 1px 2px rgba(0,0,0,.05); }}
  main h1 {{ font-size: 1.875rem; margin: 0 0 .5rem; }}
  main h2 {{ font-size: 1.375rem; margin: 2.5rem 0 .75rem; padding-top: .5rem;
            border-top: 1px solid #e2e8f0; }}
  main h2:first-of-type {{ border-top: 0; margin-top: 1rem; }}
  main h3 {{ font-size: 1.0625rem; margin: 1.75rem 0 .5rem; color: #1d4ed8; }}
  main p {{ line-height: 1.65; margin: .75rem 0; }}
  main ul, main ol {{ line-height: 1.65; padding-left: 1.25rem; }}
  main li {{ margin: .35rem 0; }}
  main hr {{ border: 0; border-top: 1px solid #e2e8f0; margin: 2rem 0; }}
  main code {{ background: #f1f5f9; padding: .1rem .35rem; border-radius: .25rem;
              font-size: .875em; }}
  blockquote {{ margin: 1rem 0; padding: .875rem 1.125rem; background: #eff6ff;
               border-left: 3px solid #1d4ed8; border-radius: 0 .5rem .5rem 0;
               line-height: 1.6; }}
  figure {{ margin: 1.25rem 0; }}
  figure img {{ max-width: 100%; border: 1px solid #e2e8f0; border-radius: .5rem;
               display: block; }}
  figcaption {{ font-size: .75rem; color: #94a3b8; margin-top: .4rem; }}
  .tablewrap {{ overflow-x: auto; margin: 1rem 0; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .875rem; }}
  th, td {{ text-align: left; padding: .5rem .75rem; border-bottom: 1px solid #e2e8f0;
           vertical-align: top; }}
  th {{ background: #f8fafc; font-size: .75rem; text-transform: uppercase;
       letter-spacing: .03em; color: #64748b; }}
  mark {{ background: #fef08a; }}
  .hidden {{ display: none; }}

  @media print {{
    header, nav {{ display: none; }}
    .layout {{ display: block; padding: 0; }}
    main {{ box-shadow: none; padding: 0; }}
    figure img {{ max-width: 60%; }}
  }}
  @media (max-width: 60rem) {{
    .layout {{ flex-direction: column; }}
    nav {{ position: static; width: 100%; flex: none; max-height: none; }}
  }}
</style>
</head>
<body>
<header>
  <a class="home" href="/" title="Back to all modules"><span>⌂</span> Home</a>
  <div>
    <h1>User Guide</h1>
    <div class="sub">APEX THERMOCON · how to do everything, with pictures</div>
  </div>
  <div class="search">
    <input id="q" type="search" placeholder="Search the guide…" autocomplete="off">
  </div>
</header>

<div class="layout">
  <nav id="toc">
{toc}
  </nav>
  <main id="content">
{body}
  </main>
</div>

<script>
// Jump-to highlighting: mark the section you are reading in the sidebar.
const links = [...document.querySelectorAll('#toc a')];
const targets = links.map(a => document.getElementById(a.hash.slice(1))).filter(Boolean);
function onScroll() {{
  let current = null;
  for (const t of targets) {{
    if (t.getBoundingClientRect().top <= 120) current = t; else break;
  }}
  links.forEach(a => a.classList.toggle('on', current && a.hash === '#' + current.id));
}}
document.addEventListener('scroll', onScroll, {{ passive: true }});
onScroll();

// Search: filter the contents list, and highlight hits in the page.
const q = document.getElementById('q');
const content = document.getElementById('content');
const blocks = [...content.children];
let none = null;
q.addEventListener('input', () => {{
  const term = q.value.trim().toLowerCase();
  document.querySelectorAll('#content mark').forEach(m => m.replaceWith(m.textContent));
  if (!term) {{
    blocks.forEach(b => b.classList.remove('hidden'));
    links.forEach(a => a.classList.remove('hidden'));
    if (none) {{ none.remove(); none = null; }}
    return;
  }}
  // show a whole chapter when anything inside it matches
  let keep = false, hits = 0;
  for (const b of blocks) {{
    if (/^H[12]$/.test(b.tagName)) keep = b.textContent.toLowerCase().includes(term);
    const self = b.textContent.toLowerCase().includes(term);
    if (self) {{ keep = true; hits++; }}
    b.classList.toggle('hidden', !(keep || self));
  }}
  links.forEach(a => a.classList.toggle('hidden', !a.textContent.toLowerCase().includes(term)));
  if (!hits && !none) {{
    none = document.createElement('p');
    none.textContent = 'Nothing in the guide matches “' + q.value + '”.';
    content.appendChild(none);
  }} else if (hits && none) {{ none.remove(); none = null; }}
}});
</script>
</body>
</html>
"""


def main() -> int:
    if not GUIDE.exists():
        print(f"guide not found: {GUIDE}", file=sys.stderr)
        return 1

    body, toc = convert(GUIDE.read_text(encoding="utf-8"))
    toc_html = "\n".join(
        f'    <a class="h{t["level"]}" href="#{t["anchor"]}">{html.escape(t["text"])}</a>'
        for t in toc)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if OUT_IMAGES.exists():
        shutil.rmtree(OUT_IMAGES)
    OUT_IMAGES.mkdir(parents=True)

    used = set(re.findall(r'src="images/([^"]+)"', body))
    for name in sorted(used):
        src = IMAGES / name
        if src.exists():
            shutil.copy2(src, OUT_IMAGES / name)
        else:
            print(f"  !! missing image: {name}", file=sys.stderr)

    OUT_HTML.write_text(PAGE.format(toc=toc_html, body=body), encoding="utf-8")
    print(f"wrote {OUT_HTML.relative_to(ROOT)}  "
          f"({len(toc)} contents entries, {len(used)} images, "
          f"{len(body.splitlines())} blocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
