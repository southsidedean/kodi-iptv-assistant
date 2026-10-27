import time
from pathlib import Path

import pytest

from kia.curate import Checked, classify, is_excluded, select
from kia.history import History
from kia.m3u import Entry
from kia.probe import ProbeResult, interpret


def entry(name, url, tvg_id="", group="", source="us"):
    return Entry(name=name, url=url, attrs={"tvg-id": tvg_id, "group-title": group}, source=source)


def test_interpret_picks_best_variant():
    payload = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 360},
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            {"codec_type": "audio", "codec_name": "aac"},
        ]
    }
    r = interpret(payload)
    assert r.ok and r.height == 1080 and r.audio_codec == "aac"


def test_interpret_audio_only():
    payload = {"streams": [{"codec_type": "audio", "codec_name": "aac"}]}
    assert not interpret(payload).ok
    assert interpret(payload).error == "no video stream"
    assert interpret(payload, require_video=False).ok


def test_classify_grace_period():
    fail = ProbeResult(ok=False, error="timed out")
    assert classify(ProbeResult(ok=True), {}, 2) == "ok"
    assert classify(fail, {}, 2) == "dead"
    assert classify(fail, {"last_ok": "x", "consecutive_failures": 2}, 2) == "flaky"
    assert classify(fail, {"last_ok": "x", "consecutive_failures": 3}, 2) == "dead"


def test_select_prefers_working_then_resolution_then_speed():
    a = Checked(entry("CNN (720p)", "u1", "CNN.us"), ProbeResult(ok=True, elapsed=1), "ok", 720)
    b = Checked(entry("CNN (1080p)", "u2", "CNN.us"), ProbeResult(ok=True, elapsed=3), "ok", 1080)
    c = Checked(entry("CNN alt", "u3", "CNN.us"), ProbeResult(ok=False), "flaky", 2160)
    d = Checked(entry("Dead TV", "u4", "Dead.us"), ProbeResult(ok=False), "dead")
    chosen = select([a, b, c, d])
    assert [x.entry.url for x in chosen] == ["u2"]
    assert b.selected and not a.selected and not c.selected and not d.selected


def test_select_groups_untagged_by_name():
    a = Checked(entry("Town TV (480p)", "u1"), ProbeResult(ok=True), "ok", 480)
    b = Checked(entry("Town TV (720p) [Not 24/7]", "u2"), ProbeResult(ok=True), "ok", 720)
    assert [x.entry.url for x in select([a, b])] == ["u2"]


def test_exclusions():
    e = entry("Shop Now [Not 24/7]", "u", group="Shop;General")
    assert is_excluded(e, ["shop"], [], [])
    assert is_excluded(e, [], ["not 24/7"], [])
    assert is_excluded(e, [], [], [r"^shop"])
    assert not is_excluded(e, ["News"], ["Geo-blocked"], ["^News"])


def test_history_counts_and_roundtrip(tmp_path: Path):
    h = History(tmp_path / "history.json")
    h.record("u", ProbeResult(ok=True, height=720), "2026-10-16T00:00:00+00:00")
    h.record("u", ProbeResult(ok=False, error="403"), "2026-10-17T00:00:00+00:00")
    h.save()
    rec = History(tmp_path / "history.json").get("u")
    assert rec["checks"] == 2 and rec["successes"] == 1
    assert rec["consecutive_failures"] == 1
    assert rec["last_ok"].startswith("2026-10-16")
    assert rec["height"] == 720 and rec["last_error"] == "403"


def test_error_summary_strips_noise():
    from kia.probe import _summarize_error

    hls = "[hls @ 0x99cc34000] parse_playlist error Server returned 404 Not Found [https://a.example/x_800.m3u8]"
    assert _summarize_error(hls) == "parse_playlist error Server returned 404 Not Found"
    assert _summarize_error("https://a.example/x.m3u8: Server returned 403 Forbidden") == "Server returned 403 Forbidden"
    assert _summarize_error("") == "ffprobe failed with no output"


def test_error_summary_prefers_root_cause():
    from kia.probe import _summarize_error

    stderr = "\n".join(
        [
            "mime type is not rfc8216 compliant",
            "[https @ 0x1] HTTP error 404 Not Found",
            "[hls @ 0x2] parse_playlist error Server returned 404 Not Found [https://a.example/x.m3u8]",
            "[hls @ 0x2] Empty segment",
        ]
    )
    assert _summarize_error(stderr) == "parse_playlist error Server returned 404 Not Found"


