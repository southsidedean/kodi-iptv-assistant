"""The outbound guard: a small proxy that keeps playlists from the internet off your network.

kia tests the streams of every playlist it reads, and downloads the guides they name, so a
playlist from the internet could try to send kia to the devices around it (kia.lan). kia
checks each address first, but ffprobe then looks the name up again and follows redirects
and HLS pieces by itself, all out of kia's sight. So the streams and guides of a playlist
that isn't yours go through this guard instead, and every connection is checked here:

- each name is looked up once, here, and the connection goes to the address checked, so
  a DNS server that answers differently the second time gains nothing;
- an address that isn't out on the internet (kia.lan.is_local_address) is refused, as
  is a name with any such address, the way kia.lan.is_local judges them;
- redirects and HLS pieces are new connections, so each one is checked too.

ffprobe may use only plain http (with tcp under it, crypto for encrypted HLS pieces, and
data for inline ones), and sends every request to the guard as one for a whole address
(GET http://...). It may not use https, tls, or httpproxy: ffmpeg's HLS reader opens any
piece whose address starts with "http", httpproxy:// included, and that one connects
wherever it names, past the guard. So an https stream reaches ffprobe as an http address
of the guard's own (for_ffprobe: http://kia-guard.invalid/https/host:port/path), and the
guard opens it itself, with TLS and the server's certificate checked. It rewrites the
addresses in the HLS playlists and redirects it passes back the same way, so a stream's
https pieces work too; one it misses fails, since ffprobe has no way to open it. kia's
own downloads (urllib) send https as tunnels (CONNECT), which the guard opens too. It listens on
127.0.0.1 only, and only for tickets: each test or download gets its own name and secret
in the proxy address, and the guard notes what it refused for that ticket, so the report
can say why a stream failed. Without a ticket, it answers 407, which is also how ffmpeg
finds out it needs to send one.

Your own playlists don't use it: they can name anything on your network. Nor do rtmp and
rtsp streams, which can't go through an HTTP proxy; they get kia.lan's check alone. When
you've set a proxy of your own (http_proxy and the like), kia leaves your proxy in place
and doesn't start the guard (get() returns None).
"""

from __future__ import annotations

import base64
import hmac
import os
import re
import secrets
import select
import socket
import socketserver
import ssl
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from kia import lan

HEAD_LIMIT = 16 * 1024  # bytes for a request's line and headers; ffmpeg's are well under 1 KB
HEAD_TIME = 15  # seconds to send them
CONNECT_TIME = 15  # seconds to connect to the server
IDLE = 60  # seconds a connection may pass nothing before the guard closes it
CONNECTIONS = 64  # at once: kia tests up to `concurrency` streams, each opening a few
# Your own proxy settings: with any of them set, kia uses your proxy and not the guard.
PROXY_VARS = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
# Headers for the guard alone, or for one connection: never passed on to the server. Host
# and Accept-Encoding the guard writes itself (the server's own name; no compression, so a
# playlist can be read and rewritten).
_HOP = {"proxy-authorization", "proxy-connection", "connection", "keep-alive", "te", "trailer", "upgrade",
        "host", "accept-encoding"}  # fmt: skip
INNER = "kia-guard.invalid"  # the host of for_ffprobe's addresses: .invalid is never a real name
PLAYLIST_LIMIT = 4 * 1024 * 1024  # bytes of an HLS playlist the guard rewrites; real ones are a few KB


def for_ffprobe(url: str) -> str:
    """url as ffprobe asks the guard for it: an https address becomes an http one of the
    guard's own, which the guard opens with TLS (see the module's notes); others stay."""
    try:
        p = urlsplit(url)
        host, port = p.hostname, p.port or 443
    except ValueError:
        return url
    if p.scheme.lower() != "https" or not host:
        return url
    login = p.netloc.rpartition("@")[0]
    where = f"[{host}]" if ":" in host else host
    return f"http://{login + '@' if login else ''}{INNER}/https/{where}:{port}{p.path or '/'}{'?' + p.query if p.query else ''}"


