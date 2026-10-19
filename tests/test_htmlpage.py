from kia import htmlpage
from kia.curate import render_markdown


def test_report_markdown_becomes_html():
    md = (
        "# Stream check report\n\nGenerated now\n\n"
        "> **Warning:** 3 streams failed on <local> DNS.\n\n"
        "| Channel | tvg-id | Streams |\n|---|---|---:|\n"
        + "".join(f"| Ch {i} & co | `C{i}.us` | {i} |\n" for i in range(30))
    )
    title, body, has_filter = htmlpage.markdown_to_html(md)
    assert title == "Stream check report" and has_filter
    assert "<blockquote><strong>Warning:</strong> 3 streams failed on &lt;local&gt; DNS.</blockquote>" in body
    assert '<td>Ch 0 &amp; co</td><td><code>C0.us</code></td><td class="num">0</td>' in body
    page = htmlpage.report_page(md, "report.md")
    assert 'href="report.md"' in page and "data-filter" in page and "<script>" in page


def test_short_tables_have_no_filter_and_real_report_renders():
    _, body, has_filter = htmlpage.markdown_to_html("# R\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert not has_filter and "<td>1</td><td>2</td>" in body
    title, body, _ = htmlpage.markdown_to_html(render_markdown([], [], "now"))
    assert title == "Stream check report" and "<table" in body and "|" not in body


def test_logo_files_match_the_code_and_pages_carry_it():
    from pathlib import Path

    images = Path(__file__).resolve().parent.parent / "images"
    assert (images / "kia-logo.svg").read_text().strip() == htmlpage.logo_svg(**htmlpage.LOGO_LIGHT, standalone=True)
    assert (images / "kia-logo-dark.svg").read_text().strip() == htmlpage.logo_svg(**htmlpage.LOGO_DARK, standalone=True)
    page = htmlpage.report_page("# Report\n", "report.md")
    assert 'rel="icon" href="data:image/svg+xml,' in page and 'class="brand" href="index.html"' in page
