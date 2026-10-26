"""kia.epgshare and native.grab_epgshare01: epgshare01.online's guides, matched by name."""

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kia import cli, epgmap, epgshare, native

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
T = 1_000_000.0
INDEX = ('<a href="epg_ripper_US2.txt">US2</a> <a href="epg_ripper_UK1.txt">UK1</a> <a href="epg_ripper_DISTROTV1.txt">x</a>'
         '<a href="epg_ripper_US_LOCALS1.txt">x</a> <a href="epg_ripper_ALL_SOURCES1.txt">x</a>'
         '<a href="epg_ripper_../../etc.txt">x</a>')  # fmt: skip
LISTS = {
    "US2": "202610261138\n\n\n-- epg_ripper_US2 --\n\nBounce.TV.us2\nAXS.TV.us2\n3ABN.Kids.Network.us2\n<script>\n",
    "DISTROTV1": "-- epg_ripper_DISTROTV1 --\nBusiness.Rockstars.distro\n",
    "UK1": "-- epg_ripper_UK1 --\nBBC.One.uk1\n",
}


def server(calls: list[str]):
    def get(url):
        calls.append(url)
        if url.endswith("/epgshare01/"):
            return INDEX
        feed = url.rpartition("epg_ripper_")[2].removesuffix(".txt")
        return LISTS[feed]

    return get


def test_which_feeds():
    available = ["US2", "UK1", "CA2", "DISTROTV1", "US_LOCALS1", "ALL_SOURCES1", "PLEX1"]
    assert epgshare.feeds_for(available, {"us"}) == {"US2": "us", "DISTROTV1": "us"}
    assert epgshare.feeds_for(available, {"uk", "ca"}) == {"UK1": "uk", "CA2": "ca"}  # US services only with US channels


@pytest.mark.parametrize("cid, name", [("Bounce.TV.us2", "Bounce TV"), ("[PK13].Team.USA.PEACOCK", "Team USA"),
                                       ("Business.Rockstars.distro", "Business Rockstars"), ("plain", "plain"), (".us2", "")])  # fmt: skip
def test_names_from_ids(cid, name):
    assert epgshare.name_of(cid) == name


def test_lists_are_read_once_a_day_for_your_countries(tmp_path: Path):
    calls = []
    lists = epgshare.load_lists(tmp_path, {"us"}, lambda m: None, server(calls), now=T)
    assert lists["US2"] == {"country": "us", "ids": ["Bounce.TV.us2", "AXS.TV.us2", "3ABN.Kids.Network.us2"]}  # no <script>
    assert set(lists) == {"US2", "DISTROTV1"} and len(calls) == 3  # never US_LOCALS1, ALL_SOURCES1, or a made-up name
    assert epgshare.load_lists(tmp_path, {"us"}, lambda m: None, server(calls), now=T + 3600) == lists and len(calls) == 3
    epgshare.load_lists(tmp_path, {"us", "uk"}, lambda m: None, server(calls), now=T + 3600)  # another country: again
    assert any("UK1" in c for c in calls[3:])


def test_last_times_lists_when_epgshare01_cant_be_reached(tmp_path: Path):
    lists = epgshare.load_lists(tmp_path, {"us"}, lambda m: None, server([]), now=T)

    def down(url):
        raise OSError("timed out")

    said = []
    assert epgshare.load_lists(tmp_path, {"us"}, said.append, down, now=T + 2 * 86400) == lists
    assert "using last time's" in said[0]
    (tmp_path / epgshare.CACHE).write_text(json.dumps({"time": T, "countries": ["us"], "lists": {
        "../x": {"country": "us", "ids": ["A.us2"]}, "US2": {"country": "usa", "ids": ["B.us2"]},
        "UK1": {"country": "uk", "ids": ["C.uk1", 5, "has space"]}, "CA2": "nope"}}))  # fmt: skip
    assert epgshare.load_lists(tmp_path, {"us"}, said.append, down, now=T + 2 * 86400) == {"UK1": {"country": "uk", "ids": ["C.uk1"]}}


