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
    assert len(channels) == 7 and sum(1 for c in channels if not c.xmltv_id) == 1  # kept for name matching
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


def name_repo(tmp_path: Path) -> list[epgmap.SiteChannel]:
    sc = lambda site, sid, name, xid="": epgmap.SiteChannel(site, sid, "en", xid, name)
    return [
        sc("pluto.tv", "p1", "Battlestar Galactica"),  # no id on the site
        sc("pluto.tv", "p0", "CNN", "CNN.us@East"),  # gives pluto.tv its country: us
        sc("tvpassport.com", "t1", "News 12 Connecticut", "News12Connecticut.us@SD"),
        sc("tvpassport.com", "t2", "Adult Swim - West"),
        sc("tvpassport.com", "t3", "Syfy", "Syfy.us@East"),
        sc("tvpassport.com", "t4", "Fun Roads", "FunRoads.us@SD"),
        sc("mi.tv", "m1", "Fun Roads", "FunRoads.mx@SD"),  # another country
        sc("tvpassport.com", "t5", "Two of Us", "TwoOfUs.us@SD"),
        sc("other.tv", "o1", "Two of Us", "TwoOfUsAgain.us@SD"),  # one name, two channels
        sc("tvpassport.com", "t6", "CBN News Channel"),
    ]


def test_name_matching_is_strict(tmp_path: Path):
    channels = name_repo(tmp_path)
    names = {
        "BattlestarGalactica.us@SD": "Battlestar Galactica",
        "AdultSwim.us@West": "Adult Swim West",
        "News12PlusConnecticut.us@HD": "News12+ Connecticut",  # "+" matters
        "SYFY.us@East": "SYFY",  # id differs only in capitals: an id match
        "FunRoads.us@HD": "Fun Roads",
        "TwoOfUsShow.us@HD": "Two of Us",  # two channels share the name: no match
        "CBNNews.us@SD": "CBN News",  # only a loose match
    }
    cands = epgmap.find_candidates(list(names), channels, EpgConfig(), names=names)
    how = {tid: (c[0].channel.site, c[0].how) for tid, c in cands.items()}
    assert how == {
        "BattlestarGalactica.us@SD": ("pluto.tv", "name"),
        "AdultSwim.us@West": ("tvpassport.com", "name"),
        "SYFY.us@East": ("tvpassport.com", "id"),
        "FunRoads.us@HD": ("tvpassport.com", "id"),  # feed fallback by id wins over a name
    }
    suggestions = epgmap.suggest_by_name(list(names), names, channels, set(cands))
    assert [(s.tvg_id, s.site_name) for s in suggestions] == [("CBNNews.us@SD", "CBN News Channel")]

    off = epgmap.find_candidates(list(names), channels, EpgConfig(ignore_name_matches=["AdultSwim.us@West"]), names=names)
    assert "AdultSwim.us@West" not in off and "BattlestarGalactica.us@SD" in off
    assert set(epgmap.find_candidates(list(names), channels, EpgConfig(name_matching=False), names=names)) == {
        "SYFY.us@East", "FunRoads.us@HD"}  # fmt: skip


def test_report_lists_name_matches_and_suggestions(tmp_path: Path):
    channels = name_repo(tmp_path)
    names = {"BattlestarGalactica.us@SD": "Battlestar Galactica", "CBNNews.us@SD": "CBN News"}
    cands = epgmap.find_candidates(list(names), channels, EpgConfig(), names=names)
    sugg = epgmap.suggest_by_name(list(names), names, channels, set(cands))
    report = epgmap.render_report(list(names.items()), cands, {"pluto.tv": 1}, set(), sugg)
    assert "| Matched by name | 1 |" in report
    assert "| Battlestar Galactica | `BattlestarGalactica.us@SD` | pluto.tv | Battlestar Galactica |" in report
    assert "## Possible matches (not used)" in report and "CBN News Channel" in report


def test_programmes_without_titles_are_not_guide_data(tmp_path: Path):
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    t = lambda h: xmltv.format_time(now + timedelta(hours=h))
    blank = tmp_path / "blank.xml"
    blank.write_text("<tv>" + "".join(f'<programme start="{t(h)}" stop="{t(h + 1)}" channel="A.us"/>' for h in range(24)) + "</tv>")
    titled = tmp_path / "titled.xml"
    titled.write_text(f'<tv><programme start="{t(0)}" stop="{t(2)}" channel="A.us"><title>News</title></programme>'
                      f'<programme start="{t(2)}" stop="{t(3)}" channel="A.us"><title> </title></programme></tv>')  # fmt: skip
    assert xmltv.scan(blank) == {}
    slots = {"blank.tv": xmltv.scan(blank), "titled.tv": xmltv.scan(titled)}
    choice = xmltv.choose_sources(["A.us"], slots, {"A.us": ["blank.tv", "titled.tv"]}, now)["A.us"]
    assert (choice.site, choice.programmes) == ("titled.tv", 1)
    out = tmp_path / "guide.xml"
    xmltv.write_guide(out, [Entry("A", "u", {"tvg-id": "A.us"})], {"A.us": choice}, {"titled.tv": titled}, False, 1, now)
    assert out.read_text().count("<programme") == 1


