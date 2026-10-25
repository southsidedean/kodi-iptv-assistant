"""A small built-in web server for installs without Docker/nginx, the Dashboard's API,
and the Pluto TV relay (with Docker, nginx serves the files and forwards /api/ and
/pluto/ here)."""

from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import threading
import time
import traceback
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from kia import api as api_module
from kia import metrics as metrics_module
from kia import tls



# The web server's addresses on Docker's network (KIA_PROXY_HOST, "web" in compose.yaml),
# looked up again every minute: a recreated container can get a new address.
_proxy = {"at": -1e9, "addresses": frozenset()}
_proxy_lock = threading.Lock()


def proxy_addresses() -> frozenset[str]:
    with _proxy_lock:
        if time.monotonic() - _proxy["at"] > 60:
            host = os.environ.get("KIA_PROXY_HOST", "web")
            try:
                found = {a[4][0] for a in socket.getaddrinfo(host, None)}
            except OSError:  # no such name (not in Docker): nothing to trust
                found = set()
            _proxy.update(at=time.monotonic(), addresses=frozenset(found))
        return _proxy["addresses"]


# The same as nginx's (deploy/kia-common.inc): kia's pages load nothing from elsewhere and
# are never shown inside another site's page.
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

class OutputHandler(SimpleHTTPRequestHandler):
    extensions_map = {  # noqa: RUF012
        **SimpleHTTPRequestHandler.extensions_map,
        ".m3u": "audio/x-mpegurl",
        ".m3u8": "audio/x-mpegurl",
        ".gz": "application/gzip",
        ".xml": "application/xml",
        ".json": "application/json",
        ".md": "text/plain; charset=utf-8",
    }

    def send_head(self):
        # Don't serve files that a run is still writing.
        if urllib.parse.unquote(self.path.split("?", 1)[0]).endswith(".tmp"):
            self.send_error(404)
            return None
        return super().send_head()

    # A client that connects and goes quiet doesn't hold a thread forever.
    timeout = 60

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    def log_message(self, format, *args):
        pass

    # The Dashboard's API (kia.api), when the server was given one.
    api = None
    MAX_BODY = 64 * 1024
    # Whether requests count as secure: this server's own TLS, or behind nginx, the
    # X-Kia-Secure header nginx sets (it overwrites whatever the browser sent). Only nginx
    # itself is believed: other containers on Docker's network (Threadfin, Grafana) can
    # reach kia directly and could send the header too.
    secure = False
    trust_proxy = False

    def _from_proxy(self) -> bool:
        return self.trust_proxy and self.client_address[0] in proxy_addresses()

    def _secure(self) -> bool:
        if self.trust_proxy:
            return self._from_proxy() and self.headers.get("X-Kia-Secure") == "1"
        return self.secure

    def _api(self, method: str) -> bool:
        if not self.api or not self.path.startswith("/api/"):
            return False
        # An API address has no ;parameters, escaped slashes, or dot segments. One that has
        # them was made to reach kia through another of nginx's locations, which don't say
        # whether a request came over HTTPS, so it's refused.
        route = self.path.split("?", 1)[0]
        if ";" in route or "%2f" in route.lower() or "%2e" in route.lower() or "/." in route:
            self._json(400, {"error": "Bad request."})
            return True
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            status, data = 400, {"error": "Bad request."}
        elif length > self.MAX_BODY:
            status, data = 413, {"error": "Too much data."}
        else:
            body = self.rfile.read(length) if length else b""
            try:
                status, data = api_module.handle(self.api, method, self.path, self.headers, body, self._secure(),
                                                 self._viewer())  # fmt: skip
            except Exception:  # never drop the connection without an answer
                traceback.print_exc()
                status, data = 500, {"error": "kia hit an error doing that; its log has the details."}
        self._json(status, data)
        return True

    def _json(self, status: int, data: dict | None) -> None:
        payload = b"" if data is None else json.dumps(data).encode()  # None: no body (MCP's 202)
        self.send_response(status)
        if data is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # Pluto TV channels (kia.pluto), when the server was given the relay.
    relay = None

    def _viewer(self) -> str:
        """Who's asking: to count Pluto viewers, and to slow down wrong passwords per address.
        Behind nginx, the address nginx passes on, which nginx sets itself for /api/,
        /pluto/, and /metrics (deploy/), so a browser can't choose it."""
        return (self._from_proxy() and self.headers.get("X-Real-IP")) or self.client_address[0]

    def _pluto(self) -> bool:
        if not self.relay or not self.path.startswith("/pluto/"):
            return False
        status, kind, body = self.relay.handle(self.path, self._viewer())
        try:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the player moved on
        return True

    # kia's numbers for Prometheus (kia.metrics), when the server was given them.
    metrics = None

    def _metrics(self) -> bool:
        if not self.metrics or self.path.split("?", 1)[0] != "/metrics":
            return False
        try:
            body = self.metrics().encode()
            status = 200
        except Exception:  # never drop the connection without an answer
            traceback.print_exc()
            body, status = b"kia couldn't gather its numbers; its log has the details.\n", 500
        self.send_response(status)
        self.send_header("Content-Type", metrics_module.CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_GET(self):
        if not self._pluto() and not self._metrics() and not self._api("GET"):
            super().do_GET()

    def do_POST(self):
        if not self._api("POST"):
            self.send_error(405)

    def do_PUT(self):
        if not self._api("PUT"):
            self.send_error(405)

    def do_DELETE(self):
        if not self._api("DELETE"):
            self.send_error(405)


class TlsServer(ThreadingHTTPServer):
    """HTTPS with tls/fullchain.pem and tls/privkey.pem, reloaded when they change."""

    def __init__(self, address, handler, tls_dir: Path):
        self.tls_dir = tls_dir
        self._loaded: tuple[float, ssl.SSLContext] | None = None
        self._context()  # fail now, not on the first visitor, if the files are unusable
        super().__init__(address, handler)

    def _context(self) -> ssl.SSLContext:
        cert = self.tls_dir / tls.CERT
        stamp = max(cert.stat().st_mtime, (self.tls_dir / tls.KEY).stat().st_mtime)
        if not self._loaded or self._loaded[0] != stamp:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(cert, self.tls_dir / tls.KEY)
            self._loaded = (stamp, context)
        return self._loaded[1]

    def get_request(self):
        sock, address = super().get_request()
        # The handshake happens in the request's own thread, so a slow client can't stall others.
        return self._context().wrap_socket(sock, server_side=True, do_handshake_on_connect=False), address

    def handle_error(self, request, client_address):
        """A browser that turned down kia's own certificate, http:// sent to this port, or a
        port scan: one line, not a traceback for each."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError)):
            print(f"https: {client_address[0]} didn't finish connecting ({type(exc).__name__})", file=sys.stderr)
            return
        super().handle_error(request, client_address)


def make_server(directory: Path, bind: str, port: int, api=None, tls_dir: Path | None = None,
                trust_proxy: bool = False, relay=None, metrics=None) -> ThreadingHTTPServer:  # fmt: skip
    directory.mkdir(parents=True, exist_ok=True)
    handler = type("Handler", (OutputHandler,),
                   {"api": api, "secure": tls_dir is not None, "trust_proxy": trust_proxy, "relay": relay,
                    "metrics": metrics and staticmethod(metrics)})  # fmt: skip
    factory = partial(handler, directory=str(directory))
    if tls_dir is not None:
        return TlsServer((bind, port), factory, tls_dir)
    return ThreadingHTTPServer((bind, port), factory)


def serve_in_background(directory: Path, bind: str, port: int, api=None, tls_dir: Path | None = None,
                        trust_proxy: bool = False, relay=None, metrics=None) -> ThreadingHTTPServer:  # fmt: skip
    server = make_server(directory, bind, port, api, tls_dir, trust_proxy, relay, metrics)
    threading.Thread(target=server.serve_forever, daemon=True, name="kia-serve").start()
    return server
