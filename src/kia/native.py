"""Built-in grabbers for sites that publish ready-made XMLTV files.

iptv-org/epg's i.mjh.nz grabber downloads and fully parses a whole provider guide (up to
100 MB) for every channel and day, which takes ~25 minutes and >2 GB of memory for a few
hundred channels. The files are plain XMLTV, so fetching each once and streaming through
it gives the same programmes in seconds with almost no memory.
"""

from __future__ import annotations

import gzip
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from kia import __version__
from kia.xmltv import parse_time

IMJH_BASE = "https://raw.githubusercontent.com/matthuisman/i.mjh.nz/master"


def read_channel_list(path: Path) -> list[tuple[str, str, str]]:
    """(site_id, xmltv_id, name) for each channel in a kia-written channels file."""
    root = ET.parse(path).getroot()
    return [(el.get("site_id", ""), el.get("xmltv_id", ""), (el.text or "").strip()) for el in root.iter("channel")]


def filter_xmltv(
    stream,
    wanted: dict[str, list[str]],
    start: datetime,
    end: datetime,
    write: Callable[[str], None],
) -> int:
    """Copy programmes for wanted source channel ids that start in [start, end).

    wanted maps a source channel id to the tvg-ids it should appear under. Returns the
    number of programmes written.
    """
    # Plex ids are "<provider prefix>-<channel id>" and the prefix changes over time, while
    # the channel id doesn't; fall back to matching on the part after the last "-".
    by_suffix = {sid.rpartition("-")[2]: tvg_ids for sid, tvg_ids in wanted.items() if "-" in sid}
    written = 0
    for _, el in ET.iterparse(stream, events=("end",)):
        if el.tag != "programme":
            if el.tag == "channel":
                el.clear()
            continue
        source = el.get("channel", "")
        targets = wanted.get(source) or ("-" in source and by_suffix.get(source.rpartition("-")[2]))
        if targets:
            try:
                begins = parse_time(el.get("start", ""))
            except ValueError:
                begins = None
            if begins and start <= begins < end:
                el.tail = None
                for tvg_id in targets:
                    el.set("channel", tvg_id)
                    write("  " + ET.tostring(el, encoding="unicode") + "\n")
                    written += 1
        el.clear()
    return written


def fetch(url: str, timeout: float = 120):
    req = urllib.request.Request(url, headers={"User-Agent": f"kodi-iptv-assistant/{__version__}"})
    return urllib.request.urlopen(req, timeout=timeout)


def grab_imjh(
    channels_file: Path,
    output: Path,
    days: int,
    log: Callable[[str], None],
    base_url: str = IMJH_BASE,
    now: datetime | None = None,
) -> None:
    """Write an XMLTV guide for every channel in channels_file from i.mjh.nz's published files."""
    by_path: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    names: dict[str, str] = {}
    for site_id, tvg_id, name in read_channel_list(channels_file):
        path, _, source_id = site_id.partition("#")
        if path and source_id and tvg_id:
            by_path[path][source_id].append(tvg_id)
            names.setdefault(tvg_id, name)

    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    log(f"i.mjh.nz: {len(names)} channels from {len(by_path)} files, {days} days")

    output.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    with open(output, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
        for tvg_id, name in sorted(names.items()):
            out.write(f"  <channel id={quoteattr(tvg_id)}><display-name>{escape(name)}</display-name></channel>\n")
        for path, wanted in sorted(by_path.items()):
            t0 = time.monotonic()
            try:
                with fetch(f"{base_url}/{path}.xml.gz") as resp, gzip.GzipFile(fileobj=resp) as stream:
                    n = filter_xmltv(stream, wanted, start, end, out.write)
                log(f"  {path}: {len(wanted)} channels, {n} programmes in {time.monotonic() - t0:.1f}s")
            except (OSError, ET.ParseError, EOFError) as exc:
                failures += 1
                log(f"  {path}: ERR: {exc}")
        out.write("</tv>\n")
    if failures == len(by_path):
        raise RuntimeError("every i.mjh.nz file failed to download")


# site name -> grabber(channels_file, output, days, log)
NATIVE_GRABBERS: dict[str, Callable[..., None]] = {"i.mjh.nz": grab_imjh}