def test_listings_that_run_out_are_followed_by_the_name(tmp_path: Path):
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    t = lambda h, m=0: xmltv.format_time(now.replace(hour=0) + timedelta(hours=h, minutes=m))
    src = tmp_path / "short.xml"
    # Listings until 21:30 on day one only, like a guide that reaches 11 hours ahead.
    src.write_text("<tv>" + "".join(f'<programme start="{t(h)}" stop="{t(h + 1)}" channel="A.us"><title>P{h}</title></programme>'
                                    for h in range(12, 21)) + f'<programme start="{t(21)}" stop="{t(21, 30)}" channel="A.us"><title>Last</title></programme></tv>')  # fmt: skip
    choices = xmltv.choose_sources(["A.us"], {"short.tv": xmltv.scan(src)}, {"A.us": ["short.tv"]}, now)
    assert choices["A.us"].ends.hour == 21 and choices["A.us"].ends.minute == 30
    out = tmp_path / "guide.xml"
    xmltv.write_guide(out, [Entry("Alpha", "u", {"tvg-id": "A.us"})], choices, {"short.tv": src}, True, 2, now, 4)
    fills = [p for p in ET.parse(out).getroot().findall("programme") if p.get("kia-placeholder")]
    assert fills[0].get("start").startswith("20261019213000") and fills[0].get("stop").startswith("20261020000000")
    assert fills[-1].get("stop").startswith("20261021000000")  # to the end of the 2-day guide
    assert all(p.findtext("title") == "Alpha" for p in fills)

    out2 = tmp_path / "off.xml"
    xmltv.write_guide(out2, [Entry("Alpha", "u", {"tvg-id": "A.us"})], choices, {"short.tv": src}, False, 2, now, 4)
    assert "kia-placeholder" not in out2.read_text()  # placeholders = false: no name blocks at all


def test_accepted_suggestions_are_used(tmp_path: Path, monkeypatch):
    from kia import cli

    repo = tmp_path / "epg"
    path = repo / "sites" / "tvp" / "tvpassport.com.channels.xml"
    path.parent.mkdir(parents=True)
    path.write_text('<channels><channel site="tvpassport.com" site_id="c1" xmltv_id="X.us@SD">X</channel>'
                    '<channel site="tvpassport.com" site_id="c2">CBN News Channel</channel></channels>')  # fmt: skip
    (repo / ".git").mkdir()
    out = tmp_path / "output"
    out.mkdir()
    (out / "curated.m3u").write_text('#EXTM3U\n#EXTINF:-1 tvg-id="CBNNews.us@SD",CBN News\nhttp://a/1\n')
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    cfg = tmp_path / "kia.toml"
    cfg.write_text(f'[epg]\nrepo_dir = "{repo}"\nexclude_sites = []\n')
    monkeypatch.delenv("KIA_EXCLUDE_SITES", raising=False)
    assert cli.main(["map", "-c", str(cfg)]) == 0
    assert "## Possible matches (not used)" in (out / "mapping-report.md").read_text()
    cfg.write_text(f'[epg]\nrepo_dir = "{repo}"\nexclude_sites = []\naccept_name_matches = ["CBNNews.us@SD"]\n')
    assert cli.main(["map", "-c", str(cfg)]) == 0
    report = (out / "mapping-report.md").read_text()
    assert "| CBN News | `CBNNews.us@SD` | tvpassport.com | CBN News Channel |" in report and "Possible matches" not in report
    assert 'site_id="c2"' in (tmp_path / "work" / "channels" / "tvpassport.com.channels.xml").read_text()


def test_a_control_character_in_a_name_cant_break_the_guide(tmp_path: Path):
    from kia import m3u

    text = '#EXTM3U\n#EXTINF:-1 tvg-id="Bad.us" tvg-logo="http://x/\x02.png" group-title="N\x1bews",Bad\x01Name\nhttp://x/1.m3u8\n'
    entries = m3u.parse(text)
    assert entries[0].name == "BadName" and entries[0].group == "News"
    now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    # Even an entry made some other way: what goes into the guide is cleaned on the way out.
    raw = Entry("Raw\x08One", "u", {"tvg-id": "Raw\x0b.us"})
    out = tmp_path / "guide.xml"
    xmltv.write_guide(out, [*entries, raw], xmltv.choose_sources(["Bad.us", "Raw\x0b.us"], {}, {}, now), {},
                      placeholders=True, days=1, now=now)  # fmt: skip
    names = [d.text for d in ET.parse(out).getroot().iter("display-name")]  # parses: the guide is valid XML
    assert names == ["BadName", "RawOne"]


def test_guide_times_with_and_without_an_offset():
    noon_central = datetime(2026, 10, 24, 17, 0, tzinfo=timezone.utc)
    assert xmltv.parse_time("20261024120000 -0500") == noon_central
    assert xmltv.parse_time("20261024120000-0500") == noon_central  # no space before it
    assert xmltv.parse_time("20261024170000") == noon_central  # none: UTC


def test_namespaced_attributes_in_a_sites_channel_list_are_left_out(tmp_path: Path):
    site = tmp_path / "sites" / "odd.tv"
    site.mkdir(parents=True)
    (site / "odd.tv.channels.xml").write_text(
        '<channels xmlns:x="urn:x"><channel site="odd.tv" site_id="1" xml:lang="en" x:extra="1" logo="l.png"'
        ' xmltv_id="A.us">A</channel></channels>'
    )
    (sc,) = epgmap.load_site_channels(tmp_path)
    assert sc.attrs == {"logo": "l.png"}
