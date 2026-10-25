"""Download the guide from another kia server (e.g. a laptop taking it from the home server)."""

from __future__ import annotations

import gzip
import http.client
import time
import zlib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from kia import __version__


class UpstreamError(Exception):
    pass


def download_guide(base_url: str, dest: Path, timeout: float = 30) -> None:
    """Fetch <base_url>/guide.xml.gz into dest, replacing it only if the download is a valid guide."""
    url = f"{base_url.rstrip('/')}/guide.xml.gz"
    req = urllib.request.Request(url, headers={"User-Agent": f"kodi-iptv-assistant/{__version__}"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as f:
            while chunk := resp.read(1 << 16):
                f.write(chunk)
        # Read the whole guide as XML: a connection that drops part way leaves a truncated
        # gzip, which only shows at the end, and a broken guide would replace the good copy,
        # then fail when kia builds this computer's guide from it.
        with gzip.open(tmp, "rb") as g:
            root = _xmltv_root(g)
    except (urllib.error.URLError, OSError, EOFError, zlib.error, http.client.HTTPException, ET.ParseError) as exc:
        tmp.unlink(missing_ok=True)
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        raise UpstreamError(f"{url}: {getattr(exc, 'reason', exc)}") from exc
    if root != "tv":
        tmp.unlink(missing_ok=True)
        raise UpstreamError(f"{url}: not an XMLTV guide")
    tmp.replace(dest)


def _xmltv_root(stream) -> str:
    """Parse the whole guide, a little at a time; returns the outermost tag (tv for XMLTV)."""
    events = ET.iterparse(stream, events=("start", "end"))
    _, root = next(events)
    for event, _el in events:
        if event == "end":
            root.clear()
    return root.tag


def age(path: Path) -> str:
    hours = (time.time() - path.stat().st_mtime) / 3600
    return f"{hours:.0f} hours" if hours < 48 else f"{hours / 24:.0f} days"
