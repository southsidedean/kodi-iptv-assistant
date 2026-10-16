from pathlib import Path

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
