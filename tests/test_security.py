"""The fixes from the 0.4.0 code review: each was a way to hurt or fool a kia install."""

import io
import json
import socket
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from kia import api, auth, backup, guard, m3u, pluto, radio, serve
from kia.m3u import Entry


@pytest.fixture
def server(tmp_path: Path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "curated.m3u").write_text("#EXTM3U\n")
    cfg = api.ApiConfig(tmp_path / "out", tmp_path / "state", tmp_path / "profiles.toml", None, auth.Auth(tmp_path / "state", "pw"))
    srv = serve.serve_in_background(tmp_path / "out", "127.0.0.1", 0, api=cfg)
    yield srv.server_address[1], cfg
    srv.shutdown()


def raw(port: int, request: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(request)
        s.shutdown(socket.SHUT_WR)
        return s.recv(4096)


def test_bad_lengths_get_an_answer(server):
    port, _ = server
    for length in (b"-1", b"abc"):
        reply = raw(port, b"POST /api/preview HTTP/1.1\r\nHost: x\r\nContent-Length: " + length + b"\r\n\r\n")
        assert reply.startswith(b"HTTP/1.0 400") or reply.startswith(b"HTTP/1.1 400")


def test_unexpected_errors_get_a_500(server, monkeypatch):
    port, _ = server

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(api, "handle", boom)
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/profiles", timeout=5)
    assert err.value.code == 500 and "error" in json.loads(err.value.read())


def test_pages_cant_be_framed_or_load_from_elsewhere(server):
    port, _ = server
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/curated.m3u", timeout=5) as r:
        assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
        assert "connect-src 'self'" in r.headers["Content-Security-Policy"]
    # nginx sends the same ones, on its own error pages too (always).
    nginx = (Path(__file__).resolve().parent.parent / "deploy" / "kia-common.inc").read_text()
    for name, value in serve.SECURITY_HEADERS.items():
        assert f'add_header {name} "{value}" always;' in nginx, name


def test_third_party_text_cant_add_playlist_lines():
    e = Entry("Bad\nname", "http://a/1\n#EXTINF:-1,Sneaky\nhttp://evil/x.m3u8",
              {"tvg-logo": 'x"\n#EXTINF:-1,Injected\nhttp://evil/ad.m3u8'})  # fmt: skip
    text = m3u.write([e])
    assert len(m3u.parse(text)) == 1
    assert sum(line.startswith("#EXTINF") for line in text.splitlines()) == 1
    assert [line for line in text.splitlines() if not line.startswith("#")] == ["http://a/1 #EXTINF:-1,Sneaky http://evil/x.m3u8"]


def test_radio_logos_must_be_web_addresses():
    station = {"name": "Jazz", "url_resolved": "http://a/s", "favicon": "javascript:alert(1)", "stationuuid": "u"}
    entries = radio.to_entries([station], "radio")
    assert "tvg-logo" not in entries[0].attrs


def crafted(path: Path, name: str, data: bytes = b"x", mode: int = 0o755, listed: bool = True) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for n, body, m in [(backup.MANIFEST, json.dumps({"files": [name] if listed else []}).encode(), 0o644), (name, data, mode)]:
            info = tarfile.TarInfo(n)
            info.size, info.mode = len(body), m
            tar.addfile(info, io.BytesIO(body))
    return path


def test_restore_only_writes_kias_own_files(tmp_path: Path):
    base = tmp_path / "kia"
    base.mkdir()
    restored, _ = backup.restore(crafted(tmp_path / "a.tar.gz", "src/kia/api.py"), base, tmp_path / "safe")
    assert restored == [] and not (base / "src").exists()
    restored, _ = backup.restore(crafted(tmp_path / "b.tar.gz", "state/run.sh"), base, tmp_path / "safe")
    assert restored == ["state/run.sh"] and (base / "state" / "run.sh").stat().st_mode & 0o111 == 0
    restored, _ = backup.restore(crafted(tmp_path / "c.tar.gz", "sources.mine.txt"), base, tmp_path / "safe")
    assert restored == ["sources.mine.txt"]  # your own sources file, named in the backup


def test_backup_stores_what_symlinks_point_at(tmp_path: Path):
    base = tmp_path / "kia"
    (base / "tls").mkdir(parents=True)
    (tmp_path / "real.pem").write_text("CERT")
    (base / "tls" / "fullchain.pem").symlink_to(tmp_path / "real.pem")
    backup.make(base, tmp_path / "b.tar.gz")
    assert (tmp_path / "b.tar.gz").stat().st_mode & 0o077 == 0
    other = tmp_path / "other"
    other.mkdir()
    restored, _ = backup.restore(tmp_path / "b.tar.gz", other, tmp_path / "safe")
    assert restored == ["tls/fullchain.pem"] and (other / "tls" / "fullchain.pem").read_text() == "CERT"


def test_relay_plays_only_kias_channels_and_only_so_many(monkeypatch):
    allowed = {"66ba495ffe11e5000881f049"}
    r = pluto.Relay(None, log=lambda m: None, fetcher=lambda u: (_ for _ in ()).throw(OSError("offline")),
                    allowed=lambda: allowed)  # fmt: skip
    status, _, body = r.handle("/pluto/5f7794162a4559000781fc12.m3u8", "x")
    assert status == 404 and b"isn't in" in body and r.channels == {}
    monkeypatch.setattr(pluto, "MAX_CHANNELS", 1)
    allowed.add("5f7794162a4559000781fc12")
    r.handle("/pluto/66ba495ffe11e5000881f049.m3u8", "x")
    assert r.handle("/pluto/5f7794162a4559000781fc12.m3u8", "x")[0] == 503


def test_relay_fetches_only_https():
    for url in ("file:///etc/passwd", "http://192.168.1.20/admin"):
        with pytest.raises(ValueError):
            pluto.fetch(url)


def test_relay_cache_stays_small(monkeypatch):
    r = pluto.Relay(None, log=lambda m: None, fetcher=lambda u: (u, b"x"), remux_fn=lambda *a: b"TS")
    ch = r.channel("66ba495ffe11e5000881f049")
    from datetime import datetime, timezone

    for n in range(60):
        ch.pieces.append(pluto.Piece(n, datetime.now(timezone.utc), 5.0, f"https://p/{n}.ts", None, None, n, None, None, str(n), n * 5.0))
    for n in reversed(range(60)):  # a viewer walking backwards
        ch.piece(n, "x")
    assert len(ch.cache) <= pluto.CACHE_PIECES


def test_sources_hide_logins_outside_the_secure_page():
    assert api.mask_url("https://user:secret@example.org/list.m3u?token=abc") == "https://•••@example.org/list.m3u?•••"
    assert api.mask_url("https://example.org/list.m3u") == "https://example.org/list.m3u"
    assert api.mask_url("radio-browser: country=US") == "radio-browser: country=US"


def test_encoded_tmp_addresses_are_hidden_too(server):
    port, _ = server
    out = Path(server[1].output_dir)
    (out / "guide.xml.tmp").write_text("half written")
    for path in ("/guide.xml.tmp", "/guide.xml%2etmp", "/guide.xml.%74mp"):
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)
        assert err.value.code == 404


