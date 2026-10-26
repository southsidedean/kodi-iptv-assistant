"""Finding shows in the guides: kia's own (output/guide.xml), and the Live TV guides of the Plex
and Jellyfin that kia knows (KIA_PLEX_URL and KIA_PLEX_TOKEN, KIA_JELLYFIN_URL and
KIA_JELLYFIN_TOKEN). For "when is the game on, and on which channel?" from kia's MCP server
(kia_find_shows, kia_whats_on) and `kia find`.

It only reads. Everything it returns is guide text that strangers wrote (titles, channel
names), so each piece is cut short, put on one line, and has any login in an address hidden
(doctor._quote), like kia doctor's answers.
"""

from __future__ import annotations

import os
import re
import urllib.parse
# Only for pieces of kia's own guide.xml, which kia wrote; expat expands no entities from outside.
import xml.etree.ElementTree as ET  # nosec B405
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia import m3u
from kia.config import Config
from kia.doctor import _file_name, _quote, list_of
from kia.xmltv import PLACEHOLDER_ATTR

MAX_RESULTS = 25  # shows in one answer, soonest first
MAX_HOURS = 168  # how far ahead a search can look: the week a guide holds at most
JELLYFIN_SEARCH = 500  # Jellyfin's matches asked for; it includes past ones, which kia leaves out
# Plex's own guides (for an antenna tuner): kia's channels in Plex use kia's guide, which kia
# searches itself, so Plex's XMLTV guides are skipped. The name goes into a path, so it's checked.
PLEX_OWN_GUIDE = re.compile(r"tv\.plex\.providers\.epg\.cloud:\d+")


@dataclass
class Show:
    start: datetime
    end: datetime
    title: str
    channel: str
    number: str = ""
    episode: str = ""
    where: str = "kia"  # kia's playlists, Plex, or Jellyfin: where to watch it
    in_title: bool = True  # the words are in its title or episode, not only its description

    def to_dict(self) -> dict:
        out = {"start": _local(self.start), "end": _local(self.end), "title": self.title, "channel": self.channel,
               "number": self.number, "where": self.where}  # fmt: skip
        if self.episode:
            out["episode"] = self.episode
        return out


def _local(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="minutes")


def _words(text: str) -> list[str]:
    return [w for w in text.lower().split() if w]


def _has(text: str, words: list[str]) -> bool:
    """Every word in text as a whole word ("NFL" isn't the "nfl" in "influence"), any case."""
    text = text.lower()
    return all(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text) for w in words)


def search(cfg: Config, query: str = "", category: str = "", hours: float = 24, now: datetime | None = None,
           env: dict | None = None, plex=None, jellyfin=None) -> dict:  # fmt: skip
    """Shows whose title, episode, or description has every word of query, and whose category
    has category's words, airing between now and hours from now (0: on now), from every guide
    kia knows, soonest first. plex and jellyfin stand in for their clients in tests."""
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    hours = max(0.0, min(float(hours), MAX_HOURS))
    words, kinds = _words(query)[:10], _words(category)[:5]
    until = now + timedelta(hours=hours)
    notes: list[str] = []
    found = _kia(cfg, words, kinds, now, until)
    found += _from_plex(env, plex, words, kinds, now, until, notes)
    found += _from_jellyfin(env, jellyfin, words, kinds, now, until, notes, found, cfg.output_dir)
    # Shows named for the words first ("Bears" finds the games before a show whose description
    # mentions bears), then the soonest.
    found.sort(key=lambda s: (not s.in_title, s.start, s.where != "kia", s.channel))
    out = {"from": _local(now), "until": _local(until), "shows": [s.to_dict() for s in found[:MAX_RESULTS]]}
    if len(found) > MAX_RESULTS:
        notes.append(f"{len(found)} shows match; these are the first {MAX_RESULTS}. More words, or fewer hours, narrow it down.")
    if not found:
        notes.append("Nothing in the guides matches. Guide titles are often short, so try fewer words.")
    if notes:
        out["notes"] = notes
    return out


def _airing(start: datetime, end: datetime, now: datetime, until: datetime) -> bool:
    """On at some point between now and until (with until == now: on now)."""
    return start <= until and end > now


# kia's own guide


def _xmltv_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text.strip(), "%Y%m%d%H%M%S %z")
    except (ValueError, AttributeError):
        return None


# A programme in kia's guide.xml, found without parsing the whole file: the scan picks the few
# that could match (in the time window, with every word in them), and only those are parsed.
_PROGRAMME = re.compile(rb"<programme\b([^>]*)>(.*?)</programme>", re.S)
_ATTR = re.compile(rb'(\w[\w-]*)="([^"]*)"')