def test_sources_file(tmp_path: Path):
    from kia.config import load, parse_sources

    text = """
# comment
https://iptv-org.github.io/iptv/countries/us.m3u
https://example.com/other/us.m3u            # inline comment
https://iptv-org.github.io/iptv/categories/news.m3u   headlines
# https://disabled.example/x.m3u
https://example.com/live.m3u8#frag
"""
    sources = parse_sources(text)
    assert [s.name for s in sources] == ["us", "us-2", "headlines", "live"]
    assert sources[3].url == "https://example.com/live.m3u8#frag"

    (tmp_path / "kia.toml").write_text('sources_file = "lists/mine.txt"\n')
    (tmp_path / "lists").mkdir()
    (tmp_path / "lists" / "mine.txt").write_text("/tmp/a.m3u8\n")
    assert [s.url for s in load(tmp_path / "kia.toml").sources] == ["/tmp/a.m3u8"]

    other = tmp_path / "other.txt"
    other.write_text("/tmp/b.m3u8 bee\n")
    assert [s.name for s in load(tmp_path / "kia.toml", other).sources] == ["bee"]
    assert [s.name for s in load(tmp_path / "kia.toml", other, ["/tmp/c.m3u"]).sources] == ["c"]


def test_sources_file_rejects_empty_and_duplicate_names(tmp_path: Path):
    import pytest

    from kia.config import load, parse_sources

    with pytest.raises(ValueError, match="duplicate"):
        parse_sources("/a.m3u x\n/b.m3u x\n")
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("# nothing yet\n")
    with pytest.raises(ValueError, match="no playlist sources"):
        load(tmp_path / "kia.toml")


def test_public_url_derives_epg_url(tmp_path: Path, monkeypatch):
    from kia.config import load

    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    cfg = tmp_path / "kia.toml"
    monkeypatch.delenv("KIA_PUBLIC_URL", raising=False)
    cfg.write_text("")
    assert load(cfg).epg_url is None
    cfg.write_text('[output]\npublic_url = "http://tv.lan:8780/"\n')
    assert load(cfg).epg_url == "http://tv.lan:8780/guide.xml.gz"
    monkeypatch.setenv("KIA_PUBLIC_URL", "http://box:9000")
    assert load(cfg).public_url == "http://box:9000"
    cfg.write_text('[output]\nepg_url = "http://elsewhere/g.xml"\n')
    assert load(cfg).epg_url == "http://elsewhere/g.xml"


def test_env_overrides(tmp_path: Path, monkeypatch):
    import pytest

    from kia.config import load

    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    cfg = tmp_path / "kia.toml"
    cfg.write_text("[check]\nconcurrency = 12\n[epg]\ndays = 3\nparallel_sites = 4\n")
    for name in ("KIA_EPG_DAYS", "KIA_PARALLEL_SITES", "KIA_CHECK_CONCURRENCY"):
        monkeypatch.setenv(name, "")  # compose passes unset values as empty strings
    c = load(cfg)
    assert (c.epg.days, c.epg.parallel_sites, c.concurrency) == (3, 4, 12)

    monkeypatch.setenv("KIA_EPG_DAYS", "7")
    monkeypatch.setenv("KIA_PARALLEL_SITES", " 1 ")
    monkeypatch.setenv("KIA_CHECK_CONCURRENCY", "8")
    c = load(cfg)
    assert (c.epg.days, c.epg.parallel_sites, c.concurrency) == (7, 1, 8)

    monkeypatch.setenv("KIA_EPG_DAYS", "0")
    with pytest.raises(ValueError, match="KIA_EPG_DAYS"):
        load(cfg)
    monkeypatch.setenv("KIA_EPG_DAYS", "three")
    with pytest.raises(ValueError, match="whole number"):
        load(cfg)


