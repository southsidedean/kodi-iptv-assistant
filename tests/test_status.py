import json
from pathlib import Path

import pytest

from kia import cli, status
from kia.alerts import EXIT_ALERT
from kia.m3u import Entry, write


def make_instance(tmp_path: Path) -> Path:
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    (tmp_path / "kia.toml").write_text('[output]\npublic_url = "http://tv.lan:8780"\n')
    out = tmp_path / "output"
    out.mkdir()
    entries = [Entry("A <b>", "u1", {"tvg-id": "A.us"}), Entry("KINK", "u2", {"tvg-id": "radio.k", "radio": "true"})]
    (out / "curated.m3u").write_text(write(entries))
    return tmp_path / "kia.toml"


@pytest.mark.parametrize("rc, result", [(0, "ok"), (EXIT_ALERT, "alerts"), (1, "failed")])
def test_run_records_its_result(tmp_path: Path, monkeypatch, rc, result):
    monkeypatch.delenv("KIA_PUBLIC_URL", raising=False)
    cfg = make_instance(tmp_path)
    seen = []

    def fake_pipeline(config, update_epg):
        seen.append(json.loads((tmp_path / "output" / "status.json").read_text())["result"])
        (tmp_path / "state" / "alerts.log").write_text("2099-01-01T00:00:00+00:00\tdns_local\tkia: local DNS problem\tx\n")
        return rc

    monkeypatch.setattr(cli, "run_pipeline", fake_pipeline)
    cli.run_and_report(str(cfg), update_epg=False)
    assert seen == ["running"]
    s = json.loads((tmp_path / "output" / "status.json").read_text())
    assert s["result"] == result and s["last_run"]["exit_code"] == rc
    assert s["channels"]["tv"] == 1 and s["channels"]["radio"] == 1
    assert s["playlist_url"] == "http://tv.lan:8780/curated.m3u"
    assert [a["event"] for a in s["alerts"]] == ["dns_local"]
    page = (tmp_path / "output" / "index.html").read_text()
    assert status.RESULTS[result] in page and "curated.m3u" in page


def test_crash_is_recorded(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_PUBLIC_URL", raising=False)
    cfg = make_instance(tmp_path)
    monkeypatch.setattr(cli, "run_pipeline", lambda c, u: 1 / 0)
    assert cli.run_and_report(str(cfg), update_epg=False) == 1
    assert json.loads((tmp_path / "output" / "status.json").read_text())["result"] == "failed"


def test_old_alert_lines_and_escaping(tmp_path: Path):
    log = tmp_path / "alerts.log"
    log.write_text(
        "2026-10-16T18:41:50+00:00\tkia check: old format\tmessage\n"
        "2026-10-19T09:18:35+00:00\tsite_failing\t<script>x</script>\tmessage\n"
    )
    found = status.recent_alerts(log, "2026-10-16T00:00:00+00:00")
    assert [a["title"] for a in found] == ["kia check: old format", "<script>x</script>"]
    assert status.recent_alerts(log, "2026-10-19T00:00:00+00:00")[0]["event"] == "site_failing"
    s = status.collect(tmp_path, tmp_path, {"result": "ok", "started": "2026-10-16T00:00:00+00:00"}, None)
    s["alerts"] = found
    page = status.render_html(s)
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_page_links_to_the_docs(tmp_path: Path):
    page = status.render_html(status.collect(tmp_path, tmp_path, {}, None))
    for url, name, _ in status.HELP:
        assert f'href="{url}"' in page and name in page
    # Each help link names a docs page that exists in this repository.
    root = Path(__file__).resolve().parent.parent
    for url, _, _ in status.HELP:
        assert (root / "docs" / url.rsplit("/", 1)[1]).exists(), url


def test_reports_link_html_and_markdown(tmp_path: Path):
    (tmp_path / "report.html").write_text("x")
    (tmp_path / "report.md").write_text("x")
    (tmp_path / "guide-report.html").write_text("x")  # no Markdown twin: no link to one
    page = status.render_html(status.collect(tmp_path, tmp_path, {}, None))
    assert 'href="report.html"' in page and '(<a href="report.md">Markdown</a>)' in page
    assert 'href="guide-report.md"' not in page
