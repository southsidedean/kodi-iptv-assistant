"""HTML pages for the output folder: the Dashboard and browser-friendly reports.

kia writes its reports as Markdown, which reads well in a text editor or on GitHub but
shows as raw text in a browser. Each report also gets an .html copy, made from the same
Markdown by a converter for the small part of Markdown the reports use: headings,
paragraphs, a quoted warning, bold, code, and tables.
"""

from __future__ import annotations

import re
from html import escape
from urllib.parse import quote

PROJECT_URL = "https://github.com/southsidedean/kodi-iptv-assistant"
DOCS_URL = f"{PROJECT_URL}/blob/main/docs"
# The Dashboard's pages, in the menu at the top of every page kia serves.
NAV = [("index.html", "Dashboard"), ("playlists.html", "Playlists"), ("sources.html", "Sources"),
       ("guide.html", "Guide"), ("system.html", "System")]  # fmt: skip

# The kia logo: a screen showing program guide rows, with the "now" line in amber.
LOGO_LIGHT = {"screen": "#1f2b4d", "rows": "#cfd8f0", "now": "#e5a52e"}
LOGO_DARK = {"screen": "#33447a", "rows": "#e3e9fb", "now": "#f2b84b"}


def logo_svg(screen: str, rows: str, now: str, standalone: bool = False) -> str:
    """The logo as SVG markup, in the given colors (CSS var() references work inline)."""
    bars = [(12, 16, 13, 0.9), (27, 16, 25, 0.55), (12, 26, 24, 0.55), (38, 26, 14, 0.9), (12, 36, 8, 0.9), (22, 36, 30, 0.55)]
    parts = [f'<rect x="4" y="8" width="56" height="42" rx="9" fill="{screen}"/>']
    parts += [f'<rect x="{x}" y="{y}" width="{w}" height="6" rx="3" fill="{rows}" opacity="{o}"/>' for x, y, w, o in bars]
    parts += [f'<rect x="30" y="12.5" width="3" height="33" rx="1.5" fill="{now}"/>',
              f'<rect x="24" y="53" width="16" height="4" rx="2" fill="{screen}"/>']  # fmt: skip
    ns = ' xmlns="http://www.w3.org/2000/svg"' if standalone else ""
    return f'<svg{ns} viewBox="0 0 64 64" role="img" aria-label="kia logo">{"".join(parts)}</svg>'


# The browser tab icon, and the mark at the top of every page (it follows the theme).
FAVICON = "data:image/svg+xml," + quote(logo_svg(**LOGO_LIGHT, standalone=True))
MARK = logo_svg("var(--logo-screen)", "var(--logo-rows)", "var(--logo-now)")

# The kia theme: one stylesheet for every page kia serves (the Dashboard and the
# reports). Colors, sizes, and spacing are tokens, light and dark follow the device's
# setting, fonts are each system's own (nothing loads from the internet), and narrow
# screens get a one-column layout with tap-sized controls.
STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f6f7fa; --card: #ffffff; --fg: #1b2033; --muted: #5d6478; --line: #dde1ea; --note: #fff4e5;
  --good: #1b7f3b; --good-bg: #e6f4ea; --warn: #8f5400; --warn-bg: #fdf1dc; --bad: #b3261e; --bad-bg: #fbe4e2;
  --info: #2f4fb5; --info-bg: #e5eafb;
  --bar-full: #2f6f4f; --bar-part: #e5a52e; --bar-none: #c9ccd6; --bar-dead: #d9534f;
  --logo-screen: #1f2b4d; --logo-rows: #cfd8f0; --logo-now: #e5a52e;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --step--1: .875rem; --step-0: 1rem; --step-1: 1.15rem; --step-2: 1.45rem; --step-3: 2rem;
  --space-1: .5rem; --space-2: .75rem; --space-3: 1rem; --space-4: 1.25rem; --space-5: 2rem;
  --radius: 12px; --radius-small: 8px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #121418; --card: #1c1f26; --fg: #eef0f5; --muted: #a3a9b8; --line: #2e3340; --note: #3a2c14;
    --good: #7ee2a0; --good-bg: #14301f; --warn: #f2c26b; --warn-bg: #3a2c14; --bad: #ff8a80; --bad-bg: #3d1a18;
    --info: #9fb3ff; --info-bg: #1c2447;
    --bar-full: #4fb07f; --bar-part: #f2b84b; --bar-none: #474b57; --bar-dead: #e2726e;
    --logo-screen: #33447a; --logo-rows: #e3e9fb; --logo-now: #f2b84b;
  }
}
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
body { background: var(--bg); color: var(--fg); font: var(--step-0)/1.5 var(--font); margin: 0 auto; max-width: 60rem;
  padding-inline: max(var(--space-3), env(safe-area-inset-left)); padding-block: var(--space-4) var(--space-5); }
