"""Built-in grabbers for sites that publish ready-made XMLTV files.

iptv-org/epg's i.mjh.nz grabber downloads and fully parses a whole provider guide (up to
100 MB) for every channel and day, which takes ~25 minutes and >2 GB of memory for a few
hundred channels. The files are plain XMLTV, so fetching each once and streaming through
it gives the same programmes in seconds with almost no memory.
"""

from __future__ import annotations

import gzip
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia import __version__
from kia.m3u import xml_attr, xml_text
from kia.xmltv import format_time, parse_time

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
    suffix_fallback: bool = True,
    deadline: float | None = None,
    seen: set[str] | None = None,
) -> int:
    """Copy programmes for wanted source channel ids that are on in [start, end): one that
    started before start and is still on counts, so a refresh just after midnight UTC keeps
    the show on air.

    wanted maps a source channel id to the tvg-ids it should appear under. Returns the
    number of programmes written, and adds the tvg-ids that got any to seen. Past deadline
    (time.monotonic()), raises TimeoutError.
    """
    # Plex ids are "<provider prefix>-<channel id>" and the prefix changes over time, while
    # the channel id doesn't; fall back to matching on the part after the last "-".
    by_suffix = {sid.rpartition("-")[2]: tvg_ids for sid, tvg_ids in wanted.items() if "-" in sid and suffix_fallback}
    written = 0
    events = ET.iterparse(stream, events=("start", "end"))
    _, root = next(events)
    for event, el in events:
        if event != "end":
            continue
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("stopped at the time limit (site_time_limit)")
        if el.tag != "programme":
            if el.tag == "channel":
                root.clear()
            continue
        source = el.get("channel", "")
        targets = wanted.get(source) or ("-" in source and by_suffix.get(source.rpartition("-")[2]))
        if targets:
            try:
                begins = parse_time(el.get("start", ""))
            except ValueError:
                begins = None
            try:
                ends = parse_time(el.get("stop", "")) if el.get("stop") else begins
            except ValueError:
                ends = begins
            if begins and begins < end and (start <= begins or (ends and ends > start)):
                el.tail = None
                for tvg_id in targets:
                    el.set("channel", tvg_id)
                    write("  " + ET.tostring(el, encoding="unicode") + "\n")
                    written += 1
                if seen is not None:
                    seen.update(targets)
        # Each programme is done with: clearing the root lets it go. Clearing only the
        # programme leaves an empty element behind for each, some 24 MB for a big guide.
        root.clear()
    return written


