"""The 0.6.7 review's fixes to Plex, Threadfin, the Pluto TV relay, the stack tiles, and the
Grafana dashboards."""

import io
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kia import dashcheck, pluto, plex, stack


class FakePlex:
    """Plex with one DVR: kia's two Threadfins, under the address they had when they were added."""

    def __init__(self, uris):
        self.deleted = []
        self._dvrs = [{"key": "7", "Device": [{"key": str(i), "uri": u} for i, u in enumerate(uris)]}]

    def dvrs(self):
        return self._dvrs

    def call(self, method, path, params=None):
        assert method == "DELETE", (method, path)  # removing asks Plex for nothing else
        self.deleted.append(path)
        return {}


def test_remove_finds_threadfins_that_are_stopped_or_renamed(tmp_path: Path):
    for folder in ("threadfin", "threadfin-2"):
        (tmp_path / folder / "conf").mkdir(parents=True)
        (tmp_path / folder / "conf" / "settings.json").write_text("{}")
    addresses = plex.kia_addresses(tmp_path, "my-server.lan")
    assert addresses == ["my-server.lan:34400", "my-server.lan:34401"]  # whether they answer or not
    # Added when kia's address was 192.168.1.20: still kia's, by their ports.
    p = FakePlex(["http://192.168.1.20:34400", "http://192.168.1.20:34401"])
    assert plex.remove(p, addresses, lambda m: None) is True and p.deleted == ["/livetv/dvrs/7"]
    # In kia's stack they share a port, and their names tell them apart.
    p = FakePlex(["http://threadfin:34400", "http://threadfin-2:34400"])
    assert plex.remove(p, plex.kia_addresses(tmp_path, None), lambda m: None) is True
    # Someone else's tuner on another port still keeps a DVR from being deleted.
    p = FakePlex(["http://192.168.1.20:34400", "http://192.0.2.9:5004"])
    with pytest.raises(plex.PlexError, match="other tuners"):
        plex.remove(p, addresses, lambda m: None)


def test_the_relay_keeps_only_real_keys_and_bounded_pieces(monkeypatch):
    big = b"x" * (pluto.CACHE_BYTES // 3 + 1)
    r = pluto.Relay(None, log=lambda m: None, fetcher=lambda u: (u, b"k" * 17))
    ch = pluto.Channel("a" * 24, r)
    with pytest.raises(ValueError, match="not 16"):
        ch._key("https://example.com/k")
    assert ch.keys == {}  # not kept
    # Four pieces of a third of the budget each: the oldest goes, though fewer than CACHE_PIECES.
    monkeypatch.setattr(ch, "_remux", lambda p: big)
    ch.pieces = [pluto.Piece(n, datetime.now(timezone.utc), 6.0, f"https://example.com/{n}.ts", None, None, n, None, None,
                             f"https://example.com/{n}.ts", 6.0 * n) for n in range(4)]  # fmt: skip
    for n in range(4):
        ch.piece(n, "tv")
    assert sum(map(len, ch.cache.values())) <= pluto.CACHE_BYTES and 0 not in ch.cache


def test_a_refused_redirect_isnt_asked_again():
    calls = []

    def refuse(url):
        calls.append(url)
        raise pluto.RedirectRefused(url, 302, "redirect to an address that isn't https", {}, io.BytesIO())

    with pytest.raises(pluto.RedirectRefused):
        pluto.fetch_retry("https://example.com/x", refuse)
    assert len(calls) == 1
    handler = pluto._HttpsOnly()
    with pytest.raises(urllib.error.HTTPError):  # still an HTTPError to anything that asks
        handler.redirect_request(urllib.request.Request("https://e/x"), io.BytesIO(), 302, "Found", {}, "http://192.168.1.20/")


def test_a_piece_whose_length_isnt_a_number_is_refused():
    for bad in ("inf", "nan", "-3"):
        with pytest.raises(ValueError, match="length"):
            pluto.parse_media(f"#EXTM3U\n#EXTINF:{bad},\nhttps://e/1.ts\n", "https://e/")


def test_odd_prometheus_answers_dont_stop_the_tiles_or_the_dashboard_check(monkeypatch):
    for body in (b'{"data": {"result": ["x"]}}', b'{"data": {"result": [{"value": [1]}]}}', b"<html>"):
        monkeypatch.setattr(stack.urllib.request, "urlopen", lambda *a, b=body, **k: io.BytesIO(b))
        assert stack.node_exporter_up() is False
    for body in (b"<html>", b'{"data": []}'):  # a proxy's page; data in another shape
        monkeypatch.setattr(dashcheck.urllib.request, "urlopen", lambda *a, b=body, **k: io.BytesIO(b))
        assert dashcheck._ask("http://prometheus:9090", "up", 1) == 0
