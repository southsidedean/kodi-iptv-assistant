"""epgshare01.online's channels, for kia map.

epgshare01 publishes free XMLTV guides, one file a "feed": a country's (US2, UK1, CA2) or a
service's (DISTROTV1, TBNPLUS1). iptv-org/epg lists its channels too, but under feed names
epgshare01 has since changed (US1 is now US2), and mostly without channel ids. So kia reads
epgshare01's own lists instead: each feed has a small text file naming its channel ids, such
as Bounce.TV.us2. kia turns each into a site channel with a name (Bounce TV) and the feed's
country, and kia map matches them by exact name like any other site's (kia.epgmap). The
guide itself comes from kia's own reader (native.grab_epgshare01).

Only the feeds for the countries in your playlists are read, and the lists are kept for a
day: the files change daily, and one person pays for the traffic. Feed names come from
epgshare01's own page and channel ids from its lists, so both are checked before use.
"""

from __future__ import annotations

import http.client
import json
import re
import time
from collections.abc import Callable
from pathlib import Path

from kia.epgmap import SiteChannel
from kia.files import read_object, temp_name
from kia.native import EPGSHARE_BASE, fetch

SITE = "epgshare01.online"
CACHE = "epgshare01-lists.json"  # in work/
KEEP = 20 * 3600  # seconds a night's lists are kept: a little under a day
# A country's feeds are its two-letter code and a number (US2, UK1). Services that carry US
# channels, by feed: their channels count as US ones. Not US_LOCALS1 (58 MB a night, for a
# few local stations, often a station's sub-channels the listing doesn't tell apart), and
# not ALL_SOURCES1 (all of them, 200 MB).
_COUNTRY_FEED = re.compile(r"([A-Z]{2})\d")
US_SERVICES = ("DISTROTV1", "TBNPLUS1", "PEACOCK1", "WHALETVPLUS1", "VOA1", "US_SPORTS1")
_FEED_NAME = re.compile(r"[A-Z0-9_]{2,40}")  # as native.grab_epgshare01 allows them in an address
_FEED_LINK = re.compile(r'href="epg_ripper_([A-Z0-9_]{2,40})\.txt"')
_CHANNEL_ID = re.compile(r"[^\s<>\"&]{1,120}")


def feeds_for(available: list[str], countries: set[str]) -> dict[str, str]:
    """Which of epgshare01's feeds to read for your channels' countries: feed -> country."""
    out = {}
    for feed in available:
        m = _COUNTRY_FEED.fullmatch(feed)
        if m and m.group(1).lower() in countries:
            out[feed] = m.group(1).lower()
        elif feed in US_SERVICES and "us" in countries:
            out[feed] = "us"
    return out


def name_of(channel_id: str) -> str:
    """A channel's name from its id: "Bounce.TV.us2" -> "Bounce TV", "[PK13].Team.USA.PEACOCK"
    -> "Team USA". The part after the last dot is the feed's mark."""
    base = channel_id.rpartition(".")[0] if "." in channel_id else channel_id
    base = re.sub(r"^\[[^\]]*\]\.?", "", base)
    return " ".join(base.replace(".", " ").split())


def site_channels(lists: dict[str, dict]) -> list[SiteChannel]:
    """Site channels from the feeds' lists ({feed: {"country": cc, "ids": [...]}})."""
    out = []
    for feed, info in sorted(lists.items()):
        for cid in info["ids"]:
            name = name_of(cid)
            if name:
                out.append(SiteChannel(SITE, f"{feed}#{cid}", "en", "", name, country=info["country"]))
    return out


def _checked(lists: object) -> dict[str, dict]:
    """Last time's lists, with anything damaged into another shape left out."""
    out = {}
    for feed, info in lists.items() if isinstance(lists, dict) else ():
        if not (isinstance(feed, str) and _FEED_NAME.fullmatch(feed) and isinstance(info, dict)):
            continue
        cc, ids = info.get("country"), info.get("ids")
        if isinstance(cc, str) and re.fullmatch(r"[a-z]{2}", cc) and isinstance(ids, list):
            out[feed] = {"country": cc, "ids": [i for i in ids if isinstance(i, str) and _CHANNEL_ID.fullmatch(i)]}
    return out


def _get(url: str) -> str:
    with fetch(url, timeout=30) as resp:
        return resp.read(5_000_000).decode("utf-8", "replace")


def load_lists(work_dir: Path, countries: set[str], log: Callable[[str], None] = print,
               get: Callable[[str], str] = _get, now: float | None = None) -> dict[str, dict]:  # fmt: skip
    """The channel lists of the feeds for your countries: from last time if less than a day
    old and for the same countries, otherwise downloaded again. When the download fails,
    last time's lists, whatever their age; {} without any."""
    now = time.time() if now is None else now
    path = work_dir / CACHE
    saved = read_object(path)
    lists = _checked(saved.get("lists"))
    fresh = isinstance(saved.get("time"), (int, float)) and now - saved["time"] < KEEP
    if fresh and saved.get("countries") == sorted(countries):
        return lists
    try:
        available = sorted(set(_FEED_LINK.findall(get(f"{EPGSHARE_BASE}/"))))
        wanted = feeds_for(available, countries)
        new = {}
        for feed, cc in wanted.items():
            text = get(f"{EPGSHARE_BASE}/epg_ripper_{feed}.txt")
            ids = [line.strip() for line in text.splitlines()]
            new[feed] = {"country": cc, "ids": [i for i in ids if _CHANNEL_ID.fullmatch(i) and not i.isdigit() and not i.startswith("--")]}
    except (OSError, ValueError, http.client.HTTPException) as exc:  # HTTPException: an answer cut short
        log(f"epgshare01: couldn't read its channel lists ({exc}); using last time's")
        return lists
    work_dir.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(path)
    tmp.write_text(json.dumps({"time": now, "countries": sorted(countries), "lists": new}))
    tmp.replace(path)
    log(f"epgshare01: {sum(len(v['ids']) for v in new.values())} channels in {len(new)} feeds")
    return new