def _kia(cfg: Config, words: list[str], kinds: list[str], now: datetime, until: datetime) -> list[Show]:
    """Matches in output/guide.xml, which kia wrote (tens of megabytes on a big list)."""
    path = cfg.output_dir / "guide.xml"
    try:
        data = path.read_bytes()
    except OSError:
        return []
    channels = _kia_channels(cfg.output_dir / "curated.m3u")
    needles = [w.encode() for w in words + kinds]
    found, seen = [], set()
    for m in _PROGRAMME.finditer(data):
        if needles and not all(n in m.group(2).lower() for n in needles):
            continue
        attrs = dict(_ATTR.findall(m.group(1)))
        if PLACEHOLDER_ATTR.encode() in attrs:
            continue  # kia's own stand-in for a channel without listings: its name, not a show
        start, end = (_xmltv_time(attrs.get(k, b"").decode(errors="replace")) for k in (b"start", b"stop"))
        if not (start and end and _airing(start, end, now, until)):
            continue
        try:
            el = ET.fromstring(m.group(0))  # nosec B314
        except ET.ParseError:
            continue
        title, episode = el.findtext("title") or "", el.findtext("sub-title") or ""
        cats = " ".join(c.text or "" for c in el.findall("category"))
        if not (_has(" ".join([title, episode, el.findtext("desc") or ""]), words) and _has(cats, kinds)):
            continue
        name, number = channels.get(el.get("channel", ""), (el.get("channel", ""), ""))
        key = (el.get("channel"), start, title)
        if key not in seen:  # a channel in two of your playlists has its listings twice
            seen.add(key)
            found.append(Show(start, end, _quote(title, 120), _quote(name, 80), _quote(number, 10), _quote(episode, 120),
                              in_title=_has(f"{title} {episode}", words)))  # fmt: skip
    return found


def _kia_channels(playlist: Path) -> dict[str, tuple[str, str]]:
    """tvg-id -> (name, number) from kia's playlist."""
    if not playlist.is_file():
        return {}
    return {e.tvg_id: (e.name, e.attrs.get("tvg-chno", "")) for e in m3u.parse(playlist.read_text(encoding="utf-8", errors="replace"))
            if e.tvg_id}  # fmt: skip


# Plex's own guides (an antenna, say)


def _plex_client(env: dict, plex):
    if plex is not None:
        return plex
    url, token = env.get("KIA_PLEX_URL", ""), env.get("KIA_PLEX_TOKEN", "")
    if not url or not token:
        return None
    from kia.plex import Plex

    return Plex(url, token, timeout=5, startup_wait=0)


def _plex_guides(client) -> list[str]:
    return [d["epgIdentifier"] for d in list_of(client.dvrs(), dict) if PLEX_OWN_GUIDE.fullmatch(str(d.get("epgIdentifier")))]


def _container(answer: object, key: str) -> list[dict]:
    """The dicts under MediaContainer's key in a Plex answer; [] for anything shaped otherwise."""
    box = answer.get("MediaContainer") if isinstance(answer, dict) else None
    return list_of(box.get(key), dict) if isinstance(box, dict) else []


def _from_plex(env: dict, plex, words: list[str], kinds: list[str], now: datetime, until: datetime, notes: list[str]) -> list[Show]:
    from kia.plex import PlexError

    client = _plex_client(env, plex)
    if client is None or not words:
        return []  # Plex searches by words; it can't list a category's shows
    if kinds:
        notes.append("Plex's guide search doesn't say shows' categories, so Plex's shows are left out when you ask for one.")
        return []
    found = []
    try:
        for guide in _plex_guides(client):
            answer = client.call("GET", f"/{guide}/hubs/search", {"query": " ".join(words), "limit": 50})
            for hub in _container(answer, "Hub"):
                for item in list_of(hub.get("Metadata"), dict):
                    found += _plex_airings(item, words, now, until)
    except PlexError as exc:
        notes.append(f"Plex didn't answer: {_quote(exc)}")
    return found


def _plex_airings(item: dict, words: list[str], now: datetime, until: datetime) -> list[Show]:
    """Each airing of one of Plex's matches (an episode can air more than once)."""
    show, title = str(item.get("grandparentTitle") or ""), str(item.get("title") or "")
    if not _has(" ".join([show, title, str(item.get("summary") or "")]), words):
        return []  # Plex's search also matches loosely; kia keeps what has every word
    out = []
    for media in list_of(item.get("Media"), dict):
        if not isinstance(media.get("beginsAt"), int) or not isinstance(media.get("endsAt"), int):
            continue
        start, end = (datetime.fromtimestamp(media[k], timezone.utc) for k in ("beginsAt", "endsAt"))
        if _airing(start, end, now, until):
            channel = media.get("channelTitle") or media.get("channelCallSign") or "?"
            out.append(Show(start, end, _quote(show or title, 120), _quote(channel, 80), _quote(media.get("channelVcn") or "", 10),
                            _quote(title if show and title != show else "", 120), "Plex", _has(f"{show} {title}", words)))  # fmt: skip
    return out