def test_env_lists_and_placeholders(tmp_path: Path, monkeypatch):
    import pytest

    from kia.config import load

    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    cfg = tmp_path / "kia.toml"
    cfg.write_text('[filters]\nexclude_groups = ["Shop"]\n[epg]\nexclude_sites = ["epg.iptvx.one"]\nplaceholders = true\n')
    for name in ("KIA_EXCLUDE_SITES", "KIA_EXCLUDE_GROUPS", "KIA_PLACEHOLDERS"):
        monkeypatch.setenv(name, "")
    c = load(cfg)
    assert (c.epg.exclude_sites, c.exclude_groups, c.epg.placeholders) == (["epg.iptvx.one"], ["Shop"], True)

    monkeypatch.setenv("KIA_EXCLUDE_SITES", " xumo.tv, ,epg.iptvx.one,tvguide.com ")
    monkeypatch.setenv("KIA_EXCLUDE_GROUPS", "Religious")
    monkeypatch.setenv("KIA_PLACEHOLDERS", "Off")
    c = load(cfg)
    assert c.epg.exclude_sites == ["epg.iptvx.one", "xumo.tv", "tvguide.com"]
    assert c.exclude_groups == ["Shop", "Religious"]
    assert c.epg.placeholders is False

    monkeypatch.setenv("KIA_PLACEHOLDERS", "maybe")
    with pytest.raises(ValueError, match="true or false"):
        load(cfg)


def test_report_names_configured_resolvers():
    from kia.curate import render_markdown

    c = Checked(entry("X", "u1"), ProbeResult(ok=False, error="LOCAL DNS FAILURE: hostname resolves on public DNS"), "dead")
    report = render_markdown([c], [], "now", ("9.9.9.9",))
    assert "backup resolvers (9.9.9.9)" in report and "8.8.8.8" not in report


def test_radio_playlist(tmp_path: Path, monkeypatch):
    from kia import cli
    from kia.config import load, parse_sources

    s = parse_sources("https://x/jazz.m3u jazz radio\nhttps://x/rock.m3u radio\nhttps://x/tv.m3u tv\n")
    assert [(x.name, x.radio) for x in s] == [("jazz", True), ("rock", True), ("tv", False)]
    (tmp_path / "jazz.m3u").write_text("#EXTM3U\n#EXTINF:-1,Cool Jazz\nhttp://a/1\n#EXTINF:-1 group-title=\"Smooth\",Smooth FM\nhttp://a/2\n")
    (tmp_path / "sources.txt").write_text(f"{tmp_path / 'jazz.m3u'} jazz radio\n")
    (tmp_path / "kia.toml").write_text("")
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    found = cli.load_entries(load(tmp_path / "kia.toml"), None)
    assert [(e.name, e.is_radio, e.group) for e in found] == [("Cool Jazz", True, "jazz"), ("Smooth FM", True, "Smooth")]


def test_a_download_that_fails_is_tried_again(monkeypatch):
    import urllib.error

    from kia import cli

    answers = [OSError("timed out"), urllib.error.HTTPError("u", 503, "busy", {}, None), b"#EXTM3U\n"]
    waits = []

    def download(url, guarded, limit, seconds):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(cli.native, "download", download)
    monkeypatch.setattr(cli.time, "sleep", waits.append)
    assert cli.fetch("https://x/list.m3u") == "#EXTM3U\n" and waits == list(cli.FETCH_WAITS)

    answers[:] = [urllib.error.HTTPError("u", 404, "not found", {}, None)]  # not worth trying again
    with pytest.raises(urllib.error.HTTPError) as err:
        cli.fetch("https://x/gone.m3u")
    err.value.close()
    assert waits == list(cli.FETCH_WAITS)


def test_a_source_that_cant_be_downloaded_uses_its_last_copy(tmp_path: Path, monkeypatch):
    import os

    from kia import cli
    from kia.config import load

    (tmp_path / "sources.txt").write_text("https://x/us.m3u us\n")
    (tmp_path / "kia.toml").write_text("")
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    cfg = load(tmp_path / "kia.toml")
    monkeypatch.setattr(cli, "fetch", lambda url, guarded=True: "#EXTM3U\n#EXTINF:-1,News\nhttp://a/1\n")
    assert [e.name for e in cli.load_entries(cfg, None)] == ["News"]

    def down(url, guarded=True):
        raise OSError("GitHub is down")

    monkeypatch.setattr(cli, "fetch", down)
    assert [e.name for e in cli.load_entries(cfg, None)] == ["News"]  # last night's copy
    saved = cfg.epg.work_dir / "sources" / "us.m3u"
    week = time.time() - (cli.SAVED_COPY_DAYS + 1) * 86400
    os.utime(saved, (week, week))
    assert cli.load_entries(cfg, None) == []  # too old: skipped, and the channels_drop alert says so