def test_kias_own_key_is_private_without_docker(tmp_path: Path, monkeypatch):
    import shutil as _sh

    from kia import tls

    if not _sh.which("openssl"):
        pytest.skip("needs openssl")
    monkeypatch.delenv("KIA_TLS_SHARED", raising=False)
    tls.make_self_signed(tmp_path / "a", ["kia.lan"])
    assert (tmp_path / "a" / tls.KEY).stat().st_mode & 0o077 == 0
    monkeypatch.setenv("KIA_TLS_SHARED", "true")  # with Docker, nginx reads it, through the group
    tls.make_self_signed(tmp_path / "b", ["kia.lan"])
    assert (tmp_path / "b" / tls.KEY).stat().st_mode & 0o077 == 0o040
    old = tmp_path / "b" / tls.KEY  # a key from before 0.5.2: readable by everyone
    old.chmod(0o644)
    tls.tighten_key(tmp_path / "b", log=lambda m: None)
    assert old.stat().st_mode & 0o777 == 0o640


def test_the_relay_wont_follow_a_redirect_off_https():
    handler = pluto._HttpsOnly()
    req = urllib.request.Request("https://stitcher.pluto.tv/x.m3u8")
    for target in ("http://192.168.1.20/admin", "file:///etc/passwd"):
        with pytest.raises(urllib.error.HTTPError):
            handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, target)
    assert handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://cdn.pluto.tv/y.m3u8") is not None


def test_test_only_tries_stream_addresses(monkeypatch):
    for url in ("file:///etc/passwd", "-i /etc/passwd"):
        monkeypatch.setattr(api, "_entry", lambda cfg, body, u=url: Entry("X", u))
        with pytest.raises(api.ApiError) as err:
            api.test_channel(None, {"key": "x"})
        assert err.value.status == 400