h1 { font-size: var(--step-2); line-height: 1.2; margin: 0 0 var(--space-3); text-wrap: balance; }
h2 { font-size: var(--step-1); line-height: 1.3; margin: var(--space-5) 0 var(--space-2); }
a { color: inherit; text-underline-offset: 2px; }
a:focus-visible, button:focus-visible, input:focus-visible { outline: 2px solid var(--info); outline-offset: 2px; }
code { font-family: var(--mono); font-size: .92em; overflow-wrap: anywhere; }
ul { padding-left: 1.2rem; }
.muted, li span { color: var(--muted); }
footer { color: var(--muted); font-size: var(--step--1); margin-top: var(--space-5); }

/* Brand mark, menu, and Documentation button at the top of every page */
.top { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--space-2) var(--space-3);
  margin-bottom: var(--space-2); }
.brand { display: inline-flex; align-items: center; gap: var(--space-2); color: inherit; text-decoration: none;
  font-weight: 700; font-size: 2.6rem; line-height: 1; letter-spacing: -.01em; min-height: 44px; }
.brand svg { width: 4.25rem; height: 4.25rem; }
.docs-button { display: inline-flex; align-items: center; min-height: 44px; padding: .45rem 1rem; border: 1px solid var(--line);
  border-radius: var(--radius-small); background: var(--card); text-decoration: none; font-weight: 600; }
nav.menu { display: flex; flex-wrap: wrap; gap: .25rem; margin-bottom: var(--space-4); border-bottom: 1px solid var(--line); }
nav.menu a { display: inline-flex; align-items: center; min-height: 44px; padding: .4rem .8rem; text-decoration: none;
  color: var(--muted); border-bottom: 3px solid transparent; margin-bottom: -1px; }
nav.menu a:hover { color: var(--fg); }
nav.menu a[aria-current=page] { color: var(--fg); font-weight: 600; border-bottom-color: var(--logo-now); }
footer p { margin: 0 0 var(--space-1); }

/* Tables (reports) */
.wide { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid var(--line); padding: .5rem .35rem; text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: 500; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
table.settings { table-layout: fixed; }
table.settings td:first-child { width: 38%; }
table.settings td:last-child { width: 32%; font-size: .9em; overflow-wrap: anywhere; }
#settings details { border-top: 1px solid var(--line); padding: var(--space-1) 0; }
#settings summary { cursor: pointer; font-weight: 600; min-height: 44px; display: list-item; padding-top: .55rem; }
#settings summary .n { color: var(--muted); font-weight: 400; font-size: var(--step--1); margin-left: .4rem; }
blockquote { background: var(--note); border-radius: var(--radius-small); margin: var(--space-3) 0; padding: .6rem .9rem; }
input[type=search] { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-small); color: var(--fg);
  font: inherit; margin: var(--space-1) 0; padding: .55rem .7rem; width: 100%; min-height: 44px; }