class _StayOffYourNetwork(urllib.request.HTTPRedirectHandler):
    """Redirects only to http or https, and never from an address on the internet to one on
    your network (kia.lan): a guide a stranger's playlist names was checked before the
    download, and a redirect mustn't take it past that check."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from kia import lan

        new = urllib.parse.urlparse(newurl)
        if new.scheme not in ("http", "https"):
            raise urllib.error.URLError(f"redirected to a {new.scheme or 'relative'} address, which kia doesn't follow")
        if lan.is_local(lan.host_of(newurl)) and not lan.is_local(lan.host_of(req.full_url)):
            raise urllib.error.URLError("redirected to an address on your own network, which kia doesn't follow")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_StayOffYourNetwork)


MAX_PAGE = 20 * 1024 * 1024  # bytes: a schedule page or API answer; real ones are a few hundred KB
PAGE_TIME = 120  # seconds for the whole page: a timeout alone limits each read, not the download


def read_page(resp, limit: int = MAX_PAGE, seconds: float = PAGE_TIME) -> bytes:
    """A whole answer, up to limit bytes and seconds: a server that trickles a byte at a time
    or never stops can't hold a download slot, or kia's memory, for long."""
    give_up, parts, size = time.monotonic() + seconds, [], 0
    while chunk := resp.read(1 << 16):
        size += len(chunk)
        if size > limit:
            raise OSError(f"the answer is over {limit // 1024**2} MB")
        if time.monotonic() > give_up:
            raise TimeoutError(f"the answer took over {seconds:.0f} seconds")
        parts.append(chunk)
    return b"".join(parts)


def fetch(url: str, timeout: float = 120):
    req = urllib.request.Request(url, headers={"User-Agent": f"kodi-iptv-assistant/{__version__}"})
    return _OPENER.open(req, timeout=timeout)


def grab_imjh(
    channels_file: Path,
    output: Path,
    days: int,
    log: Callable[[str], None],
    base_url: str = IMJH_BASE,
    now: datetime | None = None,
    previous: Path | None = None,
    deadline: float | None = None,
) -> None:
    """Write an XMLTV guide for every channel in channels_file from i.mjh.nz's published files.

    For the channels of a file that fails tonight, programmes still to come are kept from
    previous (last night's guide), rather than leaving them empty until tomorrow."""
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
    failed: list[str] = []
    with open(output, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
        for tvg_id, name in sorted(names.items()):
            out.write(f"  <channel id={xml_attr(tvg_id)}><display-name>{xml_text(name)}</display-name></channel>\n")
        for path, wanted in sorted(by_path.items()):
            t0 = time.monotonic()
            # Written to a list first, so a file cut off half-way adds nothing.
            got: list[str] = []
            try:
                with fetch(f"{base_url}/{path}.xml.gz") as resp, gzip.GzipFile(fileobj=resp) as stream:
                    n = filter_xmltv(stream, wanted, start, end, got.append, deadline=deadline)
                out.writelines(got)
                log(f"  {path}: {len(wanted)} channels, {n} programmes in {time.monotonic() - t0:.1f}s")
            except (OSError, ET.ParseError, EOFError, zlib.error, http.client.HTTPException) as exc:
                # Cut off (IncompleteRead) or damaged gzip: this file fails, the others still count.
                failed.append(path)
                log(f"  {path}: ERR: {exc}")
        if failed and previous and previous.exists():
            keep = {tid: [tid] for path in failed for tids in by_path[path].values() for tid in tids}
            try:
                with open(previous, "rb") as old:
                    n = filter_xmltv(old, keep, now, end, out.write, suffix_fallback=False)
                log(f"  kept {n} programmes from last time for the channels of {', '.join(failed)}")
            except (OSError, ET.ParseError) as exc:
                log(f"  couldn't read last time's guide: {exc}")
        out.write("</tv>\n")
    if len(failed) == len(by_path):
        raise RuntimeError("every i.mjh.nz file failed to download")


KINK_SCHEDULE_URL = "https://kink.nl/programmering"


def parse_kink_schedule(html: str) -> list[tuple[datetime, datetime, str, str]]:
    """(start, end, show, DJ) for each time slot on kink.nl's schedule page.

    The page is built with Next.js: its data sits in self.__next_f.push([1, "..."]) script
    chunks, which are string literals. Decoded and joined, they hold JSON lists of time
    slots after each "timeslots":{"data": .
    """
    chunks = re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html)
    text = "".join(json.loads(c) for c in chunks)
    decoder = json.JSONDecoder()
    slots: dict[str, tuple[datetime, datetime, str, str]] = {}
    for m in re.finditer(r'"timeslots":\{"data":', text):
        try:
            found, _ = decoder.raw_decode(text, m.end())
        except ValueError:
            continue
        for s in found if isinstance(found, list) else []:
            try:
                show = (s.get("program") or {}).get("data") or {}
                title = (s.get("alt_title") or show.get("title") or "").strip()
                if title and s["start"] not in slots:
                    dj = (s.get("alt_dj") or show.get("alt_title") or "").strip()
                    slots[s["start"]] = (datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]), title, dj)
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
    return sorted(slots.values())


def grab_kink(
    channels_file: Path,
    output: Path,
    days: int,
    log: Callable[[str], None],
    url: str = KINK_SCHEDULE_URL,
    now: datetime | None = None,
) -> None:
    """KINK's program schedule, for each channel in channels_file (the main KINK station)."""
    tvg_ids = [tvg_id for _, tvg_id, _ in read_channel_list(channels_file) if tvg_id]
    with fetch(url, timeout=60) as resp:
        slots = parse_kink_schedule(read_page(resp).decode("utf-8", errors="replace"))
    if not slots:
        raise RuntimeError("no time slots found on the schedule page; kink.nl may have changed")
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    kept = [s for s in slots if s[1] > start and s[0] < end]
    log(f"kink.nl: {len(slots)} time slots, {len(kept)} in the next {days} days, for {len(tvg_ids)} channel(s)")
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
        for tid in tvg_ids:
            out.write(f"  <channel id={xml_attr(tid)}><display-name>KINK</display-name></channel>\n")
        for tid in tvg_ids:
            for a, b, title, dj in kept:
                desc = f"<desc>{xml_text(dj)}</desc>" if dj else ""
                out.write(
                    f"  <programme start={xml_attr(format_time(a))} stop={xml_attr(format_time(b))} "
                    f"channel={xml_attr(tid)}><title>{xml_text(title)}</title>{desc}</programme>\n"
                )
        out.write("</tv>\n")


