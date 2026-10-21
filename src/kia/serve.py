"""A small built-in web server for installs without Docker/nginx, the profile editor's
API, and the Pluto TV relay (with Docker, nginx serves the files and forwards /api/ and
/pluto/ here)."""

from __future__ import annotations

import json
import ssl
import threading
import traceback
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from kia import api as api_module
from kia import tls


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
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def log_message(self, format, *args):
        pass

    # The profile editor's API (kia.api), when the server was given one.
    api = None
    MAX_BODY = 64 * 1024
    # Whether requests count as secure: this server's own TLS, or behind nginx, the
    # X-Kia-Secure header nginx sets (it overwrites whatever the browser sent).
    secure = False
    trust_proxy = False

    def _secure(self) -> bool:
        if self.trust_proxy:
            return self.headers.get("X-Kia-Secure") == "1"
        return self.secure

    def _api(self, method: str) -> bool:
        if not self.api or not self.path.startswith("/api/"):
            return False
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
                status, data = api_module.handle(self.api, method, self.path, self.headers, body, self._secure())
            except Exception:  # never drop the connection without an answer
                traceback.print_exc()
                status, data = 500, {"error": "kia hit an error doing that; its log has the details."}
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        return True

    # Pluto TV channels (kia.pluto), when the server was given the relay.
    relay = None

    def _viewer(self) -> str:
        """Who's asking, to count viewers (behind nginx, the address nginx passes on)."""
        return (self.trust_proxy and self.headers.get("X-Real-IP")) or self.client_address[0]

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

    def do_GET(self):
        if not self._pluto() and not self._api("GET"):
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


def make_server(directory: Path, bind: str, port: int, api=None, tls_dir: Path | None = None,
                trust_proxy: bool = False, relay=None) -> ThreadingHTTPServer:  # fmt: skip
    directory.mkdir(parents=True, exist_ok=True)
    handler = type("Handler", (OutputHandler,),
                   {"api": api, "secure": tls_dir is not None, "trust_proxy": trust_proxy, "relay": relay})  # fmt: skip
    factory = partial(handler, directory=str(directory))
    if tls_dir is not None:
        return TlsServer((bind, port), factory, tls_dir)
    return ThreadingHTTPServer((bind, port), factory)


def serve_in_background(directory: Path, bind: str, port: int, api=None, tls_dir: Path | None = None,
                        trust_proxy: bool = False, relay=None) -> ThreadingHTTPServer:  # fmt: skip
    server = make_server(directory, bind, port, api, tls_dir, trust_proxy, relay)
    threading.Thread(target=server.serve_forever, daemon=True, name="kia-serve").start()
    return server
