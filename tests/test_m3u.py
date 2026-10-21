from kia import m3u

SAMPLE = """#EXTM3U
#EXTINF:-1 tvg-id="00sReplay.us@SD" tvg-logo="https://example.com/a.png" group-title="Movies",00s Replay
https://jmp2.uk/plu-62ba60f059624e000781c436.m3u8
#EXTINF:-1 tvg-id="HumorMill.us@SD" http-user-agent="Mozilla/5.0 (Windows)" group-title="Comedy",Humor Mill (1080p)
#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows)
https://example.cloudfront.net/HumorMill/152.m3u8?ads.vf=3I7
#EXTINF:-1 tvg-id="FNX.us@KVCRDT" http-referrer="https://fnx.org/" group-title="Culture",FNX KVCR-DT [Geo-blocked]
#EXTVLCOPT:http-referrer=https://fnx.org/
https://castus.example.com/ch1/video.m3u8
#EXTINF:-1 tvg-id="" group-title="Local;News",Town TV, Channel 3 (720p) [Not 24/7]
#KODIPROP:inputstream=inputstream.adaptive
http://town.example/live.m3u8
"""


def test_parse_attributes_and_names():
    entries = m3u.parse(SAMPLE, source="us")
    assert len(entries) == 4
    first = entries[0]
    assert first.tvg_id == "00sReplay.us@SD"
    assert first.group == "Movies"
    assert first.name == "00s Replay"
    assert first.url == "https://jmp2.uk/plu-62ba60f059624e000781c436.m3u8"
    assert first.source == "us"


def test_headers_from_vlcopt():
    entries = m3u.parse(SAMPLE)
    assert entries[1].user_agent == "Mozilla/5.0 (Windows)"
    assert entries[2].referrer == "https://fnx.org/"
    assert entries[0].user_agent is None


def test_labels_quality_and_commas_in_name():
    town = m3u.parse(SAMPLE)[3]
    assert town.name == "Town TV, Channel 3 (720p) [Not 24/7]"
    assert town.labels == ["Not 24/7"]
    assert town.advertised_height == 720
    assert town.kodiprops == ["inputstream=inputstream.adaptive"]
    assert town.key == "name:towntvchannel3"


def test_roundtrip():
    entries = m3u.parse(SAMPLE)
    again = m3u.parse(m3u.write(entries))
    assert [(e.name, e.url, e.attrs, e.vlcopts, e.kodiprops) for e in again] == [
        (e.name, e.url, e.attrs, e.vlcopts, e.kodiprops) for e in entries
    ]


def test_write_epg_header():
    out = m3u.write([], epg_url="http://server.lan/guide.xml.gz")
    assert out.startswith('#EXTM3U x-tvg-url="http://server.lan/guide.xml.gz"')


def test_bare_url_lines():
    entries = m3u.parse("#EXTM3U\nhttp://a.example/x.m3u8\n")
    assert entries[0].url == "http://a.example/x.m3u8"


def test_byte_order_mark_is_ignored():
    from kia.config import parse_sources

    (entry,) = m3u.parse("\ufeff#EXTM3U\n#EXTINF:-1,A\nhttp://a/1\n")
    assert entry.name == "A"
    assert [s.url for s in parse_sources("\ufeffhttps://x/us.m3u\n")] == ["https://x/us.m3u"]


def test_playlist_carries_the_tested_user_agent():
    from kia.cli import play_as_tested

    own = m3u.Entry("Own", "http://a/1", vlcopts={"http-user-agent": "Custom/1"})
    plain = m3u.Entry("Plain", "https://jmp2.uk/plu-1.m3u8")
    play_as_tested([own, plain], "VLC/3.0.20 LibVLC/3.0.20")
    text = m3u.write([own, plain])
    assert "#EXTVLCOPT:http-user-agent=Custom/1\nhttp://a/1" in text
    assert "#EXTVLCOPT:http-user-agent=VLC/3.0.20 LibVLC/3.0.20\nhttps://jmp2.uk/plu-1.m3u8" in text

    bare = m3u.Entry("Bare", "http://b/1")
    play_as_tested([bare], None)  # user_agent = "" in kia.toml: Kodi keeps its own
    assert "EXTVLCOPT" not in m3u.write([bare])


def test_playlists_in_windows_1252_keep_their_accents(tmp_path):
    from kia.cli import decode_text, fetch

    assert decode_text("Español ✓".encode()) == "Español ✓"
    assert decode_text("AMC en Español, Pokémon".encode("cp1252")) == "AMC en Español, Pokémon"
    f = tmp_path / "list.m3u"
    f.write_bytes('#EXTM3U\n#EXTINF:-1,AMC en Español\nhttp://a/1\n'.encode("cp1252"))
    (entry,) = m3u.parse(fetch(str(f)))
    assert entry.name == "AMC en Español"