KEXP_TIMESLOTS = "https://api.kexp.org/v2/timeslots/?format=json&limit=500"
KEXP_ZONE = "America/Los_Angeles"  # KEXP's week is in Seattle time


def kexp_programmes(slots: list[dict], now: datetime, days: int) -> list[tuple[datetime, datetime, str, str]]:
    """(start, stop, show, hosts) for each show in the days ahead, from KEXP's weekly time
    slots: a weekday (1 is Monday), start and end times, and the dates the slot runs."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(KEXP_ZONE)
    today = now.astimezone(zone).date()
    shows = []
    for offset in range(-1, days + 1):  # yesterday too: a show from late last night may still be on
        day = today + timedelta(days=offset)
        for s in slots:
            try:
                if int(s["weekday"]) != day.isoweekday():
                    continue
                if s.get("start_date") and day < datetime.fromisoformat(s["start_date"]).date():
                    continue
                if s.get("end_date") and day > datetime.fromisoformat(s["end_date"]).date():
                    continue
                start = datetime.combine(day, datetime.strptime(s["start_time"], "%H:%M:%S").time(), zone)
                stop = datetime.combine(day, datetime.strptime(s["end_time"], "%H:%M:%S").time(), zone)
                if stop <= start:
                    stop += timedelta(days=1)  # runs past midnight
                title = (s.get("program_name") or "").strip()
            except (KeyError, TypeError, ValueError):
                continue
            hosts = [h for h in (s.get("host_names") or []) if isinstance(h, str) and h.strip()]
            if title:
                shows.append((start, stop, title, " and ".join(hosts)))
    shows.sort()
    # A show runs until the next one starts, if they overlap.
    kept = []
    for i, (start, stop, title, hosts) in enumerate(shows):
        if i + 1 < len(shows) and shows[i + 1][0] < stop:
            stop = shows[i + 1][0]
        if stop > start:
            kept.append((start, stop, title, hosts))
    window_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=days)
    return [s for s in kept if s[1] > window_start and s[0] < window_end]


def grab_kexp(
    channels_file: Path,
    output: Path,
    days: int,
    log: Callable[[str], None],
    url: str = KEXP_TIMESLOTS,
    now: datetime | None = None,
) -> None:
    """KEXP's show schedule, for each channel in channels_file (the station KEXP)."""
    tvg_ids = [tvg_id for _, tvg_id, _ in read_channel_list(channels_file) if tvg_id]
    with fetch(url, timeout=60) as resp:
        slots = json.loads(read_page(resp)).get("results", [])
    shows = kexp_programmes(slots, now or datetime.now(timezone.utc), days)
    if not shows:
        raise RuntimeError("no shows found in KEXP's schedule; api.kexp.org may have changed")
    log(f"kexp.org: {len(slots)} weekly time slots, {len(shows)} shows in the next {days} days, for {len(tvg_ids)} channel(s)")
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
        for tid in tvg_ids:
            out.write(f"  <channel id={xml_attr(tid)}><display-name>KEXP</display-name></channel>\n")
        for tid in tvg_ids:
            for a, b, title, hosts in shows:
                desc = f"<desc>{xml_text('With ' + hosts)}</desc>" if hosts else ""
                out.write(
                    f"  <programme start={xml_attr(format_time(a))} stop={xml_attr(format_time(b))} "
                    f"channel={xml_attr(tid)}><title>{xml_text(title)}</title>{desc}</programme>\n"
                )
        out.write("</tv>\n")


# site name -> grabber(channels_file, output, days, log)
NATIVE_GRABBERS: dict[str, Callable[..., None]] = {"i.mjh.nz": grab_imjh, "kink.nl": grab_kink, "kexp.org": grab_kexp}

# Radio stations with a schedule kia can read itself: station name (see epgmap.name_key)
# -> (site, site_id). Almost no radio station is on iptv-org's guide sites.
RADIO_SCHEDULES: dict[str, tuple[str, str]] = {"kink": ("kink.nl", "kink"), "kexp": ("kexp.org", "kexp")}
