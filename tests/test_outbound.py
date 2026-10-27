"""Downloads kia makes for playlists and services that aren't yours: playlists, radio station
lists and .pls files, and the Samsung TV Plus links of Pluto TV channels. They go through
kia's outbound guard (kia.native.opened), so none reaches your network, and they're cut off
at a size and a time."""


import pytest

from kia import cli, native, pluto
from kia.m3u import Entry

from test_deploy import server  # noqa: F401 - a fixture


def test_a_strangers_playlist_cant_be_on_your_network(server, monkeypatch):
    www, base = server
    (www / "us.m3u").write_text("#EXTM3U\n#EXTINF:-1,A\nhttp://a/1\n")
    monkeypatch.setattr(cli, "FETCH_WAITS", ())
    assert cli.fetch(f"{base}/us.m3u", guarded=False).startswith("#EXTM3U")  # yours: anywhere
    with pytest.raises(OSError, match="your own network"):
        cli.fetch(f"{base}/us.m3u")  # a stranger's, here on this computer: the guard refuses it


def test_a_playlist_too_big_is_cut_off(server, monkeypatch):
    www, base = server
    (www / "big.m3u").write_text("#EXTM3U\n" + "#EXTINF:-1,A\nhttp://a/1\n" * 1000)
    monkeypatch.setattr(cli, "FETCH_WAITS", ())
    monkeypatch.setattr(cli, "MAX_PLAYLIST", 1024)
    with pytest.raises(OSError, match="over"):
        cli.fetch(f"{base}/big.m3u", guarded=False)


def test_radio_lists_go_through_the_guard(server):
    www, base = server
    (www / "s.pls").write_text("[playlist]\nFile1=https://ice.example/x\n")
    assert native.download(f"{base}/s.pls", False).startswith(b"[playlist]")
    from kia import radio

    with pytest.raises(OSError, match="your own network"):
        radio._get(f"{base}/s.pls")


def test_samsung_links_are_looked_up_only_when_theyre_jmp2uks(server):
    _, base = server
    looked = []
    entries = [Entry("Pluto TV A", "https://jmp2.uk/stvp-USBA1", {}),
               Entry("Pluto TV B", f"{base}/x?https://jmp2.uk/stvp-USBA2", {}),  # jmp2.uk's name later on: not its link
               Entry("Pluto TV C", "https://evil.example/jmp2.uk/stvp-USBA3", {})]  # fmt: skip
    pluto.partner_channels(entries, lambda m: None, resolve=lambda u: looked.append(u) or u)
    assert looked == ["https://jmp2.uk/stvp-USBA1"]
    with pytest.raises(OSError, match="your own network"):
        pluto.final_url(f"{base}/x")  # through the guard: this computer is refused


def test_the_pluto_relay_cant_reach_your_network():
    with pytest.raises(OSError, match="403"):
        pluto.fetch("https://127.0.0.1:9/x.m3u8")  # https, but here: the guard refuses it