def plex_on_now(env: dict, query: str, now: datetime | None = None, plex=None) -> list[dict]:
    """What's on now on Plex's own channels (an antenna's) whose name, call sign, or number has
    query's words, with what's next: for kia_whats_on. [] without Plex or its own guide."""
    from kia.plex import PlexError

    client = _plex_client(env, plex)
    words = _words(query)
    if client is None or not words:
        return []
    now = now or datetime.now(timezone.utc)
    ts = int(now.timestamp())
    channels: dict[str, dict] = {}
    try:
        for guide in _plex_guides(client):
            # Plex's grid takes beginsAt<= and endsAt>= as written; the next 3 hours give "next".
            answer = client.call("GET", f"/{guide}/grid?type=1,4&beginsAt<={ts + 3 * 3600}&endsAt>={ts}")
            for item in _container(answer, "Metadata"):
                for media in list_of(item.get("Media"), dict):
                    if not isinstance(media.get("beginsAt"), int):
                        continue
                    name = str(media.get("channelTitle") or media.get("channelCallSign") or "")
                    if not _has(" ".join([name, str(media.get("channelCallSign") or ""), str(media.get("channelVcn") or "")]), words):
                        continue
                    slot = {"start": _local(datetime.fromtimestamp(media["beginsAt"], timezone.utc)),
                            "end": _local(datetime.fromtimestamp(media.get("endsAt") or media["beginsAt"], timezone.utc)),
                            "title": _quote(item.get("grandparentTitle") or item.get("title") or "", 120), "_at": media["beginsAt"]}  # fmt: skip
                    channels.setdefault(name, {"channel": _quote(name, 80), "number": _quote(media.get("channelVcn") or "", 10),
                                               "where": "Plex", "slots": []})["slots"].append(slot)  # fmt: skip
    except PlexError:
        return []
    out = []
    for ch in list(channels.values())[:5]:
        slots = sorted(ch.pop("slots"), key=lambda s: s["_at"])
        for s in slots:
            s.pop("_at")
        ch["on_now"], ch["next"] = (slots[0] if slots else None), slots[1:4]
        out.append(ch)
    return out


# Jellyfin's guide


def _from_jellyfin(env: dict, jellyfin, words: list[str], kinds: list[str], now: datetime, until: datetime, notes: list[str],
                   already: list[Show], output_dir: Path) -> list[Show]:  # fmt: skip
    """Jellyfin's matches, when it has a guide of its own (Schedules Direct, another XMLTV file).
    Most read only kia's guide, which kia searches itself: then Jellyfin's copy, which can be a
    day old and holds kia's stand-ins for channels without listings, would only repeat it. A
    show kia already found on a channel of the same name at the same time isn't listed twice."""
    from kia import jellyfin as jellyfin_module

    if not words:
        return []  # Jellyfin searches by words; it can't list a category's shows
    client = jellyfin
    if client is None:
        url, token = env.get("KIA_JELLYFIN_URL", ""), env.get("KIA_JELLYFIN_TOKEN", "")
        if not url or not token:
            return []
        client = jellyfin_module.Jellyfin(url, token, timeout=5)
    seen = {(s.title.lower(), s.start, _plain(s.channel)) for s in already}
    try:
        live_tv = client.call("/System/Configuration/livetv")
        guides = [_file_name(g.get("Path")) for g in list_of(live_tv.get("ListingProviders") if isinstance(live_tv, dict) else None, dict)]
        if not guides or all(name and (output_dir / name).is_file() for name in guides):
            return []  # only kia's own guides: already searched
        users = client.call("/Users")
        user = users[0].get("Id") if isinstance(users, list) and users and isinstance(users[0], dict) else None
        if not user:
            return []  # Jellyfin searches its guide only for a user
        # Asked for as LiveTvProgram (Jellyfin's name for the kind); they come back with Type "Program".
        params = {"searchTerm": " ".join(words), "includeItemTypes": "LiveTvProgram", "recursive": "true", "limit": JELLYFIN_SEARCH,
                  "userId": user, "fields": "ChannelInfo,Genres", "sortBy": "StartDate"}  # fmt: skip
        answer = client.call("/Items?" + urllib.parse.urlencode(params))
    except jellyfin_module.JellyfinError as exc:
        notes.append(f"Jellyfin didn't answer: {_quote(exc)}")
        return []
    found = []
    for item in list_of(answer.get("Items") if isinstance(answer, dict) else None, dict):
        start, end = (_jellyfin_time(item.get(k)) for k in ("StartDate", "EndDate"))
        genres = " ".join(g for g in item.get("Genres") or [] if isinstance(g, str))
        if not (start and end and _airing(start, end, now, until) and _has(genres, kinds)):
            continue
        title, channel = _quote(item.get("Name") or "", 120), _quote(item.get("ChannelName") or "?", 80)
        if (title.lower(), start, _plain(channel)) in seen:
            continue
        episode = _quote(item.get("EpisodeTitle") or "", 120)
        found.append(Show(start, end, title, channel, _quote(item.get("ChannelNumber") or "", 10), episode, "Jellyfin",
                          _has(f"{title} {episode}", words)))  # fmt: skip
    return found


def _jellyfin_time(text: object) -> datetime | None:
    """Jellyfin's "2026-10-25T20:00:00.0000000Z": seven decimals, which Python 3.11 can't read."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", text).replace("Z", "+00:00"))
    except ValueError:
        return None


def _plain(channel: str) -> str:
    """A channel's name without a quality mark ("CNN (720p)" -> "cnn"), to compare guides."""
    return re.sub(r"\s*\(\d+p\)$", "", channel).strip().lower()
