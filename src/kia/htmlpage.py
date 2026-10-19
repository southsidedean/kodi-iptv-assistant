"""HTML pages for the output folder: the status page and browser-friendly reports.

kia writes its reports as Markdown, which reads well in a text editor or on GitHub but
shows as raw text in a browser. Each report also gets an .html copy, made from the same
Markdown by a converter for the small part of Markdown the reports use: headings,
paragraphs, a quoted warning, bold, code, and tables.
"""

from __future__ import annotations

import re
from html import escape

STYLE = """
:root { --bg: #fafafa; --fg: #1d1d1f; --muted: #6e6e73; --line: #d2d2d7; --bad: #b3261e; --good: #1b7f3b; --note: #fff4e5; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161617; --fg: #f5f5f7; --muted: #a1a1a6; --line: #3a3a3c; --bad: #ff8a80; --good: #7ee2a0; --note: #3a2c14; }
}
body { background: var(--bg); color: var(--fg); font: 16px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 60rem; padding: 1.5rem 1rem; }
h1 { font-size: 1.4rem; margin: 0 0 1rem; }
h2 { font-size: 1.1rem; margin-top: 2rem; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid var(--line); padding: .45rem .25rem; text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: normal; }
.summary th { width: 45%; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.wide { overflow-x: auto; }
blockquote { background: var(--note); border-radius: .4rem; margin: 1rem 0; padding: .6rem .9rem; }
.result-failed td, .result-alerts td { color: var(--bad); } .result-ok td { color: var(--good); }
ul { padding-left: 1.2rem; } li span, .muted { color: var(--muted); }
code { word-break: break-all; }
a { color: inherit; }
input[type=search] { background: var(--bg); border: 1px solid var(--line); border-radius: .4rem; color: var(--fg); font: inherit; margin: .5rem 0; padding: .4rem .6rem; width: 100%; box-sizing: border-box; }
footer { color: var(--muted); font-size: .85rem; margin-top: 2rem; }
"""

# Tables longer than this get a filter box.
FILTER_ROWS = 25
FILTER_SCRIPT = """
<script>
document.querySelectorAll("input[data-filter]").forEach(function (box) {
  var rows = document.getElementById(box.dataset.filter).tBodies[0].rows;
  box.addEventListener("input", function () {
    var q = box.value.toLowerCase();
    for (var i = 0; i < rows.length; i++) {
      rows[i].hidden = q && rows[i].textContent.toLowerCase().indexOf(q) < 0;
    }
  });
});
</script>
"""


def page(title: str, body: str, footer: str = "", script: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{STYLE}</style>
</head>
<body>
{body}
<footer>{footer}</footer>
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
    footer = f'<a href="index.html">Status page</a> · <a href="{escape(markdown_name)}">Markdown version</a>'
    return page(title, body, footer, FILTER_SCRIPT if has_filter else "")