def test_test_keeps_strangers_playlists_off_your_network(tmp_path: Path, monkeypatch):
    # As the nightly check: a channel from a playlist off the internet isn't tested on your network.
    tried = []

    async def fake_probe(url, **kw):
        tried.append(url)
        return type("R", (), {"ok": True, "error": None, "elapsed": 1.0, "height": 720})()

    monkeypatch.setattr("kia.probe.probe", fake_probe)
    cfg = type("C", (), {"user_agent": None, "require_video": False, "ffprobe": "ffprobe"})()
    monkeypatch.setattr(api, "_entry", lambda cfg, body: Entry("Router", "http://192.168.1.20/admin"))
    monkeypatch.setattr(api, "_from_yours", lambda cfg, entry: False)
    with pytest.raises(api.ApiError, match="on your own network"):
        api.test_channel(cfg, {"key": "x"})
    monkeypatch.setattr(api, "_from_yours", lambda cfg, entry: True)  # your own tuner's playlist
    assert api.test_channel(cfg, {"key": "x"})["ok"] and tried == ["http://192.168.1.20/admin"]



def test_test_sends_strangers_streams_through_the_guard(hosts, monkeypatch):
    hosts["tv.example"] = ["8.8.8.8"]  # on the internet
    asked = []

    async def fake_probe(url, proxy=None, **kw):
        asked.append((url, proxy))
        return type("R", (), {"ok": True, "error": None, "elapsed": 1.0, "height": 720})()

    monkeypatch.setattr("kia.probe.probe", fake_probe)
    cfg = type("C", (), {"user_agent": None, "require_video": False, "ffprobe": "ffprobe"})()
    monkeypatch.setattr(api, "_entry", lambda cfg, body: Entry("TV", "https://tv.example/a.m3u8"))
    monkeypatch.setattr(api, "_from_yours", lambda cfg, entry: False)
    api.test_channel(cfg, {"key": "x"})
    url, proxy = asked[-1]
    assert url == "http://kia-guard.invalid/https/tv.example:443/a.m3u8" and proxy.endswith(f"127.0.0.1:{guard.get().port}")
    monkeypatch.setattr(api, "_from_yours", lambda cfg, entry: True)
    api.test_channel(cfg, {"key": "x"})
    assert asked[-1] == ("https://tv.example/a.m3u8", None)  # yours: straight to it


def test_the_nightly_check_sends_strangers_streams_through_the_guard(tmp_path: Path, hosts, monkeypatch):
    import asyncio

    from kia import cli
    from kia.config import load

    hosts["tv.example"] = ["8.8.8.8"]
    asked = {}

    async def fake_probe(url, proxy=None, **kw):
        asked[url] = proxy
        return cli.ProbeResult(ok=True, height=720)

    monkeypatch.setattr(cli, "probe", fake_probe)
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://example.com/us.m3u\n")
    cfg = load(tmp_path / "kia.toml")
    mine, theirs = Entry("Mine", "http://tv.example/mine.m3u8"), Entry("Theirs", "https://tv.example/theirs.m3u8")
    mine.yours = True
    asyncio.run(cli.probe_all([mine, theirs], cfg, verbose=False))
    assert asked["http://tv.example/mine.m3u8"] is None
    assert asked["http://kia-guard.invalid/https/tv.example:443/theirs.m3u8"].endswith(f"127.0.0.1:{guard.get().port}")


def test_guide_downloads_dont_redirect_onto_your_network():
    from kia import native

    handler = native._StayOffYourNetwork()
    req = urllib.request.Request("https://8.8.8.8/guide.xml")
    for target in ("http://192.168.1.20/guide.xml", "file:///etc/passwd", "ftp://8.8.4.4/g.xml"):
        with pytest.raises(urllib.error.URLError):
            handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, target)
    assert handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://8.8.4.4/g.xml") is not None
    # A guide that's on your network already (your own playlist's) may move within it.
    local = urllib.request.Request("http://192.168.1.20/guide.xml")
    assert handler.redirect_request(local, io.BytesIO(), 302, "Found", {}, "http://192.168.1.20/g2.xml") is not None


def test_a_leftover_temp_file_cant_make_the_password_file_public(tmp_path: Path):
    target = tmp_path / "admin.json"
    leftover = tmp_path / "admin.json.tmp"
    leftover.write_text("from a crash")
    leftover.chmod(0o644)
    auth._write_private(target, "{}")
    assert target.stat().st_mode & 0o077 == 0


def test_the_built_in_server_wont_follow_a_link(tmp_path: Path):
    # Without Docker, kia serves output/ itself: like nginx, it serves nothing through a link.
    import threading
    from http.server import ThreadingHTTPServer

    out = tmp_path / "output"
    out.mkdir()
    (out / "curated.m3u").write_text("#EXTM3U\n")
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    (out / "leak.txt").symlink_to(secret)
    (out / "dir").symlink_to(tmp_path)
    handler = lambda *a, **k: serve.OutputHandler(*a, directory=str(out), **k)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        assert urllib.request.urlopen(f"{base}/curated.m3u", timeout=5).read() == b"#EXTM3U\n"
        for path in ("/leak.txt", "/dir/secret.txt"):
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(base + path, timeout=5)
            err.value.close()
            assert err.value.code == 404
    finally:
        httpd.shutdown()
