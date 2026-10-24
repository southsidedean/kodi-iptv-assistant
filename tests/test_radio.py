import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kia import cli, radio, xmltv
from kia.config import Config, Source, parse_sources
from kia.curate import Checked, select
from kia.m3u import Entry, parse, write
from kia.probe import ProbeResult

STATIONS = [
    {
        "stationuuid": "u1",
        "name": "  Jazz  FM ",
        "url": "http://a/jazz",
        "url_resolved": "http://a/jazz.mp3",
        "favicon": "http://a/logo.png",
        "tags": "jazz,smooth jazz",
        "bitrate": 128,
    },
    {"stationuuid": "u2", "name": "Jazz FM", "url": "http://b/jazz-hq", "tags": "", "bitrate": 320, "favicon": ""},
    {"stationuuid": "u3", "name": "Tiny News", "url": "http://c/news", "tags": "news", "bitrate": 32},
    {"stationuuid": "u4", "name": "", "url": "http://d/x"},
]


def test_sources_line_parsing():
    srcs = parse_sources(
        "https://iptv-org.github.io/iptv/countries/us.m3u\n"
        "radio-browser: country=us top=50 tag=jazz\n"
        "radio-browser: country=US   # most popular US stations\n"
        "radio-browser: language=spanish top=20 name=latino\n"
    )
    assert [(s.name, s.is_radio_browser, s.params) for s in srcs] == [
        ("us", False, {}),
        ("radio-us-jazz", True, {"country": "us", "top": "50", "tag": "jazz"}),
        ("radio-us", True, {"country": "US"}),
        ("latino", True, {"language": "spanish", "top": "20"}),
    ]


@pytest.mark.parametrize(
    "line, message",
    [
        ("radio-browser: contry=US", "unknown filter"),
        ("radio-browser: country=US top=lots", "must be a number"),
        ("radio-browser: top=99999", "between 1 and"),
        ("radio-browser: min_bitrate=abc", "min_bitrate='abc' must be a number"),
        ("radio-browser: jazz", "key=value"),
    ],
)
def test_sources_line_errors(line, message):
    with pytest.raises(ValueError, match=message):
        parse_sources(line)


def test_build_query():
    q = radio.build_query({"country": "us", "tags": "jazz,smooth", "top": "25"})
    assert q == {
        "hidebroken": "true",
        "order": "votes",
        "reverse": "true",
        "limit": "25",
        "countrycode": "US",
        "tagList": "jazz,smooth",
    }
    assert radio.build_query({})["limit"] == str(radio.DEFAULT_TOP)


def test_to_entries_and_playlist_output():
    entries = radio.to_entries(STATIONS, "radio-us", min_bitrate=64)
    assert [(e.name, e.url) for e in entries] == [("Jazz FM", "http://a/jazz.mp3"), ("Jazz FM", "http://b/jazz-hq")]
    first = entries[0]
    assert first.is_radio and first.group == "Jazz" and first.attrs["tvg-logo"] == "http://a/logo.png"
    assert entries[1].group == "Radio" and "tvg-logo" not in entries[1].attrs
    assert first.key == entries[1].key == "radio:jazzfm"  # same station, two streams
    text = write(entries)
    assert 'radio="true"' in text
    assert parse(text)[0].is_radio


def test_radio_duplicates_collapse_to_one_station():
    a, b = radio.to_entries(STATIONS[:2], "r")
    chosen = select(
        [Checked(a, ProbeResult(ok=True, elapsed=2.0), "ok"), Checked(b, ProbeResult(ok=True, elapsed=0.5), "ok")]
    )
    assert [c.entry.url for c in chosen] == ["http://b/jazz-hq"]


def test_radio_is_probed_without_requiring_video(monkeypatch):
    seen = {}

    async def fake_probe(url, timeout, user_agent, referrer, require_video, ffprobe):
        seen[url] = require_video
        return ProbeResult(ok=True)

    monkeypatch.setattr(cli, "probe", fake_probe)
    tv = Entry("News", "http://tv", {"tvg-id": "News.us"})
    station = radio.to_entries(STATIONS[:1], "r")[0]
    cfg = Config(sources=[Source("s", "u")])
    asyncio.run(cli.probe_all([tv, station], cfg, verbose=False))
    assert seen == {"http://tv": True, "http://a/jazz.mp3": False}


def test_failed_source_is_skipped(monkeypatch, tmp_path: Path):
    def boom(params, name):
        raise radio.RadioError("radio-browser unreachable")

    monkeypatch.setattr(radio, "load", boom)
    playlist = tmp_path / "tv.m3u"
    playlist.write_text(write([Entry("News", "http://tv", {"tvg-id": "News.us"})]))
    cfg = Config(sources=parse_sources(f"{playlist} tv\nradio-browser: country=US"))
    assert [e.name for e in cli.load_entries(cfg, None)] == ["News"]