@dataclass
class Ticket:
    """One test's or download's pass through the guard: the proxy address to give ffprobe
    or urllib (with its own name and secret), and the hosts the guard refused for it."""

    url: str
    refused: list[str] = field(default_factory=list)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class Guard:
    """The guard, listening on 127.0.0.1 at a port of the system's choosing. resolve,
    is_local, and tls are for tests (a stand-in DNS, which addresses count as local, and the
    certificates trusted)."""

    def __init__(self, resolve: Callable | None = None, is_local: Callable[[str], bool] | None = None,
                 tls: ssl.SSLContext | None = None):  # fmt: skip
        self._resolve = resolve or socket.getaddrinfo
        self._is_local = is_local or lan.is_local_address
        self._tls = tls or ssl.create_default_context()  # the server's certificate and name checked
        self._tls.minimum_version = max(self._tls.minimum_version, ssl.TLSVersion.TLSv1_2)  # nothing older
        self._tickets: dict[str, tuple[str, Ticket]] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(CONNECTIONS)
        guard = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                guard._handle(self.request)

        self._server = _Server(("127.0.0.1", 0), Handler)
        self.port: int = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, name="kia-guard", daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @contextmanager
    def ticket(self) -> Iterator[Ticket]:
        """A ticket for one test or download, good until the with block ends."""
        name, secret = secrets.token_hex(8), secrets.token_urlsafe(24)
        ticket = Ticket(f"http://{name}:{secret}@127.0.0.1:{self.port}")
        with self._lock:
            self._tickets[name] = (secret, ticket)
        try:
            yield ticket
        finally:
            with self._lock:
                self._tickets.pop(name, None)

    def _ticket_for(self, header: str | None) -> Ticket | None:
        scheme, _, value = (header or "").partition(" ")
        if scheme.lower() != "basic":
            return None
        try:
            name, _, secret = base64.b64decode(value.strip(), validate=True).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return None
        with self._lock:
            known = self._tickets.get(name)
        if known is None or not hmac.compare_digest(secret.encode(), known[0].encode()):
            return None
        return known[1]

    # --- one connection from ffprobe or urllib ---------------------------------------

    def _handle(self, client: socket.socket) -> None:
        if not self._slots.acquire(blocking=False):
            _answer(client, 503, "kia's outbound guard is busy")
            return
        try:
            self._serve(client)
        except OSError:
            pass  # either side went away: nothing to answer
        finally:
            self._slots.release()

    def _serve(self, client: socket.socket) -> None:
        client.settimeout(HEAD_TIME)
        head = _read_head(client)
        if head is None:
            _answer(client, 400, "a request kia's outbound guard can't read")
            return
        method, target, headers, early = head
        names = {k.lower(): v for k, v in headers}
        ticket = self._ticket_for(names.get("proxy-authorization"))
        if ticket is None:
            _answer(client, 407, "kia's outbound guard needs a ticket", ["Proxy-Authenticate: Basic realm=\"kia\""])
            return
        if method == "CONNECT":  # https: a tunnel to host:port, which the client's TLS goes through
            host, port = _host_port(target, None)
        elif method in ("GET", "HEAD"):  # the whole address, opened here: http, or https for_ffprobe
            scheme, host, port, path = _target(target)
            if names.get("content-length") or names.get("transfer-encoding"):
                host = ""  # a GET with a body isn't something ffmpeg sends
        else:
            _answer(client, 405, "kia's outbound guard passes on only GET, HEAD, and CONNECT")
            return
        if not host or port is None:
            _answer(client, 400, "an address kia's outbound guard can't read")
            return
        server = self._connect(host, port, ticket)
        if server is None:
            _answer(client, 403, "refused by kia's outbound guard: an address on your own network")
            return
        if server is not False and method != "CONNECT" and scheme == "https":
            try:
                server.settimeout(CONNECT_TIME)
                server = self._tls.wrap_socket(server, server_hostname=host)
            except (OSError, ValueError):
                server.close()
                server = False  # no TLS, or a certificate that isn't the server's
        if server is False:
            _answer(client, 502, "kia's outbound guard couldn't reach the server")
            return
        with server:
            if method == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                if early:  # what the client sent without waiting for the 200
                    server.sendall(early)
                _relay(client, server, both=True)
                return
            where = f"[{host}]" if ":" in host else host
            if port != (443 if scheme == "https" else 80):
                where += f":{port}"
            lines = [f"{method} {path} HTTP/1.1", f"Host: {where}"]
            lines += [f"{k}: {v}" for k, v in headers if k.lower() not in _HOP]
            lines += ["Accept-Encoding: identity", "Connection: close"]  # one request a connection: the next one is checked anew
            server.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
            # Only the answer comes back. Anything else the client sends on this connection
            # never reaches the server, so a second request can't ride along.
            _pass_back(server, client, f"{scheme}://{where}{path}", method)

    def _connect(self, host: str, port: int, ticket: Ticket) -> socket.socket | bool | None:
        """A connection to host, to an address looked up here and checked: None when it's
        refused (noted on the ticket), False when the server can't be reached."""
        try:
            found = self._resolve(host, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            return False  # a name that doesn't look up: the test fails on its own
        addresses = [(family, sockaddr) for family, _, _, _, sockaddr in found]
        # One local address is enough to refuse the name, as kia.lan.is_local does: a
        # connection could go to any of them.
        if not addresses or any(self._is_local(str(sockaddr[0])) for _, sockaddr in addresses):
            with self._lock:
                ticket.refused.append(host)
            return None
        for family, sockaddr in addresses:
            server = socket.socket(family, socket.SOCK_STREAM)
            server.settimeout(CONNECT_TIME)
            try:
                server.connect(sockaddr)  # the address checked, never the name again
            except OSError:
                server.close()
                continue
            return server
        return False


def _read_head(client: socket.socket) -> tuple[str, str, list[tuple[str, str]], bytes] | None:
    """The request's method, target, headers (in the client's own spelling), and whatever
    came after them, from at most HEAD_LIMIT bytes; None for anything else."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = client.recv(4096)
        if not chunk:
            return None
        data += chunk
        if len(data) > HEAD_LIMIT:
            return None
    raw, early = data.split(b"\r\n\r\n", 1)
    head = raw.decode("latin-1")
    first, *rest = head.split("\r\n")
    parts = first.split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        return None
    headers: list[tuple[str, str]] = []
    for line in rest:
        name, sep, value = line.partition(":")
        if not sep or not name.strip() or name != name.strip():
            return None
        headers.append((name, value.strip()))
    return parts[0], parts[1], headers, early


def _host_port(target: str, default: int | None) -> tuple[str, int | None]:
    """host:port from a CONNECT target, with [ ] around an IPv6 address."""
    host, sep, port = target.rpartition(":")
    if not sep:
        host, port = target, ""
    host = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    if not port:
        return host, default
    if not (port.isascii() and port.isdigit()) or not 0 < int(port) < 65536:  # "²" is a digit too
        return host, None
    return host, int(port)


def _target(target: str) -> tuple[str, str, int | None, str]:
    """(scheme, host, port, path) from a GET's whole address: http://host/path, or an https
    one as for_ffprobe writes it. An empty host for anything else."""
    try:
        parts = urlsplit(target)
        name = parts.hostname or ""
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        if parts.scheme != "http" or not name:
            return "", "", None, ""
        if name != INNER:
            return "http", name, parts.port or 80, path
    except ValueError:
        return "", "", None, ""
    where, _, rest = path.removeprefix("/https/").partition("/") if path.startswith("/https/") else ("", "", "")
    host, port = _host_port(where, 443)
    return "https", host, port, "/" + rest


def _to_ffprobe(url: str) -> str:
    """An address from a server, made into one ffprobe can ask the guard for."""
    return for_ffprobe(url) if url.lower().startswith("https:") else url


_URI_ATTR = re.compile(r'(URI=")([^"]*)(")')


def rewrite_playlist(text: str, base: str) -> str:
    """An HLS playlist from base with each address (piece lines, and URI="..." in tags, such
    as keys and other variants) made whole and given to ffprobe as for_ffprobe does. An
    address in any other scheme stays as it is: ffprobe can't open it."""
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("#"):
            line = _URI_ATTR.sub(lambda m: m[1] + _to_ffprobe(urljoin(base, m[2])) + m[3], line)
        elif s:
            line = _to_ffprobe(urljoin(base, s))
        out.append(line)
    return "\n".join(out)


def _pass_back(server: socket.socket, client: socket.socket, url: str, method: str) -> None:
    """The server's answer to url, back to the client. A redirect's address is given as
    for_ffprobe does; an HLS playlist is read whole and its addresses rewritten
    (rewrite_playlist); anything else passes as it comes."""
    server.settimeout(IDLE)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = server.recv(65536)
        if not chunk or len(data) > HEAD_LIMIT * 4:
            return  # no proper answer: the client sees the connection close
        data += chunk
    raw, body = data.split(b"\r\n\r\n", 1)
    status, *lines = raw.decode("latin-1").split("\r\n")
    headers = [(k, v.strip()) for k, _, v in (line.partition(":") for line in lines)]
    names = {k.lower(): v for k, v in headers}
    if "location" in names:
        moved = _to_ffprobe(urljoin(url, names["location"]))
        headers = [(k, moved if k.lower() == "location" else v) for k, v in headers]
    code = status.split(" ")[1] if status.count(" ") else ""
    hls = ("mpegurl" in names.get("content-type", "").lower()
           or urlsplit(url).path.lower().endswith((".m3u8", ".m3u")))  # fmt: skip
    if method == "GET" and code in ("200", "206") and hls and names.get("content-encoding", "identity").lower() == "identity":
        playlist = _whole_body(server, body, names)
        if playlist is None:
            return
        text = rewrite_playlist(playlist.decode("utf-8", "surrogateescape"), url).encode("utf-8", "surrogateescape")
        kept = [(k, v) for k, v in headers if k.lower() not in ("content-length", "transfer-encoding", "content-range", "connection")]
        head = ["HTTP/1.1 200 OK", *(f"{k}: {v}" for k, v in kept), f"Content-Length: {len(text)}", "Connection: close"]
        client.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + text)
        return
    client.sendall(("\r\n".join([status, *(f"{k}: {v}" for k, v in headers)]) + "\r\n\r\n").encode("latin-1") + body)
    _relay(server, client, both=False)


