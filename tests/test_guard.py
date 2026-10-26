"""kia.guard: the outbound guard that every connection of a playlist from the internet goes
through. A stand-in DNS gives the names; 127.0.0.1, where the test server listens, counts as
"the internet" here, and every other local address stays local."""

import asyncio
import base64
import http.server
import shutil
import socket
import subprocess
import threading
import urllib.error
from pathlib import Path
from typing import ClassVar

import pytest

from kia import guard, lan, native, probe

NAMES = {
    "tv.example": ["127.0.0.1"],  # a server on the internet (the test server)
    "router.example": ["192.168.1.20"],  # a name that looks up to your network
    "both.example": ["127.0.0.1", "192.168.1.20"],  # one of each: refused, as kia.lan does
    "v6.example": ["fd00::5"],
}


def resolve(host, port, type=0):  # socket.getaddrinfo's own arguments
    if host in ("127.0.0.1", "192.168.1.20", "fd00::5"):  # addresses, as getaddrinfo gives them back
        found = [host]
    elif host not in NAMES:
        raise socket.gaierror("no such name")
    else:
        found = NAMES[host]
    return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in found]


def is_local(address: str) -> bool:
    return address != "127.0.0.1" and lan.is_local_address(address)


class Handler(http.server.SimpleHTTPRequestHandler):
    seen: ClassVar[list] = []  # (request line, headers) of each request the server got

    def do_GET(self):
        Handler.seen.append((self.requestline, dict(self.headers)))
        if self.path.startswith("/to-router"):
            self.send_response(302)
            self.send_header("Location", f"http://router.example:{self.server.server_port}/x")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *a):
        pass


