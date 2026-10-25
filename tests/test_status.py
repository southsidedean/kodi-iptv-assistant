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


def test_every_page_has_the_menu_and_help(tmp_path: Path):
    from kia import htmlpage

    s = status.collect(tmp_path, tmp_path, {}, None)
    root = Path(__file__).resolve().parent.parent
    for name, render in status.PAGES.items():
        page = render(s)
        for href, label in htmlpage.NAV:
            assert f'href="{href}"' in page and f">{label}</a>" in page, (name, href)
        assert f'href="{htmlpage.DOCS_URL}/README.md" target="_blank" rel="noopener noreferrer">Documentation</a>' in page
        assert f'{htmlpage.DOCS_URL}/troubleshooting.md">troubleshooting guide</a>' in page
        here = f'href="{name}" aria-current="page"'
        assert (here in page) == (name != "details.html"), name
    for doc in ("README.md", "troubleshooting.md"):
        assert (root / "docs" / doc).exists()
    # A playlist's page sits one folder down and marks Playlists in the menu.
    pl = status.render_playlist(s, status._playlists(s)[0], [])
    assert 'href="../playlists.html" aria-current="page"' in pl and 'href="../index.html"' in pl


def test_details_points_to_the_new_pages(tmp_path: Path):
    page = status.render_details(status.collect(tmp_path, tmp_path, {}, None))
    assert "Details has moved" in page
    for href, _, _ in status.MOVED:
        assert f'<a href="{href}">' in page and href in status.PAGES


def test_reports_link_html_and_markdown(tmp_path: Path):
    (tmp_path / "report.html").write_text("x")
    (tmp_path / "report.md").write_text("x")
    (tmp_path / "guide-report.html").write_text("x")  # no Markdown twin: no link to one
    s = status.collect(tmp_path, tmp_path, {}, None)
    page = status.render_system(s)
    assert 'href="report.html"' in page and '(<a href="report.md">Markdown</a>)' in page
    assert 'href="guide-report.md"' not in page
    assert 'href="guide-report.html"' in status.render_guide(s) and 'href="report.html"' not in status.render_guide(s)


def test_coverage_and_playlist_counts(tmp_path: Path):
    import json as _json

    (tmp_path / "report.json").write_text(_json.dumps({"streams": [
        {"source": "us", "status": "ok"}, {"source": "us", "status": "dead"}, {"source": "us", "status": "ok"},
        {"source": "samsung-us", "status": "flaky"}]}))  # fmt: skip
    s = status.collect(tmp_path, tmp_path, {"result": "alerts", "coverage": {"full": 5, "partial": 2, "none": 3}}, "http://tv.lan:8780")
    assert s["sources"] == [{"name": "us", "ok": 2, "flaky": 0, "dead": 1}, {"name": "samsung-us", "ok": 0, "flaky": 1, "dead": 0}]
    page = status.render_html(s)
    assert "Working, with alerts" in page and 'href="guide.html"' in page and 'href="playlists.html"' in page
    assert 'data-copy="copy-0"' in page and "http://tv.lan:8780/curated.m3u" in page
    assert "data-add" not in page  # making a playlist is on the Playlists page
    assert "data-add" in status.render_playlists(s) and "http://tv.lan:8780/curated.m3u" in status.render_playlists(s)
    assert "Full guide: 5" in status.render_guide(s)
    assert "2 working, 0 flaky, 1 dead" in status.render_sources(s) and 'id="sources-editor"' in status.render_sources(s)


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


def test_long_playlist_pages_show_favorites_and_the_first_channels(tmp_path: Path):
    from kia.m3u import Entry

    s = status.collect(tmp_path, tmp_path, {}, None)
    entries = [Entry("Fave", "http://a/0", {"tvg-id": "F.us", "group-title": "News;Favorites"})]
    entries += [Entry(f"C{i}", f"http://a/{i}", {"tvg-id": f"C{i}.us"}) for i in range(1, 300)]
    page = status.render_playlist(s, status._playlists(s)[0], entries)
    assert page.count(" data-fav") == 1 and '<tr data-id="F.us" data-key="F.us" data-fav>' in page
    assert f'data-limit="{status.LIST_LIMIT}"' in page and "Show all" in page
    assert "Hide channels without guide data" in page and 'data-filter="channels"' in page
    # The guide view hears about rows the list hides, to keep its timeline in step.
    from kia import guideview

    assert 'new Event("rows-changed")' in page and 'addEventListener("rows-changed"' in guideview.SCRIPT


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
    details = (tmp_path / "output" / "system.html").read_text()
    assert 'id="settings"' in details and "03:30" in details
    assert "<details><summary>" in details  # each group of settings folds away
    assert cli.main(["settings", "-c", str(tmp_path / "kia.toml")]) == 0
    shown = capsys.readouterr().out
    assert "03:30" in shown and "KIA_RUN_AT" in shown
    for where in (details, shown, (tmp_path / "output" / "status.json").read_text(),
                  (tmp_path / "state" / "settings.json").read_text()):  # fmt: skip
        assert "secret" not in where


