import gzip
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia import epgmap, xmltv
from kia.config import EpgConfig
from kia.m3u import Entry

SITES_MD = """<table><tbody>
    <tr><td><a href="sites/good.tv">good.tv</a></td><td align="right">3</td><td align="center">🟢</td><td></td></tr>
    <tr><td><a href="sites/broken.tv">broken.tv</a></td><td align="right">1</td><td align="center">🔴</td><td>x</td></tr>
</tbody></table>"""


def fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "epg"
    files = {
        "good.tv/good.tv.channels.xml": [
            ("good.tv", "1", "CNN.us@East", "CNN"),
            ("good.tv", "2", "Fox.us@SD", "FOX"),
            ("good.tv", "3", "", "Unmapped & Co"),
        ],
        "other.tv/other.tv_us.channels.xml": [
            ("other.tv", "a", "CNN.us@East", "CNN"),
            ("other.tv", "b", "CNN.us@East", "CNN HD"),
            ("other.tv", "c", "Weather.us@West", "Weather"),
        ],
        "broken.tv/broken.tv.channels.xml": [("broken.tv", "z", "CNN.us@East", "CNN")],
    }
    for rel, rows in files.items():
        path = repo / "sites" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(
            f'<channel site="{s}" site_id="{i}" lang="en" xmltv_id="{x}">{n.replace("&", "&amp;")}</channel>'
            for s, i, x, n in rows
        )
        path.write_text(f"<channels>{body}</channels>")
    (repo / "SITES.md").write_text(SITES_MD)
    return repo


def test_candidates_exact_feed_fallback_and_broken_sites(tmp_path: Path):
    repo = fake_repo(tmp_path)
    channels = epgmap.load_site_channels(repo)
    assert len(channels) == 6  # the channel without an xmltv_id is ignored
    broken = epgmap.broken_sites(repo)
    assert broken == {"broken.tv"}

    cfg = EpgConfig(max_sites_per_channel=3)
    cands = epgmap.find_candidates(["CNN.us@East", "Weather.us@SD", "Nope.us@SD"], channels, cfg, broken)
    cnn = cands["CNN.us@East"]
    assert sorted(c.channel.site for c in cnn) == ["good.tv", "other.tv"]  # one per site, broken skipped
    assert all(c.exact for c in cnn)
    assert [(c.channel.site, c.exact) for c in cands["Weather.us@SD"]] == [("other.tv", False)]
    assert "Nope.us@SD" not in cands


def test_prefer_and_limit_sites(tmp_path: Path):
    channels = epgmap.load_site_channels(fake_repo(tmp_path))
    cfg = EpgConfig(max_sites_per_channel=1, prefer_sites=["other.tv"])
    cands = epgmap.find_candidates(["CNN.us@East"], channels, cfg)
    assert [c.channel.site for c in cands["CNN.us@East"]] == ["other.tv"]


def test_channel_files_rewrite_xmltv_id(tmp_path: Path):
    channels = epgmap.load_site_channels(fake_repo(tmp_path))
    cands = epgmap.find_candidates(["Weather.us@SD"], channels, EpgConfig())
    out = tmp_path / "channels"
    assert epgmap.write_channel_files(cands, out) == {"other.tv": 1}
    el = ET.parse(out / "other.tv.channels.xml").getroot().find("channel")
    assert el.get("xmltv_id") == "Weather.us@SD" and el.get("site_id") == "c" and el.text == "Weather"


NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def t(hours: float) -> str:
    return xmltv.format_time(NOW + timedelta(hours=hours))


def write_site_guide(path: Path, programmes: list[tuple[str, float, float, str]]) -> None:
    body = "".join(
        f'<programme start="{t(a)}" stop="{t(b)}" channel="{ch}"><title>{title}</title></programme>'
        for ch, a, b, title in programmes
    )
    path.write_text(f'<?xml version="1.0"?><tv>{body}</tv>')


def test_covered_hours_merges_overlaps_and_clips():
    slots = [(NOW + timedelta(hours=a), NOW + timedelta(hours=b)) for a, b in [(-2, 1), (0.5, 3), (5, 6), (23, 30)]]
    assert xmltv.covered_hours(slots, NOW, NOW + timedelta(hours=24)) == 5.0


def test_choose_and_write_guide(tmp_path: Path):
    a, b = tmp_path / "a.xml", tmp_path / "b.xml"
    write_site_guide(a, [("CNN.us@East", 0, 2, "A news"), ("Fox.us@SD", 0, 24, "Fox day")])
    write_site_guide(b, [("CNN.us@East", h, h + 1, f"B {h}") for h in range(24)])
    slots = {"a": xmltv.scan(a), "b": xmltv.scan(b)}
    order = {"CNN.us@East": ["a", "b"], "Fox.us@SD": ["a"]}
    entries = [
        Entry("CNN (1080p)", "u1", {"tvg-id": "CNN.us@East", "tvg-logo": "http://logo/cnn.png"}),
        Entry("Fox", "u2", {"tvg-id": "Fox.us@SD"}),
        Entry("Town TV [Not 24/7]", "u3", {"tvg-id": "Town.us@SD"}),
    ]
    choices = xmltv.choose_sources([e.tvg_id for e in entries], slots, order, NOW)
    assert choices["CNN.us@East"].site == "b"  # 24h beats 2h despite lower preference
    assert choices["Fox.us@SD"].site == "a"
    assert choices["Town.us@SD"].site is None

    out = tmp_path / "out" / "guide.xml"
    xmltv.write_guide(out, entries, choices, {"a": a, "b": b}, placeholders=True, days=1, now=NOW)
    root = ET.parse(out).getroot()
    chans = {c.get("id"): c for c in root.findall("channel")}
    assert chans["CNN.us@East"].findtext("display-name") == "CNN"
    assert chans["CNN.us@East"].find("icon").get("src") == "http://logo/cnn.png"
    titles = [(p.get("channel"), p.findtext("title")) for p in root.findall("programme")]
    assert ("CNN.us@East", "A news") not in titles
    assert sum(1 for ch, _ in titles if ch == "CNN.us@East") == 24
    assert ("Fox.us@SD", "Fox day") in titles
    assert [ti for ch, ti in titles if ch == "Town.us@SD"] == ["Town TV"] * 12
    with gzip.open(out.with_name("guide.xml.gz")) as f:
        assert f.read() == out.read_bytes()

    report = xmltv.render_report(entries, choices, "now")
    assert "| Full guide (20+ of the next 24 hours) | 2 | 67% |" in report
