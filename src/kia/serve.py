"""A small built-in web server for installs without Docker/nginx."""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from kia import api as api_module


class OutputHandler(SimpleHTTPRequestHandler):
    extensions_map = {
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
        if self.path.split("?", 1)[0].endswith(".tmp"):
            self.send_error(404)
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):  # noqa: A002 - signature from the base class
        pass

    # The profile editor's API (kia.api), when the server was given one.
    api = None
    MAX_BODY = 64 * 1024

    def _api(self, method: str) -> bool:
        if not self.api or not self.path.startswith("/api/"):
            return False
        length = int(self.headers.get("Content-Length") or 0)
        if length > self.MAX_BODY:
            status, data = 413, {"error": "Too much data."}
        else:
            body = self.rfile.read(length) if length else b""
            status, data = api_module.handle(self.api, method, self.path, self.headers, body)
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        return True

    def do_GET(self):
        if not self._api("GET"):
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


def make_server(directory: Path, bind: str, port: int, api=None) -> ThreadingHTTPServer:
    directory.mkdir(parents=True, exist_ok=True)
    return ThreadingHTTPServer((bind, port), partial(OutputHandler, directory=str(directory)) if api is None
                               else partial(type("ApiHandler", (OutputHandler,), {"api": api}), directory=str(directory)))  # fmt: skip


def serve_in_background(directory: Path, bind: str, port: int, api=None) -> ThreadingHTTPServer:
    server = make_server(directory, bind, port, api)
    threading.Thread(target=server.serve_forever, daemon=True, name="kia-serve").start()
    return server