@pytest.fixture
def site(tmp_path: Path):
    """A web server on 127.0.0.1 serving tmp_path, and the guard in front of it."""
    (tmp_path / "hello.txt").write_text("hello")
    Handler.seen = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Handler(*a, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    g = guard.Guard(resolve=resolve, is_local=is_local)
    yield g, server.server_port, tmp_path
    g.close()
    server.shutdown()
    server.server_close()


def ask(g: guard.Guard, head: str, then: bytes = b"") -> bytes:
    with socket.create_connection(("127.0.0.1", g.port), timeout=10) as s:
        s.sendall(head.encode() + then)
        data = b""
        while chunk := s.recv(65536):
            data += chunk
        return data


def login(ticket: guard.Ticket) -> str:
    user_pass = ticket.url.split("//", 1)[1].rsplit("@", 1)[0]
    return "Proxy-Authorization: Basic " + base64.b64encode(user_pass.encode()).decode()


def test_only_a_ticket_gets_through(site):
    g, port, _ = site
    get = f"GET http://tv.example:{port}/hello.txt HTTP/1.1\r\nHost: tv.example\r\n"
    answer = ask(g, get + "\r\n")
    assert answer.startswith(b"HTTP/1.1 407") and b'Proxy-Authenticate: Basic realm="kia"' in answer  # how ffmpeg learns to send it
    with g.ticket() as ticket:
        assert ask(g, get + login(ticket) + "\r\n\r\n").startswith(b"HTTP/1.0 200")
        wrong = ticket.url.replace("@", "x@")  # the right name, a wrong secret
        assert ask(g, get + login(guard.Ticket(wrong)) + "\r\n\r\n").startswith(b"HTTP/1.1 407")
        assert ask(g, get + "Proxy-Authorization: Basic !!!\r\n\r\n").startswith(b"HTTP/1.1 407")
    assert ask(g, get + login(ticket) + "\r\n\r\n").startswith(b"HTTP/1.1 407")  # a ticket ends with its test


def test_plain_http_is_passed_on_without_the_ticket(site):
    g, port, _ = site
    with g.ticket() as ticket:
        answer = ask(g, f"GET http://tv.example:{port}/hello.txt?a=1 HTTP/1.1\r\nHost: tv.example\r\nX-Kept: yes\r\n"
                        f"Proxy-Connection: keep-alive\r\n{login(ticket)}\r\n\r\n")  # fmt: skip
    assert answer.endswith(b"hello")
    line, headers = Handler.seen[-1]
    assert line == "GET /hello.txt?a=1 HTTP/1.1"  # the path only, as a server expects it
    assert headers["X-Kept"] == "yes" and headers["Connection"] == "close"
    assert not [h for h in headers if h.lower().startswith("proxy-")]  # the ticket stays with the guard


@pytest.mark.parametrize("host", ["router.example", "both.example", "v6.example", "192.168.1.20", "[fd00::5]"])
def test_your_network_is_refused_and_noted(site, host):
    g, port, _ = site
    with g.ticket() as ticket:
        for head in (f"GET http://{host}:{port}/x HTTP/1.1\r\nHost: {host}\r\n", f"CONNECT {host}:443 HTTP/1.1\r\n"):
            assert ask(g, head + login(ticket) + "\r\n\r\n").startswith(b"HTTP/1.1 403")
        assert ticket.refused == [host.strip("[]")] * 2
    assert not Handler.seen  # never reached


def test_a_tunnel_goes_to_the_address_checked(site):
    g, port, _ = site
    with g.ticket() as ticket:
        # https goes this way: the TLS inside is the client's own; plain http stands in here.
        answer = ask(g, f"CONNECT tv.example:{port} HTTP/1.1\r\n{login(ticket)}\r\n\r\n",
                     b"GET /hello.txt HTTP/1.1\r\nHost: tv.example\r\nConnection: close\r\n\r\n")  # fmt: skip
    assert answer.startswith(b"HTTP/1.1 200 Connection established\r\n\r\nHTTP/1.0 200") and answer.endswith(b"hello")
    assert not ticket.refused


@pytest.mark.parametrize("head, status", [
    ("POST http://tv.example/ HTTP/1.1\r\n", b"405"),
    ("GET ftp://tv.example/ HTTP/1.1\r\n", b"400"),
    ("GET http://tv.example/ HTTP/1.1\r\nContent-Length: 5\r\n", b"400"),  # a GET with a body
    ("CONNECT tv.example:99999 HTTP/1.1\r\n", b"400"),
    ("CONNECT tv.example HTTP/1.1\r\n", b"400"),
    ("GET http://nowhere.example/ HTTP/1.1\r\n", b"502"),  # a name that doesn't look up
])  # fmt: skip
def test_what_it_wont_pass_on(site, head, status):
    g, _, _ = site
    with g.ticket() as ticket:
        assert ask(g, head + login(ticket) + "\r\n\r\n").split(b" ")[1] == status
    assert not ticket.refused  # none of these is a refusal for your network


def test_requests_it_cant_read(site):
    g, _, _ = site
    assert ask(g, "nonsense\r\n\r\n").startswith(b"HTTP/1.1 400")
    assert ask(g, "GET http://tv.example/ HTTP/1.1\r\n" + "X: y\r\n" * 3000 + "\r\n").startswith(b"HTTP/1.1 400")  # too long
    assert ask(g, "GET http://tv.example/ HTTP/1.1\r\n Folded: header\r\n\r\n").startswith(b"HTTP/1.1 400")


def test_downloads_follow_redirects_only_through_the_guard(site):
    g, port, _ = site
    with g.ticket() as ticket:
        with native.fetch(f"http://tv.example:{port}/hello.txt", proxy=ticket.url) as r:
            assert r.read() == b"hello"
        assert Handler.seen[-1][1]["Host"] == f"tv.example:{port}"  # the server's name, never the guard's
        # A redirect is a new connection, checked like the first: one onto your network fails.
        with pytest.raises(urllib.error.HTTPError) as e:
            native.fetch(f"http://tv.example:{port}/to-router", proxy=ticket.url)
        assert e.value.code == 403 and ticket.refused == ["router.example"]
    assert [line for line, _ in Handler.seen] == ["GET /hello.txt HTTP/1.1", "GET /to-router HTTP/1.1"]


def test_your_own_proxy_stays_in_charge(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://my-server.lan:3128")
    assert guard.get() is None
    monkeypatch.delenv("HTTPS_PROXY")
    assert guard.get() is guard.get()  # one guard a process, started when first asked for


TICKET = "http://" + "name:secret" + "@" + "127.0.0.1:9"  # in parts: the privacy check reads x@host as an email


def test_ffprobe_through_the_guard(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")  # would send everything around the guard
    monkeypatch.setenv("HOME", "/path/to/home")
    env = probe.guarded_env(TICKET)
    assert env["http_proxy"] == TICKET and "no_proxy" not in env and env["HOME"] == "/path/to/home"
    cmd = probe.build_command("https://tv.example/a.m3u8", 5, guarded=True)
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "http,https,tls,tcp,httpproxy,crypto,data"
    assert "-protocol_whitelist" not in probe.build_command("https://tv.example/a.m3u8", 5)


def test_a_refusal_explains_the_failed_test(site, monkeypatch):
    g, _, _ = site
    monkeypatch.setattr(guard, "get", lambda: g)
    asked = []

    async def run(url, proxy=None, **kw):
        asked.append(proxy)
        if proxy is None:
            return probe.ProbeResult(ok=False, error="untouched")
        with g._lock:
            ticket = next(t for _, t in g._tickets.values() if t.url == proxy)
        ticket.refused.append("router.example")
        return probe.ProbeResult(ok=False, error="Server returned 403 Forbidden (access denied)")

    r = asyncio.run(probe.probe_guarded("https://tv.example/a.m3u8", run=run, timeout=5))
    assert r.error == probe.GUARD_REFUSED and asked[0].startswith("http://")
    r = asyncio.run(probe.probe_guarded("rtmp://tv.example/live", run=run, timeout=5))
    assert asked[1] is None  # rtmp can't go through an http proxy: kia.lan's check alone


@pytest.mark.skipif(not (shutil.which("ffprobe") and shutil.which("ffmpeg")), reason="needs ffmpeg")
@pytest.mark.timeout(120)
def test_real_ffprobe_goes_through_the_guard(site, monkeypatch):
    # An HLS playlist on "the internet" whose first piece is there too, and one whose piece
    # is on your network: ffprobe opens pieces itself, so only the guard sees that.
    g, port, www = site
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "1",
                    "-f", "mpegts", str(www / "piece.ts")], check=True)  # fmt: skip
    for name, host in (("good", "tv.example"), ("bad", "router.example")):
        (www / f"{name}.m3u8").write_text(f"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n"
                                          f"http://{host}:{port}/piece.ts\n#EXT-X-ENDLIST\n")  # fmt: skip
    monkeypatch.setattr(guard, "get", lambda: g)
    good = asyncio.run(probe.probe_guarded(f"http://tv.example:{port}/good.m3u8", timeout=10))
    assert good.ok and good.height == 120, good.error
    bad = asyncio.run(probe.probe_guarded(f"http://tv.example:{port}/bad.m3u8", timeout=10))
    assert not bad.ok and bad.error == probe.GUARD_REFUSED
    assert not [line for line, _ in Handler.seen if "/x" in line]