/* Dashboard */
main { display: grid; gap: var(--space-4); }
main h2 { margin: 0 0 var(--space-2); }
.banner { display: flex; gap: var(--space-3); align-items: flex-start; padding: var(--space-3) var(--space-4); border-radius: var(--radius); }
.banner h1 { margin: 0 0 .25rem; }
.banner p { margin: 0; color: var(--fg); }
.dot { width: .9rem; height: .9rem; border-radius: 50%; margin-top: .45rem; flex: none; background: currentColor; }
.state-ok { background: var(--good-bg); color: var(--good); }
.state-alerts { background: var(--warn-bg); color: var(--warn); }
.state-failed { background: var(--bad-bg); color: var(--bad); }
.state-running, .state-never { background: var(--info-bg); color: var(--info); }
.state-running .dot { animation: pulse 1.6s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .3; } }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: var(--space-2); }
.tile, .card { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-3) var(--space-4); min-width: 0; }
a.tile { color: inherit; text-decoration: none; }
a.tile:hover { border-color: var(--info); }
.tile .n { font-size: var(--step-3); font-weight: 650; font-variant-numeric: tabular-nums; line-height: 1.1; }
.tile .l { color: var(--muted); font-size: var(--step--1); }
.bar { display: flex; height: .8rem; border-radius: 99px; overflow: hidden; background: var(--bar-none); }
.bar span { display: block; height: 100%; }
.legend { display: flex; flex-wrap: wrap; gap: .4rem 1.2rem; margin-top: .6rem; font-size: var(--step--1); color: var(--muted); font-variant-numeric: tabular-nums; }
.legend i { display: inline-block; width: .7rem; height: .7rem; border-radius: 3px; margin-right: .35rem; vertical-align: -1px; }
.sources { display: grid; gap: var(--space-2); }
.source { display: grid; grid-template-columns: minmax(6rem, 10rem) 1fr; gap: .3rem var(--space-3); align-items: center; }
.source strong { overflow-wrap: anywhere; }
.source .counts { grid-column: 2; font-size: var(--step--1); color: var(--muted); font-variant-numeric: tabular-nums; }
.alerts { margin: 0; padding-left: 1.1rem; display: grid; gap: .3rem; }
.copy { display: flex; flex-wrap: wrap; gap: var(--space-1); align-items: center; }
.copy code { background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius-small); padding: .5rem .65rem; min-width: 0; }
button { font: inherit; min-height: 44px; padding: .45rem 1rem; border-radius: var(--radius-small); border: 1px solid var(--line);
  background: var(--card); color: var(--fg); cursor: pointer; }
.pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: var(--space-4); align-items: start; }
.playlists { display: grid; gap: var(--space-3); }
.playlist { display: grid; gap: .35rem; }
.bad-note { background: var(--bad-bg); color: var(--fg); border-radius: var(--radius-small); padding: .6rem .8rem; }
.links { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); }
.links a { display: inline-block; padding-block: .1rem; }
.links span { display: block; font-size: var(--step--1); }
.pl-title { font-weight: 650; }
.copy + h2 { margin-top: var(--space-4); }
.card h2 + p, .card h1 + p { margin-top: 0; }

/* Phones */
@media (max-width: 34rem) {
  :root { --step-2: 1.3rem; --step-3: 1.7rem; }
  .brand { font-size: 2.1rem; }
  .brand svg { width: 3.5rem; height: 3.5rem; }
  nav.menu { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; gap: 0; }
  nav.menu a { padding: .4rem .5rem; flex: none; font-size: var(--step--1); }
  .tiles { grid-template-columns: repeat(2, 1fr); }
  .tiles .tile:last-child:nth-child(odd) { grid-column: 1 / -1; }
  .tile, .card { padding: var(--space-3); }
  .banner { padding: var(--space-3); }
  .source { grid-template-columns: 1fr; }
  .source .counts { grid-column: 1; }
  .copy code { flex: 1 1 100%; }
  table.settings, table.settings tbody, table.settings tr, table.settings td { display: block; width: auto !important; }
  table.settings tr { border-bottom: 1px solid var(--line); padding: .45rem 0; }
  table.settings td { border: 0; padding: .05rem .35rem; }
  table.settings td:first-child { color: var(--muted); }
}
@media (max-width: 24rem) { nav.menu a { padding-inline: .32rem; } }
@media (max-width: 21rem) { nav.menu a { padding-inline: .2rem; font-size: .8rem; } }
@media (prefers-reduced-motion: reduce) { .state-running .dot { animation: none; } }
"""

# Tables longer than this get a filter box.
FILTER_ROWS = 25
FILTER_SCRIPT = """
<script>
document.querySelectorAll("input[data-filter]").forEach(function (box) {
  var table = document.getElementById(box.dataset.filter);
  // The rows' text as written, before any buttons are added to them.
  Array.prototype.forEach.call(table.querySelectorAll("tbody tr"), function (row) { row._text = row.textContent.toLowerCase(); });
  var none = document.createElement("p");
  none.className = "muted"; none.hidden = true; none.setAttribute("role", "status");
  table.parentNode.before(none);
  box.setAttribute("aria-label", box.placeholder);
  box.addEventListener("input", function () {
    var q = box.value.toLowerCase(), total = 0;
    Array.prototype.forEach.call(table.tBodies, function (body) {
      var shown = 0, head = body.querySelector("tr.group-head");
      Array.prototype.forEach.call(body.rows, function (row) {
        if (row === head || row._text === undefined) return;
        row.hidden = q && row._text.indexOf(q) < 0;
        if (!row.hidden) shown++;
      });
      if (head) head.hidden = !shown;
      total += shown;
    });
    none.hidden = !q || total > 0;
    none.textContent = "No channels match \u201c" + box.value + "\u201d.";
  });
});
</script>
"""


def header(root: str = "", current: str = "") -> str:
    """The logo, the Documentation button, and the menu. current: the page shown, from NAV."""
    here = ' aria-current="page"'
    links = "".join(f'<a href="{root}{href}"{here if href == current else ""}>{label}</a>' for href, label in NAV)
    return (
        f'<header class="top"><a class="brand" href="{root}index.html">{MARK}<span>kia</span></a>'
        f'<a class="docs-button" href="{DOCS_URL}/README.md">Documentation</a></header>'
        f'<nav class="menu" aria-label="kia">{links}</nav>'
    )


def page(title: str, body: str, footer: str = "", script: str = "", style: str = "", root: str = "",
         current: str = "") -> str:  # fmt: skip
    """A kia page. root: the way back to the output folder from the page ("../" for playlists/).
    current: which menu item is this page (NAV)."""
    trouble = f'<p>Having problems? See the <a href="{DOCS_URL}/troubleshooting.md">troubleshooting guide</a>.</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f6f7fa" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#121418" media="(prefers-color-scheme: dark)">
<title>{escape(title)}</title>
<link rel="icon" href="{FAVICON}">
<style>{STYLE}{style}</style>
</head>
<body>
{header(root, current)}
{body}
<footer>{trouble}<p>{footer}</p></footer>
{script}
</body>
</html>
"""