def test_guide_skips_radio_placeholders_and_reports_radio_separately(tmp_path: Path):
    now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    tv = Entry("Quiet TV", "u", {"tvg-id": "Quiet.us"})
    station = radio.to_entries(STATIONS[:1], "r")[0]
    entries = [tv, station]
    choices = xmltv.choose_sources([e.tvg_id for e in entries], {}, {}, now)
    out = tmp_path / "guide.xml"
    xmltv.write_guide(out, entries, choices, {}, placeholders=True, days=1, now=now)
    channels = {p.get("channel") for p in ET.parse(out).getroot().findall("programme")}
    assert channels == {"Quiet.us"}
    xmltv.write_guide(out, entries, choices, {}, True, 1, now, radio_placeholders=True)
    assert station.tvg_id in {p.get("channel") for p in ET.parse(out).getroot().findall("programme")}

    report = xmltv.render_report(entries, choices, "now")
    assert "| No guide data (placeholders) | 1 | 100% |" in report
    assert "Plus 1 radio stations" in report


def test_genre_capitalization():
    assert radio.genre("80s,pop") == "80s"
    assert radio.genre("smooth jazz") == "Smooth jazz"
    assert radio.genre(" , ") == "Radio"


def test_streams_are_checked_as_vlc_by_default(tmp_path: Path):
    from kia.config import DEFAULT_USER_AGENT, load

    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    cfg = tmp_path / "kia.toml"
    cfg.write_text("")
    assert load(cfg).user_agent == DEFAULT_USER_AGENT
    cfg.write_text('[check]\nuser_agent = "MyPlayer/1.0"\n')
    assert load(cfg).user_agent == "MyPlayer/1.0"
    cfg.write_text('[check]\nuser_agent = ""\n')  # empty: ffmpeg's own identity
    assert load(cfg).user_agent is None


def test_probes_are_limited_per_server(monkeypatch):
    active: dict[str, int] = {}
    peak: dict[str, int] = {}

    async def fake_probe(url, **kwargs):
        host = url.split("/")[2]
        active[host] = active.get(host, 0) + 1
        peak[host] = max(peak.get(host, 0), active[host])
        await asyncio.sleep(0.01)
        active[host] -= 1
        return ProbeResult(ok=True)

    monkeypatch.setattr(cli, "probe", fake_probe)
    entries = [Entry(f"S{i}", f"http://busy.example/{i}", {"tvg-id": f"S{i}"}) for i in range(8)]
    entries += [Entry(f"T{i}", f"http://other{i}.example/x", {"tvg-id": f"T{i}"}) for i in range(8)]
    cfg = Config(sources=[Source("s", "u")], concurrency=20, per_host=2, host_delay_ms=0)  # the limit alone
    asyncio.run(cli.probe_all(entries, cfg, verbose=False))
    assert peak["busy.example"] == 2
    assert all(peak[f"other{i}.example"] == 1 for i in range(8))


def test_transient_failures_get_a_second_try(monkeypatch):
    calls: dict[str, int] = {}

    async def flaky_probe(url, **kwargs):
        calls[url] = calls.get(url, 0) + 1
        if url.endswith("dns") and calls[url] == 1:
            return ProbeResult(ok=False, error="Failed to resolve hostname x.example: Temporary failure")
        if url.endswith("slow") and calls[url] == 1:
            return ProbeResult(ok=False, error="timed out")
        if url.endswith("gone"):
            return ProbeResult(ok=False, error="Server returned 404 Not Found")
        return ProbeResult(ok=True)

    monkeypatch.setattr(cli, "probe", flaky_probe)
    entries = [Entry(n, f"http://h{n}.example/{n}", {"tvg-id": n}) for n in ("dns", "slow", "gone", "fine")]
    results = asyncio.run(cli.probe_all(entries, Config(sources=[Source("s", "u")]), verbose=False))
    assert {u.rsplit("/", 1)[1]: r.ok for u, r in results.items()} == {
        "dns": True, "slow": True, "gone": False, "fine": True
    }  # fmt: skip
    assert calls == {"http://hdns.example/dns": 2, "http://hslow.example/slow": 2,
                     "http://hgone.example/gone": 1, "http://hfine.example/fine": 1}  # fmt: skip


def test_station_filter_and_name():
    assert radio.build_query({"station": "KINK", "country": "nl", "top": "1"})["name"] == "KINK"
    (src,) = parse_sources("radio-browser: country=NL station=KINK top=1")
    assert src.name == "radio-nl-kink"


def test_local_sources_file(tmp_path: Path, monkeypatch):
    from kia.config import load

    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("/tmp/shipped.m3u\n")
    (tmp_path / "sources.local.txt").write_text("/tmp/mine.m3u\nradio-browser: language=english top=5\n")
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    assert [s.name for s in load(tmp_path / "kia.toml").sources] == ["shipped"]
    monkeypatch.setenv("KIA_SOURCES_FILE", "sources.local.txt")
    assert [s.name for s in load(tmp_path / "kia.toml").sources] == ["mine", "radio-english"]


def test_radio_placeholders_from_env(tmp_path, monkeypatch):
    from kia.config import load

    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    (tmp_path / "kia.toml").write_text("")
    monkeypatch.delenv("KIA_RADIO_PLACEHOLDERS", raising=False)
    assert load(tmp_path / "kia.toml").epg.radio_placeholders is False
    monkeypatch.setenv("KIA_RADIO_PLACEHOLDERS", "true")
    assert load(tmp_path / "kia.toml").epg.radio_placeholders is True
