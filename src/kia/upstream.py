"""Download the guide from another kia server (e.g. a laptop taking it from the home server)."""

from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.request
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
        with gzip.open(tmp, "rb") as g:
            head = g.read(512).lstrip()
    except (urllib.error.URLError, OSError, EOFError) as exc:
        tmp.unlink(missing_ok=True)
        raise UpstreamError(f"{url}: {getattr(exc, 'reason', exc)}") from exc
    if not (head.startswith(b"<?xml") or head.startswith(b"<tv")):
        tmp.unlink(missing_ok=True)
        raise UpstreamError(f"{url}: not an XMLTV guide")
    tmp.replace(dest)


def age(path: Path) -> str:
    hours = (time.time() - path.stat().st_mtime) / 3600
    return f"{hours:.0f} hours" if hours < 48 else f"{hours / 24:.0f} days"