def test_matched_by_name_in_the_feeds_country_only(tmp_path: Path, monkeypatch):
    repo = tmp_path / "epg"
    path = repo / "sites" / "epgshare01.online" / "epgshare01.online_US1.channels.xml"
    path.parent.mkdir(parents=True)
    # iptv-org's own list, under a feed name epgshare01 no longer has: left out.
    path.write_text('<channels><channel site="epgshare01.online" site_id="US1#Old.us1" xmltv_id="">AXS TV</channel></channels>')
    (repo / ".git").mkdir()
    out = tmp_path / "output"
    out.mkdir()
    (out / "curated.m3u").write_text(
        "#EXTM3U\n"
        '#EXTINF:-1 tvg-id="Bounce.us@SD",Bounce TV (720p)\nhttp://a/1\n'
        '#EXTINF:-1 tvg-id="AXSTV.us@SD",AXS TV\nhttp://a/2\n'
        '#EXTINF:-1 tvg-id="BounceTV.uk@SD",Bounce TV\nhttp://a/3\n'  # a UK channel: not the US feed's
        '#EXTINF:-1 tvg-id="BusinessRockstars.us@SD",Business Rockstars\nhttp://a/4\n')  # fmt: skip
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    cfg = tmp_path / "kia.toml"
    cfg.write_text(f'[epg]\nrepo_dir = "{repo}"\nexclude_sites = []\n')
    monkeypatch.delenv("KIA_EXCLUDE_SITES", raising=False)
    monkeypatch.setattr(epgshare, "_get", server([]))
    assert cli.main(["map", "-c", str(cfg)]) == 0
    mapping = json.loads((tmp_path / "work" / "mapping.json").read_text())
    assert mapping == {"Bounce.us@SD": ["epgshare01.online"], "AXSTV.us@SD": ["epgshare01.online"],
                       "BusinessRockstars.us@SD": ["epgshare01.online"]}  # fmt: skip
    written = (tmp_path / "work" / "channels" / "epgshare01.online.channels.xml").read_text()
    assert 'site_id="US2#Bounce.TV.us2"' in written and 'site_id="DISTROTV1#Business.Rockstars.distro"' in written
    assert "US1#" not in written
    cfg.write_text(f'[epg]\nrepo_dir = "{repo}"\nexclude_sites = ["epgshare01.online"]\n')
    assert cli.main(["map", "-c", str(cfg)]) == 0
    assert json.loads((tmp_path / "work" / "mapping.json").read_text()) == {}


def test_the_guide_from_the_feeds(tmp_path: Path):
    base = tmp_path / "es"
    base.mkdir()
    (base / "epg_ripper_US2.xml.gz").write_bytes(gzip.compress(
        b'<tv><programme start="20261016130000 +0000" stop="20261016140000 +0000" channel="Bounce.TV.us2">'
        b"<title>In the Heat of the Night</title></programme>"
        b'<programme start="20261016130000 +0000" stop="20261016140000 +0000" channel="Other.us2"><title>No</title></programme></tv>'))  # fmt: skip
    chans = tmp_path / "epgshare01.online.channels.xml"
    chans.write_text('<channels><channel site="epgshare01.online" site_id="US2#Bounce.TV.us2" xmltv_id="Bounce.us@SD">Bounce TV</channel>'
                     '<channel site="epgshare01.online" site_id="../x#Evil" xmltv_id="E.us@SD">E</channel></channels>')  # fmt: skip
    out, said = tmp_path / "out.xml", []
    native.grab_epgshare01(chans, out, 1, said.append, base_url=base.as_uri(), now=NOW)
    text = out.read_text()
    assert 'channel="Bounce.us@SD"' in text and "In the Heat of the Night" in text and ">No<" not in text
    assert any("not a feed name" in m for m in said)  # never put into an address


def test_not_in_the_quick_refreshes():
    assert "epgshare01.online" in native.NATIVE_GRABBERS and "epgshare01.online" not in native.QUICK_GRABBERS
    assert epgmap.SiteChannel("s", "i", "", "", "n").country == ""
