from kia import epgmap, playlistguide
from kia.config import EpgConfig
from kia.curate import Checked, select
from kia.m3u import Entry
from kia.probe import ProbeResult


def chk(name, tid, source, url, height=720, elapsed=1.0, status="ok", **kw) -> Checked:
    e = Entry(name, url, {"tvg-id": tid, "tvg-logo": f"{source}.png"}, source=source, **kw)
    return Checked(e, ProbeResult(ok=status == "ok", elapsed=elapsed, height=height), status, height)


ORDER = {"us": 0, "samsung-us": 1, "roku": 2}


def test_same_channel_from_two_playlists_becomes_one():
    a = chk("FOX Weather", "FOXWeather.us@SD", "us", "http://iptv/fox", height=720)
    b = chk("FOX Weather (1080p)", "USBA300011PO", "samsung-us", "https://jmp2.uk/stvp-fox", height=1080,
            vlcopts={"http-user-agent": "Special/1"}, guide_urls=("https://g/samsung.xml.gz",))  # fmt: skip
    (out,) = select([a, b], ORDER, merge_duplicates=True)
    # The earliest playlist's identity, the best stream.
    assert (out.entry.tvg_id, out.entry.attrs["tvg-logo"], out.entry.name) == ("FOXWeather.us@SD", "us.png", "FOX Weather")
    assert (out.entry.url, out.entry.vlcopts, out.height) == ("https://jmp2.uk/stvp-fox", {"http-user-agent": "Special/1"}, 1080)
    assert [e.tvg_id for e in out.also] == ["USBA300011PO"] and b.selected and not a.selected
    # The other copy's playlist guide is used for our id.
    spec = playlistguide.plan([out.entry], {out.entry.tvg_id: [(e.source, e.tvg_id, e.guide_urls) for e in out.also]})
    assert spec == {"playlist-samsung-us": {"urls": ["https://g/samsung.xml.gz"], "channels": {"USBA300011PO": ["FOXWeather.us@SD"]}, "guarded": True}}


def test_ambiguous_names_and_other_countries_stay_apart():
    two_cities = [chk("FOX 5", "WNYW.us@SD", "us", "u1"), chk("FOX 5", "WTTG.us@SD", "us", "u2"),
                  chk("FOX 5", "R1", "roku", "u3")]  # fmt: skip
    assert len(select(two_cities, ORDER, merge_duplicates=True)) == 3
    countries = [chk("Euronews", "Euronews.fr@SD", "us", "u1"), chk("Euronews", "Euronews.uk@SD", "roku", "u2")]
    assert len(select(countries, ORDER, merge_duplicates=True)) == 2
    radio = [chk("KINK", "radio.kink", "kink", "r1"), chk("KINK", "K1", "roku", "r2")]
    radio[0].entry.attrs["radio"] = "true"
    assert len(select(radio, ORDER, merge_duplicates=True)) == 2  # radio never joins TV
    same = [chk("Bravo", "Bravo.us@East", "us", "u1"), chk("Bravo", "B9", "roku", "u2")]
    assert len(select(same, ORDER, merge_duplicates=False)) == 2


def test_map_tries_the_ids_from_other_playlists():
    channels = [epgmap.SiteChannel("i.mjh.nz", "Roku#R7", "en", "R7", "Weather")]
    cands = epgmap.find_candidates(["Weather.us@SD"], channels, EpgConfig(), aliases={"Weather.us@SD": ["R7"]})
    assert [c.channel.site_id for c in cands["Weather.us@SD"]] == ["Roku#R7"]
