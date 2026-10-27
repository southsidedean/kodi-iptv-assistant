"""Guides that playlists provide themselves.

Many playlists name their guide in the header (#EXTM3U x-tvg-url="..."), with channel ids
that match the playlist's tvg-ids: the Samsung TV Plus and Roku lists from apsattv.com,
for example, name i.mjh.nz's guides for those services. kia downloads such a guide, keeps
the programmes for the channels in your list, and offers it as a guide source for those
channels alongside iptv-org's sites.
"""

from __future__ import annotations

import gzip
import http.client
import io
import json
import re
import time
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia import guard
from kia.files import items, obj, read_object, temp_name
from kia.m3u import Entry, xml_attr, xml_text
from kia.native import fetch, filter_xmltv

PREFIX = "playlist-"


def site_name(source: str) -> str:
    """A playlist's guide shows up as a site named after the playlist, e.g. playlist-roku."""
    return PREFIX + (re.sub(r"[^A-Za-z0-9._-]+", "-", source).strip("-.") or "source")


def plan(entries: list[Entry], aliases: dict[str, list[tuple[str, str]]] | None = None) -> dict:
    """{site: {"urls": [...], "channels": {id in the guide: [tvg-ids in our playlist]}, "guarded": bool}}

    `aliases` (our tvg-id -> [(source, its id there)]) lets a channel kept from one playlist
    use another playlist's guide, when duplicates were merged. "guarded": the playlist isn't
    yours, so its guides download through kia's outbound guard (kia.guard). A playlist known
    only through aliases counts as not yours.
    """
    yours = {e.source for e in entries if e.yours}
    spec: dict[str, dict] = {}
    for e in entries:
        if not e.tvg_id:
            continue
        members = [(e.source, e.tvg_id, e.guide_urls)] + [
            (src, sid, urls) for src, sid, urls in (aliases or {}).get(e.tvg_id, [])
        ]
        for source, source_id, urls in members:
            if not urls or not source_id:
                continue
            site = spec.setdefault(site_name(source), {"urls": list(urls), "channels": {}, "guarded": source not in yours})
            site["channels"].setdefault(source_id, [])
            if e.tvg_id not in site["channels"][source_id]:
                site["channels"][source_id].append(e.tvg_id)
    return spec


def save(path: Path, spec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(path)
    tmp.write_text(json.dumps(spec, indent=1, sort_keys=True))
    tmp.replace(path)


def load(path: Path) -> dict:
    """The plan plan() made and save() wrote. A site damaged into another shape is left out;
    "guarded" is kept only as true or false (see download)."""
    spec = {}
    for site, info in read_object(path).items():
        info = obj(info)
        urls = items(info.get("urls"))
        if urls:
            channels = {gid: items(tids) for gid, tids in obj(info.get("channels")).items()}
            spec[site] = {"urls": urls, "channels": channels, **({"guarded": info["guarded"]} if isinstance(info.get("guarded"), bool) else {})}
    return spec


def load_used(path: Path) -> dict[str, list[str]]:
    """playlist-guides-used.json: each site's addresses that had our programmes."""
    return {site: items(urls) for site, urls in read_object(path).items()}


def sites_by_channel(spec: dict) -> dict[str, list[str]]:
    """Our tvg-id -> the playlist guide sites that cover it."""
    out: dict[str, list[str]] = {}
    for site, info in sorted(spec.items()):
        for tvg_ids in info["channels"].values():
            for tid in tvg_ids:
                out.setdefault(tid, []).append(site)
    return out


def _open_guide(url: str, proxy: str | None = None):
    """The guide as a stream of XML, unzipped if it's gzip (by its first bytes, not its name)."""
    resp = fetch(url, proxy=proxy)
    stream = io.BufferedReader(resp)
    if stream.peek(2)[:2] == b"\x1f\x8b":
        return resp, gzip.GzipFile(fileobj=stream)
    return resp, stream


def download(
    info: dict,
    output: Path,
    days: int,
    log: Callable[[str], None],
    now: datetime | None = None,
    deadline: float | None = None,
    previous: Path | None = None,
) -> dict[str, int]:
    """Write the programmes for info["channels"] from info["urls"] to output.

    Returns the number of programmes each address that worked gave. Raises RuntimeError if
    no guide could be read at all. Past deadline (time.monotonic()), the rest count as
    failed. When some fail, channels that got nothing keep the programmes still to come
    from previous (the last good guide), as with i.mjh.nz's files.
    """
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    wanted = info["channels"]
    ours = sorted({tid for tids in wanted.values() for tid in tids})
    log(f"{len(ours)} channels from {len(info['urls'])} guide(s), {days} days")
    counts: dict[str, int] = {}
    failures = 0
    covered: set[str] = set()
    output.parent.mkdir(parents=True, exist_ok=True)
    # A plan without "guarded" (from an older kia) counts as guarded: the safe way round.
    g = guard.get() if info.get("guarded", True) else None
    with (g.ticket() if g else nullcontext()) as ticket, open(output, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
        for tid in ours:
            out.write(f"  <channel id={xml_attr(tid)}><display-name>{xml_text(tid)}</display-name></channel>\n")
        for url in info["urls"]:
            t0 = time.monotonic()
            got: list[str] = []  # a guide cut off half-way adds nothing
            try:
                if deadline is not None and t0 > deadline:
                    raise TimeoutError("not started: past the time limit (site_time_limit)")
                resp, stream = _open_guide(url, ticket.url if ticket else None)
                with resp, stream:
                    found: set[str] = set()
                    n = filter_xmltv(stream, wanted, start, end, got.append, suffix_fallback=False, deadline=deadline,
                                     seen=found)  # fmt: skip
                out.writelines(got)
                covered |= found
                counts[url] = n
                log(f"  {url}: {n} programmes in {time.monotonic() - t0:.1f}s")
            except (OSError, EOFError, ValueError, zlib.error, http.client.HTTPException, ET.ParseError) as exc:
                # One bad guide (gone, not XML, cut off) shouldn't stop the others.
                failures += 1
                if ticket and ticket.refused:
                    log(f"  {url}: ERR: it led to an address on your own network, so kia's outbound guard stopped it")
                    ticket.refused.clear()
                else:
                    log(f"  {url}: ERR: {type(exc).__name__}: {exc}")
        missing = {tid: [tid] for tid in ours if tid not in covered}
        if failures and missing and previous and previous.exists():
            try:
                with open(previous, "rb") as old:
                    n = filter_xmltv(old, missing, now, end, out.write, suffix_fallback=False)
                log(f"  kept {n} programmes from last time for {len(missing)} channels that got none")
            except (OSError, ET.ParseError) as exc:
                log(f"  couldn't read last time's guide: {exc}")
        out.write("</tv>\n")
    if failures == len(info["urls"]):
        raise RuntimeError("no guide could be downloaded")
    return counts