def inline(text: str) -> str:
    """Escape text, then apply `code` and **bold**."""
    parts = re.split(r"(`[^`]*`)", text)
    out = []
    for part in parts:
        if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
            out.append(f"<code>{escape(part[1:-1])}</code>")
        else:
            out.append(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escape(part)))
    return "".join(out)


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _table(lines: list[str], n: int) -> str:
    header = _cells(lines[0])
    aligns = ["num" if c.endswith(":") else "" for c in _cells(lines[1])] if len(lines) > 1 else []

    def cls(i: int) -> str:
        return f' class="{aligns[i]}"' if i < len(aligns) and aligns[i] else ""

    head = "".join(f"<th{cls(i)}>{inline(c)}</th>" for i, c in enumerate(header))
    rows = "".join(
        "<tr>" + "".join(f"<td{cls(i)}>{inline(c)}</td>" for i, c in enumerate(_cells(row))) + "</tr>\n"
        for row in lines[2:]
    )
    table_id = f"t{n}"
    box = ""
    if len(lines) - 2 > FILTER_ROWS:
        box = f'<input type="search" placeholder="Filter these {len(lines) - 2} rows" data-filter="{table_id}">'
    return f'{box}<div class="wide"><table id="{table_id}"><thead><tr>{head}</tr></thead><tbody>\n{rows}</tbody></table></div>'


def markdown_to_html(markdown: str) -> tuple[str, str, bool]:
    """(title, body HTML, has_filter) for one of kia's Markdown reports."""
    title = ""
    blocks: list[str] = []
    lines = markdown.splitlines()
    i = 0
    tables = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("#"):
            level = min(len(line) - len(line.lstrip("#")), 3)
            text = line.lstrip("#").strip()
            title = title or text
            blocks.append(f"<h{level}>{inline(text)}</h{level}>")
            i += 1
        elif line.startswith("|"):
            start = i
            while i < len(lines) and lines[i].startswith("|"):
                i += 1
            tables += 1
            blocks.append(_table(lines[start:i], tables))
        elif line.startswith(">"):
            quoted = []
            while i < len(lines) and lines[i].startswith(">"):
                quoted.append(lines[i].lstrip(">").strip())
                i += 1
            blocks.append(f"<blockquote>{inline(' '.join(quoted))}</blockquote>")
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|", ">")):
                para.append(lines[i].strip())
                i += 1
            blocks.append(f"<p>{inline(' '.join(para))}</p>")
    body = "\n".join(blocks)
    return title or "Report", body, "data-filter=" in body


def report_page(markdown: str, markdown_name: str) -> str:
    title, body, has_filter = markdown_to_html(markdown)
    footer = f'<a href="{escape(markdown_name)}">Markdown version</a> of this report.'
    return page(title, body, footer, FILTER_SCRIPT if has_filter else "")