def _whole_body(server: socket.socket, body: bytes, names: dict[str, str]) -> bytes | None:
    """A playlist's whole body, at most PLAYLIST_LIMIT bytes, chunked or not; None if it's
    bigger, or cut off."""
    def more() -> bytes:
        chunk = server.recv(65536)
        if not chunk:
            raise EOFError
        return chunk

    try:
        if "chunked" in names.get("transfer-encoding", "").lower():
            out = b""
            while True:
                while b"\r\n" not in body:
                    body += more()
                size_line, body = body.split(b"\r\n", 1)
                size = int(size_line.split(b";")[0].strip() or b"x", 16)
                if size == 0:
                    return out
                if len(out) + size > PLAYLIST_LIMIT:
                    return None
                while len(body) < size + 2:
                    body += more()
                out, body = out + body[:size], body[size + 2:]
        if (length := names.get("content-length", "")).isdigit():
            if int(length) > PLAYLIST_LIMIT:
                return None
            while len(body) < int(length):
                body += more()
            return body[:int(length)]
        while len(body) <= PLAYLIST_LIMIT:
            try:
                body += more()
            except EOFError:
                return body
        return None
    except (EOFError, ValueError):
        return None


def _relay(source: socket.socket, sink: socket.socket, both: bool) -> None:
    """Copy what source sends to sink (and, with both, the other way) until one side closes
    or nothing passes for IDLE seconds."""
    if not both:  # one way, from a server that may be a TLS socket, which select can't see into
        source.settimeout(IDLE)
        try:
            while data := source.recv(65536):
                sink.sendall(data)
        except TimeoutError:
            pass
        return
    ends = [source, sink]
    for s in (source, sink):
        s.settimeout(None)
    while True:
        ready, _, _ = select.select(ends, [], [], IDLE)
        if not ready:
            return
        for s in ready:
            data = s.recv(65536)
            if not data:
                return
            (sink if s is source else source).sendall(data)


def _answer(client: socket.socket, status: int, reason: str, extra: list[str] | None = None) -> None:
    lines = [f"HTTP/1.1 {status} {reason}", "Content-Length: 0", "Connection: close", *(extra or [])]
    try:
        client.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    except OSError:
        pass


_RUNNING: list[Guard] = []  # this process's guard, once started
_RUNNING_LOCK = threading.Lock()


def get() -> Guard | None:
    """This process's guard, started the first time it's asked for; None when you've set a
    proxy of your own, which kia then leaves in charge."""
    if any(os.environ.get(v) for v in PROXY_VARS):
        return None
    with _RUNNING_LOCK:
        if not _RUNNING:
            _RUNNING.append(Guard())
        return _RUNNING[0]
