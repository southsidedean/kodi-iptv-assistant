"""A small built-in web server for installs without Docker/nginx."""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


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
        # Half-written files from an in-progress run.
        if self.path.split("?", 1)[0].endswith(".tmp"):
            self.send_error(404)
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):  # noqa: A002 - signature from the base class
        pass


def make_server(directory: Path, bind: str, port: int) -> ThreadingHTTPServer:
    directory.mkdir(parents=True, exist_ok=True)
    return ThreadingHTTPServer((bind, port), partial(OutputHandler, directory=str(directory)))


def serve_in_background(directory: Path, bind: str, port: int) -> ThreadingHTTPServer:
    server = make_server(directory, bind, port)
    threading.Thread(target=server.serve_forever, daemon=True, name="kia-serve").start()
    return server