def test_details_without_settings(tmp_path: Path):
    cfg = cli.load(make_instance(tmp_path))
    status.update(cfg.output_dir, cfg.state_dir, cfg.public_url)
    assert 'id="settings"' not in (tmp_path / "output" / "system.html").read_text()


def test_getting_started_checklist():
    settings = [{"title": "Alerts", "rows": [{"name": "ntfy address", "value": "not set", "change": ""},
                                            {"name": "Email", "value": "set", "change": ""}]},
                {"title": "Addresses", "rows": [{"name": "Password for changes", "value": "made by kia, in state/admin-password.txt", "change": ""}]}]  # fmt: skip
    s = {"last_run": {"finished": None}, "playlist_url": "http://localhost:8780/curated.m3u", "settings": settings}
    items = {i["id"]: i for i in status.checklist(s)}
    assert not items["refresh"]["done"] and not items["address"]["done"] and items["address"]["manual"]
    assert "This computer only" in items["address"]["text"]
    assert items["alerts"]["done"] and not items["password"]["done"] and items["player"]["manual"]
    card = status._checklist_card(s)
    assert 'id="getting-started"' in card and 'data-id="alerts" data-done' in card
    s.update(last_run={"finished": "2026-10-20T04:40:00+00:00"}, playlist_url="http://192.168.1.20:8780/curated.m3u")
    settings[1]["rows"][0]["value"] = "your own, from .env"
    assert all(i["done"] for i in status.checklist(s) if not i.get("manual") or i["id"] == "password")


def test_checklist_doesnt_tick_the_example_address():
    s = {"last_run": {"finished": None}, "playlist_url": "http://my-server.lan:8780/curated.m3u", "settings": []}
    assert not next(i for i in status.checklist(s) if i["id"] == "address")["done"]


def test_a_readable_env_file_is_flagged(tmp_path: Path):
    from kia import cli

    env = tmp_path / ".env"
    assert cli.env_private(env) is None  # no .env: nothing to say
    env.write_text("KIA_ADMIN_PASSWORD=x\n")
    env.chmod(0o644)
    assert cli.env_private(env) is False
    s = status.collect(tmp_path, tmp_path, {}, None)
    s["env_private"] = False
    assert "chmod 600 .env" in status.render_html(s)
    env.chmod(0o600)
    assert cli.env_private(env) is True
    for value in (True, None):
        s["env_private"] = value
        assert 'id="env-warning"' not in status.render_html(s)


def test_the_observability_button_opens_grafana_over_https(monkeypatch):
    from kia import htmlpage

    monkeypatch.delenv("KIA_GRAFANA_URL", raising=False)
    monkeypatch.delenv("KIA_GRAFANA_PORT", raising=False)
    monkeypatch.setenv("KIA_PUBLIC_URL", "http://192.168.1.20:8780")
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "plex,monitoring")
    assert htmlpage.grafana_url() == "https://192.168.1.20:8788"  # Grafana in the stack serves HTTPS only
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "plex")
    assert htmlpage.grafana_url() is None


def test_the_header_buttons_open_in_a_new_tab(monkeypatch):
    from kia import htmlpage

    monkeypatch.setenv("KIA_GRAFANA_URL", "http://192.168.1.20:8788")
    head = htmlpage.header("index.html")
    for label in ("Observability", "Documentation"):
        link = next(a for a in head.split("<a ")[1:] if f">{label}</a>" in a)
        assert 'target="_blank"' in link and "noopener" in link, label


def test_settings_show_the_stack(tmp_path: Path, monkeypatch):
    from kia import settings

    cfg = cli.load(make_instance(tmp_path))

    def parts() -> str:
        return {r["name"]: r for s in settings.describe(cfg) for r in s["rows"]}["Parts"]["value"]

    monkeypatch.delenv("KIA_STACK", raising=False)
    monkeypatch.delenv("KIA_COMPOSE_PROFILES", raising=False)
    assert parts() == "not running in Docker"
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "plex,monitoring")
    assert parts() == "not set (COMPOSE_PROFILES: plex, monitoring)"
    monkeypatch.setenv("KIA_STACK", "plex,observability")
    assert parts() == "plex, observability"
