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
    assert "<script>x</script>" not in page and "&lt;script&gt;x&lt;/script&gt;" in page


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
    page = status.render_details(status.collect(tmp_path, tmp_path, {}, None))
    assert 'href="report.html"' in page and '(<a href="report.md">Markdown</a>)' in page
    assert 'href="guide-report.md"' not in page


def test_coverage_and_playlist_counts(tmp_path: Path):
    import json as _json

    (tmp_path / "report.json").write_text(_json.dumps({"streams": [
        {"source": "us", "status": "ok"}, {"source": "us", "status": "dead"}, {"source": "us", "status": "ok"},
        {"source": "samsung-us", "status": "flaky"}]}))  # fmt: skip
    s = status.collect(tmp_path, tmp_path, {"result": "alerts", "coverage": {"full": 5, "partial": 2, "none": 3}}, "http://tv.lan:8780")
    assert s["sources"] == [{"name": "us", "ok": 2, "flaky": 0, "dead": 1}, {"name": "samsung-us", "ok": 0, "flaky": 1, "dead": 0}]
    page = status.render_html(s)
    assert "Working, with alerts" in page and 'href="details.html"' in page
    assert 'data-copy="copy-0"' in page and "http://tv.lan:8780/curated.m3u" in page
    details = status.render_details(s)
    assert "Full guide: 5" in details and "2 working, 0 flaky, 1 dead" in details


def test_a_page_per_playlist(tmp_path: Path):
    from kia import profiles
    from kia.m3u import Entry, write

    out = tmp_path / "output"
    out.mkdir()
    entries = [Entry("Toons <1>", "http://a/1", {"tvg-id": "T.us", "group-title": "Kids", "tvg-chno": "7"}, source="us"),
               Entry("CNN", "http://a/2", {"tvg-id": "C.us", "group-title": "News", "tvg-chno": "8"}, source="us")]  # fmt: skip
    (out / "curated.m3u").write_text(write(entries, None))
    pfile = tmp_path / "profiles.toml"
    pfile.write_text('[kids]\ntitle = "The kids"\ngroups = ["Kids"]\n[old]\n')
    profiles.rebuild(out, tmp_path / "state", pfile, None, lambda m: None)
    status.update(out, tmp_path / "state", "http://tv.lan:8780")
    home = (out / "index.html").read_text()
    assert 'href="playlists/kids.html"' in home and 'href="playlists/curated.html"' in home
    kids = (out / "playlists" / "kids.html").read_text()
    assert 'data-profile="kids"' in kids and 'data-root="../"' in kids and 'href="../index.html"' in kids
    assert "Toons &lt;1&gt;" in kids and "CNN" not in kids and "http://tv.lan:8780/kids.m3u" in kids
    everything = (out / "playlists" / "curated.html").read_text()
    assert 'data-profile="' not in everything and "CNN" in everything and ">8<" in everything
    assert (out / "playlists" / "old.html").exists() and (out / "details.html").exists()
    pfile.write_text('[kids]\ngroups = ["Kids"]\n')
    profiles.rebuild(out, tmp_path / "state", pfile, None, lambda m: None)
    status.update(out, tmp_path / "state", "http://tv.lan:8780")
    assert not (out / "playlists" / "old.html").exists()


def test_guide_preview(tmp_path: Path):
    from datetime import datetime, timezone

    from kia import xmltv

    guide = tmp_path / "guide.xml"
    guide.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n'
        '<programme channel="A.us" start="20261019100000 +0000" stop="20261019110000 +0000"><title>Over</title></programme>\n'
        '<programme channel="A.us" start="20261019113000 +0000" stop="20261019130000 +0000"><title>Now &amp; then</title></programme>\n'
        '<programme channel="A.us" start="20261019130000 +0000" stop="20261019140000 +0000"><title>Next</title></programme>\n'
        '<programme channel="A.us" start="20261020050000 +0000" stop="20261020060000 +0000"><title>Too late</title></programme>\n'
        '<programme channel="B.us" start="20261019120000 +0000" stop="20261019140000 +0000" kia-placeholder="1"><title>B</title></programme>\n'
        "</tv>\n"
    )
    out = tmp_path / "guide-preview.json"
    assert xmltv.write_preview(guide, out, datetime(2026, 10, 19, 12, tzinfo=timezone.utc)) == 2
    data = json.loads(out.read_text())
    titles = [t for _, _, t in data["channels"]["A.us"]]
    assert titles == ["Now & then", "Next"] and "B.us" not in data["channels"]  # name blocks aren't listings
    assert data["channels"]["A.us"][0][:2] == [int(datetime(2026, 10, 19, 11, 30, tzinfo=timezone.utc).timestamp()),
                                               int(datetime(2026, 10, 19, 13, tzinfo=timezone.utc).timestamp())]  # fmt: skip
    from kia.m3u import Entry

    page = status.render_playlist(status.collect(tmp_path, tmp_path, {}, None), status._playlists(
        status.collect(tmp_path, tmp_path, {}, None))[0], [Entry("A", "http://a", {"tvg-id": "A.us", "tvg-chno": "1"})])
    assert 'data-id="A.us"' in page and "guide-preview.json" in page and 'data-view="timeline"' in page


def test_settings_on_details_hide_secrets(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("KIA_NTFY_URL", "https://ntfy.sh/very-secret-topic")
    monkeypatch.setenv("KIA_ALERT_COMMAND", "curl https://hooks.example/secret-token")
    monkeypatch.setenv("KIA_ADMIN_PASSWORD", "hunter2-secret")
    monkeypatch.setenv("KIA_EXCLUDE_GROUPS", "Shop")
    cfg = cli.load(make_instance(tmp_path))
    cli.write_settings(cfg, "03:30", 0)
    status.update(cfg.output_dir, cfg.state_dir, cfg.public_url)
    rows = {r["name"]: r for sec in json.loads((tmp_path / "output" / "status.json").read_text())["settings"]
            for r in sec["rows"]}  # fmt: skip
    assert rows["Nightly refresh"]["value"] == "03:30" and rows["Quick guide refresh"]["value"] == "off"
    assert rows["Left-out categories"]["value"] == "Shop"
    assert rows["Left-out categories"]["change"] == ".env: KIA_EXCLUDE_GROUPS"
    assert rows["ntfy address"]["value"] == "set" and rows["Alert command"]["value"] == "set"
    assert rows["Password for changes"]["value"] == "your own, from .env"
    details = (tmp_path / "output" / "details.html").read_text()
    assert 'id="settings"' in details and "03:30" in details
    assert cli.main(["settings", "-c", str(tmp_path / "kia.toml")]) == 0
    shown = capsys.readouterr().out
    assert "03:30" in shown and "KIA_RUN_AT" in shown
    for where in (details, shown, (tmp_path / "output" / "status.json").read_text(),
                  (tmp_path / "state" / "settings.json").read_text()):  # fmt: skip
        assert "secret" not in where


def test_details_without_settings(tmp_path: Path):
    cfg = cli.load(make_instance(tmp_path))
    status.update(cfg.output_dir, cfg.state_dir, cfg.public_url)
    assert 'id="settings"' not in (tmp_path / "output" / "details.html").read_text()
