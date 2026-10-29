"""The Dashboard's API behind /api/: playlists (profiles), channels, channel sources and
Check now, the password, which pseudonym is which player, and the Stack page's requests to
the kia-stack service (kia.stack).

Reading is open to anyone who can open the Dashboard. Changes (profiles, the password)
need the admin password (kia.auth), sent as the X-Kia-Password header, and are accepted
only over HTTPS, so the password never crosses the network in the clear. Requests from
other websites are refused: browsers send a custom header like X-Kia-Password only after
asking the server first (a CORS preflight), which this API never allows, and any Origin
header must match the page's own address.

The profiles stay in the profiles file. Saving rewrites that file, then rebuilds the
profile playlists and the Dashboard from the last check, which takes about a second.
"""

from __future__ import annotations

import contextvars
import ipaddress
import json
import math
import re
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import os
import signal
import subprocess
import sys

from kia import channels, lan, locks, profiles, stack
from kia.config import is_station_line
from kia.files import items, read_object
from kia import sources as web_sources
from kia.auth import Auth
from kia.m3u import parse

# One change at a time, and none while kia check rebuilds the profiles.
# Writing kia's state: shared with a refresh, even one in another process (kia.locks).
LOCK = locks.FilesLock()
PASSWORD_LOCK = threading.Lock()
# Stack requests, one at a time: two at once would both find none waiting. Its own lock,
# so a request never waits on LOCK, which kia's other writes share.
STACK_LOCK = threading.Lock()
# Wrong guesses, by what was guessed and who sent them: (kind, address) -> (how many in a row,
# when the next try is let in). From the second one, each doubles the wait, up to a minute,
# for that address only (behind nginx, the address nginx itself passes on: deploy/kia-api.inc).
# The Dashboard password and the MCP token are counted apart: a right token mustn't reset the
# count of wrong passwords (it would let a token holder guess the password at full speed),
# and an old token on a laptop mustn't hold up the right password.
_WRONG: dict[tuple[str, str], tuple[int, float]] = {}
_CLIENT: contextvars.ContextVar[str] = contextvars.ContextVar("client", default="")


@dataclass
class ApiConfig:
    output_dir: Path
    state_dir: Path
    profiles_file: Path
    epg_url: str | None
    auth: Auth
    https_port: int | None = None  # the secure Dashboard's port, for the "open it" link
    on_change: Callable[[], None] = lambda: None  # e.g. rewrite the Dashboard
    # Choosing playlists (kia.sources): the sources file in use, sources.txt (the list to
    # choose from), kia.toml (for "check now"), and where a test check writes its report.
    sources_file: Path | None = None
    catalog_file: Path | None = None
    config_file: Path | None = None
    test_report: Path | None = None
    recycle_numbers_after_days: int = 0
    channel_numbers: bool = True
    # How the nightly check tests a stream ([check] in kia.toml), for the Test button.
    require_video: bool = True
    user_agent: str | None = None
    ffprobe: str = "ffprobe"
    # Sends one of kia's alerts (kia.alerts.notify): event, title, message.
    alert: Callable[[str, str, str], None] | None = None
    # Off by default. On (KIA_TRUST_FRONTDOOR), the Dashboard lets a person the front door has
    # logged in edit without kia's own password, but only when the gateway's token proves the
    # request came through the front door, so a direct connection can't fake the identity
    # (docs/front-door.md). Admin is the "admins" group; others get read-only for now.
    trust_frontdoor: bool = False


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _items(cfg: ApiConfig) -> list[tuple]:
    curated = cfg.output_dir / "curated.m3u"
    entries = parse(curated.read_text()) if curated.exists() else []
    sources = read_object(cfg.state_dir / "channel-sources.json")  # channel key -> the playlists it came from
    return [(e, set(items(sources[e.key]) if e.key in sources else [e.source] if e.source else [])) for e in entries]


def _load(cfg: ApiConfig) -> list[profiles.Profile]:
    try:
        return profiles.load(cfg.profiles_file)
    except ValueError as exc:
        raise ApiError(409, f"The profiles file has a mistake, so it can't be edited here until it's fixed: {exc}") from None


def get_profiles(cfg: ApiConfig, secure: bool = False) -> dict:
    """The profiles and everything the editor offers: categories, playlists, counts."""
    items = _items(cfg)
    groups: Counter = Counter()
    playlists: Counter = Counter()
    for e, src in items:
        for g in {g.strip() for g in e.group.split(";") if g.strip()}:
            groups[g] += 1
        for s in src:
            playlists[s] += 1
    error = None
    try:
        defined = profiles.load(cfg.profiles_file)
    except ValueError as exc:
        defined, error = [], str(exc)
    source = cfg.auth.source
    return {
        "editable": source != "off" and error is None,
        "secure": secure,
        "https_port": cfg.https_port,
        "numbers": cfg.channel_numbers,
        "password": source,  # where it comes from: env, generated, set, or off
        "error": error,
        "profiles": [{"name": p.name, **profiles.rules(p)} for p in defined],
        # Names for the channels profiles pin by ID, so the editor shows names, not IDs.
        "channel_names": {e.tvg_id: e.name for e, _ in items
                          if e.tvg_id and any(e.tvg_id in p.channels for p in defined)},
        "groups": [{"name": g, "count": n} for g, n in sorted(groups.items(), key=lambda kv: kv[0].casefold())],
        "sources": [{"name": s, "count": n} for s, n in sorted(playlists.items())],
        "total": len(items),
    }


def preview(cfg: ApiConfig, body: dict, headers=None, secure: bool = False) -> dict:
    """How many channels a profile with these settings would have, and a few of them.
    Name patterns are regular expressions, and a crafted one can keep the matcher busy for
    hours, so previews that use them need the password, like saving does."""
    if isinstance(body, dict) and (body.get("names") or body.get("exclude_names")):
        check_password(cfg, (headers or {}).get("X-Kia-Password"), secure)
    p = _profile_from(body)
    picked = profiles.select(p, _items(cfg))
    radio = sum(1 for e in picked if e.is_radio)
    return {"tv": len(picked) - radio, "radio": radio, "sample": [e.name for e in picked[:8]]}


def search_channels(cfg: ApiConfig, query: str) -> dict:
    q = query.strip().casefold()
    if not q:
        return {"channels": []}
    # Each stream's status from the last check, keyed by ID and by name. A row whose fields
    # aren't text is damaged, so it's left out rather than trusted.
    streams = [s for s in items(read_object(cfg.output_dir / "report.json").get("streams"), dict)
               if all(isinstance(s.get(k, ""), str) for k in ("name", "tvg_id", "status"))]  # fmt: skip
    status_by: dict[str, str] = {}
    error_by: dict[str, str] = {}
    for s in streams:
        for key in (s.get("tvg_id"), s.get("name")):
            if key:
                status_by.setdefault(key, s.get("status", ""))
                if s.get("status") not in ("ok", "flaky"):
                    error_by.setdefault(key, s.get("error") or s.get("status") or "")
    found = []
    seen: set[str] = set()
    for e, _ in _items(cfg):
        if e.tvg_id and (q in e.name.casefold() or q in e.tvg_id.casefold()):
            found.append({"id": e.tvg_id, "name": e.name, "group": e.group,
                          "status": status_by.get(e.tvg_id) or status_by.get(e.name) or "ok"})  # fmt: skip
            seen.update((e.tvg_id, e.name))
    # Dead channels the last check dropped from the playlists still matter: someone asking why a
    # channel is down is asking about one that is no longer curated, so it has to show up here.
    for s in streams:
        name, tvg_id = s.get("name", ""), s.get("tvg_id", "")
        if s.get("status") in ("ok", "flaky") or name in seen or tvg_id in seen:
            continue
        if q in name.casefold() or (tvg_id and q in tvg_id.casefold()):
            found.append({"id": tvg_id, "name": name, "group": s.get("group", ""),
                          "status": "dead", "error": error_by.get(tvg_id) or error_by.get(name) or ""})  # fmt: skip
            seen.update((tvg_id, name))
    return {"channels": found[:25]}


def ask_doctor(cfg: ApiConfig, query: str | None = None, topic: str | None = None) -> dict:
    """kia doctor's answers (kia.doctor): the overview, one channel's (query: its ID or part
    of its name), or a topic's ("guide", "pluto", "setup", "plex", "hardware", "jellyfin"). A read, open like the Dashboard's: it
    shows what the pages and Grafana already show, with logins in addresses hidden."""
    from kia import doctor
    from kia.config import load

    if not cfg.config_file:
        raise ApiError(409, "kia doctor needs kia's settings file, which this server wasn't given.")
    if query is not None and len(query) > 200:
        raise ApiError(400, "That's too long for a channel's name or ID.")
    try:
        conf = load(cfg.config_file)
    except (OSError, ValueError) as exc:
        raise ApiError(500, f"kia's settings couldn't be read: {exc}") from None
    if topic == "guide":
        reports = [doctor.guide(conf)]
    elif topic == "pluto":
        reports = [doctor.pluto(conf)]
    elif topic == "setup":
        reports = [doctor.setup(conf)]
    elif topic == "plex":
        reports = [doctor.plex(conf)]
    elif topic == "hardware":
        reports = [doctor.hardware(conf)]
    elif topic == "jellyfin":
        reports = [doctor.jellyfin(conf)]
    else:
        reports = [doctor.overview(conf)] if query is None else doctor.channel(conf, query)
    return {"reports": [r.to_dict() for r in reports]}


def _profile_from(body: dict, name: str = "preview") -> profiles.Profile:
    if not isinstance(body, dict):
        raise ApiError(400, "Expected the profile's settings as a JSON object.")
    settings = {k: v for k, v in body.items() if k != "name"}
    try:
        return profiles.from_rules(name, settings, "the Dashboard")
    except ValueError as exc:  # the file and section it names mean nothing on the page
        raise ApiError(400, str(exc).removeprefix(f"the Dashboard, [{name}]: ")) from None


def save_profile(cfg: ApiConfig, name: str, body: dict) -> dict:
    new = _profile_from(body, name)
    with LOCK:
        current = _load(cfg)
        names = [p.name for p in current]
        updated = [new if p.name == name else p for p in current] if name in names else [*current, new]
        profiles.save(cfg.profiles_file, updated)
        result = profiles.rebuild(cfg.output_dir, cfg.state_dir, cfg.profiles_file, cfg.epg_url, lambda m: None)
    cfg.on_change()
    return result


def delete_profile(cfg: ApiConfig, name: str) -> dict:
    with LOCK:
        current = _load(cfg)
        if name not in [p.name for p in current]:
            raise ApiError(404, f"There's no profile named {name!r}.")
        profiles.save(cfg.profiles_file, [p for p in current if p.name != name])
        result = profiles.rebuild(cfg.output_dir, cfg.state_dir, cfg.profiles_file, cfg.epg_url, lambda m: None)
    cfg.on_change()
    return result


# Favorites and channel numbers


def get_choices(cfg: ApiConfig) -> dict:
    return channels.load(cfg.state_dir)


NUMBERS_OFF = "Channel numbers are off (channel_numbers = false in kia.toml), so there are none to change."


def set_channel(cfg: ApiConfig, body: dict) -> dict:
    if not isinstance(body, dict) or not isinstance(body.get("key"), str):
        raise ApiError(400, "Expected the channel's key.")
    favorite = body.get("favorite")
    if favorite is not None and not isinstance(favorite, bool):
        raise ApiError(400, "favorite is true or false.")
    number = body.get("number", "keep")
    if number != "keep" and not cfg.channel_numbers:
        raise ApiError(409, NUMBERS_OFF)
    with LOCK:
        entry = next((e for e, _ in _items(cfg) if e.key == body["key"]), None)
        if entry is None:
            raise ApiError(404, "That channel isn't in the playlist.")
        choices = channels.load(cfg.state_dir)
        try:
            channels.set_choice(choices, entry.key, "radio" if entry.is_radio else "tv", favorite, number)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None
        channels.save(cfg.state_dir, choices)
        notes: list[str] = []
        before = {e.key: e.attrs.get("tvg-chno") for e, _ in _items(cfg)}
        entries = channels.rebuild(cfg.output_dir, cfg.state_dir, cfg.epg_url, notes.append,
                                   recycle_after_days=cfg.recycle_numbers_after_days, numbered=cfg.channel_numbers)  # fmt: skip
        profiles.rebuild(cfg.output_dir, cfg.state_dir, cfg.profiles_file, cfg.epg_url, lambda m: None)
    cfg.on_change()
    now = next((e for e in entries if e.key == entry.key), entry)
    # Other channels whose number changed because of this one, so the page can say where they went.
    moved = [{"name": e.name, "number": e.attrs.get("tvg-chno")} for e in entries
             if e.key != entry.key and before.get(e.key) != e.attrs.get("tvg-chno")]  # fmt: skip
    return {**choices, "channel": {"key": now.key, "number": now.attrs.get("tvg-chno"),
                                   "favorite": now.key in choices["favorites"]}, "notes": notes, "moved": moved}  # fmt: skip


def _entry(cfg: ApiConfig, body: dict):
    if not isinstance(body, dict) or not isinstance(body.get("key"), str):
        raise ApiError(400, "Expected the channel's key.")
    entry = next((e for e, _ in _items(cfg) if e.key == body["key"]), None)
    if entry is None:
        raise ApiError(404, "That channel isn't in the playlist.")
    return entry


def _from_yours(cfg: ApiConfig, entry) -> bool:
    """Whether any playlist the channel came from is yours (config.Source.is_yours). Without
    kia's settings to tell, it isn't."""
    from kia.config import load

    names = next((srcs for e, srcs in _items(cfg) if e.key == entry.key), set())
    try:
        sources = load(cfg.config_file).sources if cfg.config_file else []
    except (OSError, ValueError):
        return False
    return any(s.is_yours for s in sources if s.name in names)


def stream_url(url: str) -> str:
    """The stream to test: Pluto channels' own address rather than kia's relay for them."""
    from kia import pluto

    m = re.search(r"/pluto/([0-9a-f]{24})\.m3u8$", url)
    return pluto.FALLBACK_URL.format(channel=m.group(1)) if m else url


def test_channel(cfg: ApiConfig, body: dict) -> dict:
    """Try a channel's stream now, the way the nightly check does."""
    import asyncio

    from kia.cli import is_stream_url
    from kia.probe import probe, probe_guarded

    entry = _entry(cfg, body)
    if not is_stream_url(stream_url(entry.url)):  # as the nightly check: only stream addresses
        raise ApiError(400, "That channel's address isn't a stream kia can test.")
    if lan.is_local(lan.host_of(stream_url(entry.url))) and not _from_yours(cfg, entry):
        # As the nightly check (kia.lan): a playlist from the internet can't send tests onto
        # your network. A channel kept from last night can have moved to such an address.
        raise ApiError(400, "That channel's address is on your own network, and its playlist comes from the internet, "
                            "so kia doesn't test it.")  # fmt: skip
    # As the nightly check: one from the internet goes through kia's outbound guard (kia.guard).
    test = probe if _from_yours(cfg, entry) else probe_guarded
    r = asyncio.run(test(stream_url(entry.url), timeout=20, user_agent=entry.user_agent or cfg.user_agent,
                         referrer=entry.referrer, require_video=cfg.require_video and not entry.is_radio,
                         ffprobe=cfg.ffprobe))  # fmt: skip
    return {"key": entry.key, "name": entry.name, "ok": r.ok, "error": r.error, "seconds": round(r.elapsed, 1),
            "height": r.height}  # fmt: skip


def remove_channel(cfg: ApiConfig, body: dict) -> dict:
    """Take a channel out of every playlist until its stream works at a nightly check."""
    with LOCK:
        entry = _entry(cfg, body)
        choices = channels.load(cfg.state_dir)
        choices["removed"][entry.key] = channels.removal(entry)
        channels.save(cfg.state_dir, choices)
        channels.rebuild(cfg.output_dir, cfg.state_dir, cfg.epg_url, lambda m: None,
                         recycle_after_days=cfg.recycle_numbers_after_days, numbered=cfg.channel_numbers)  # fmt: skip
        profiles.rebuild(cfg.output_dir, cfg.state_dir, cfg.profiles_file, cfg.epg_url, lambda m: None)
    cfg.on_change()
    return channels.load(cfg.state_dir)


def put_back(cfg: ApiConfig, body: dict) -> dict:
    """Put a removed channel back now."""
    key = body.get("key") if isinstance(body, dict) else None
    with LOCK:
        choices = channels.load(cfg.state_dir)
        if not isinstance(key, str) or key not in choices["removed"]:
            raise ApiError(404, "That channel wasn't removed.")
        gone = choices["removed"].pop(key)
        channels.save(cfg.state_dir, choices)
        channels.rebuild(cfg.output_dir, cfg.state_dir, cfg.epg_url, lambda m: None,
                         recycle_after_days=cfg.recycle_numbers_after_days, numbered=cfg.channel_numbers, add=parse("#EXTM3U\n" + gone["entry"]))  # fmt: skip
        profiles.rebuild(cfg.output_dir, cfg.state_dir, cfg.profiles_file, cfg.epg_url, lambda m: None)
    cfg.on_change()
    return channels.load(cfg.state_dir)


def renumber(cfg: ApiConfig) -> dict:
    """Number every channel 1, 2, 3 in playlist order, once (kia renumber)."""
    if not cfg.channel_numbers:
        raise ApiError(409, NUMBERS_OFF)
    with LOCK:
        entries = channels.rebuild(cfg.output_dir, cfg.state_dir, cfg.epg_url, lambda m: None, renumber=True,
                                   recycle_after_days=cfg.recycle_numbers_after_days, numbered=cfg.channel_numbers)  # fmt: skip
        profiles.rebuild(cfg.output_dir, cfg.state_dir, cfg.profiles_file, cfg.epg_url, lambda m: None)
    cfg.on_change()
    radio = sum(1 for e in entries if e.is_radio)
    return {"tv": len(entries) - radio, "radio": radio}


# Choosing playlists


def _sources_texts(cfg: ApiConfig) -> tuple[str, str | None]:
    if not cfg.sources_file or not cfg.sources_file.exists():
        raise ApiError(409, "kia was started with playlists given on the command line, so they can't be chosen here.")
    text = cfg.sources_file.read_text()
    catalog = None
    if cfg.catalog_file and cfg.catalog_file.exists() and cfg.catalog_file.resolve() != cfg.sources_file.resolve():
        catalog = cfg.catalog_file.read_text()
    return text, catalog


def _counts(cfg: ApiConfig) -> dict[str, dict]:
    from kia.status import source_counts

    return {c["name"]: {k: c[k] for k in ("ok", "flaky", "dead")} for c in source_counts(cfg.output_dir / "report.json")}


def get_sources(cfg: ApiConfig, secure: bool = False, unlocked: bool = False, locked: str | None = None) -> dict:
    """The channel sources. Addresses can carry a provider's user name and password, so
    they're shown in full only with kia's password, on the secure page. locked: why the
    password given didn't unlock them (wrong, or too many wrong ones), for the page to say."""
    text, catalog = _sources_texts(cfg)
    rows = web_sources.listing(text, catalog, web_sources.load_state(cfg.state_dir), _counts(cfg))
    unlocked = unlocked and secure
    if not unlocked:
        for r in rows:
            r["url"] = mask_url(r["url"])
    return {
        "editable": cfg.auth.source != "off",
        "secure": secure,
        "unlocked": unlocked,
        "file": cfg.sources_file.name,
        "playlists": rows,
        "check": check_state(unlocked),
        **({"locked": locked} if locked and not unlocked else {}),
    }


def check_state(unlocked: bool = False) -> dict:
    state = dict(CHECK)
    if "url" in state and not unlocked:
        state["url"] = mask_url(state["url"])
    return state


def password_error(cfg: ApiConfig, given: str | None, secure: bool) -> str | None:
    """check_password's answer as text, None when the password is right: for reading what
    only the password may show. Nothing to say when no password was given."""
    if not given:
        return ""
    try:
        check_password(cfg, given, secure)
    except ApiError as exc:
        return str(exc)
    return None


def set_source(cfg: ApiConfig, body: dict) -> dict:
    url, on = body.get("url") if isinstance(body, dict) else None, body.get("on") if isinstance(body, dict) else None
    if not isinstance(url, str) or not isinstance(on, bool):
        raise ApiError(400, "Expected the playlist's url and on (true or false).")
    with LOCK:
        text, catalog = _sources_texts(cfg)
        state = web_sources.load_state(cfg.state_dir)
        rows = {r["url"]: r for r in web_sources.listing(text, catalog, state, {})}
        if not on and sum(r["on"] for r in rows.values()) == 1 and rows.get(url, {}).get("on"):
            raise ApiError(409, "That's the last playlist kia uses. Turn another one on first.")
        in_file = any(o.on and o.url == url for o in web_sources.offered(text))
        try:
            web_sources.set_on(state, url, on, in_file, url in rows)
        except ValueError as exc:
            raise ApiError(404, str(exc)) from None
        web_sources.save_state(cfg.state_dir, state)
    return get_sources(cfg, True, unlocked=True)


def add_source(cfg: ApiConfig, body: dict) -> dict:
    if not isinstance(body, dict) or not isinstance(body.get("url"), str):
        raise ApiError(400, "Expected the playlist's url.")
    with LOCK:
        text, catalog = _sources_texts(cfg)
        state = web_sources.load_state(cfg.state_dir)
        rows = web_sources.listing(text, catalog, state, {})
        if any(r["url"] == body["url"].strip() for r in rows):
            raise ApiError(409, "kia already has that playlist. Turn it on in the list instead.")
        try:
            url, name = web_sources.check_new(body["url"], str(body.get("name") or ""), {r["name"] for r in rows})
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None
        radio = body.get("radio", False)
        if not isinstance(radio, bool):  # as every other switch: "false" isn't false
            raise ApiError(400, "Expected radio to be true or false.")
        web_sources.add(state, url, name, radio)
        web_sources.save_state(cfg.state_dir, state)
    return get_sources(cfg, True, unlocked=True)


def remove_source(cfg: ApiConfig, url: str) -> dict:
    with LOCK:
        state = web_sources.load_state(cfg.state_dir)
        text, catalog = _sources_texts(cfg)
        rows = web_sources.listing(text, catalog, state, {})
        if [r["url"] for r in rows if r["on"]] == [url.strip()]:
            raise ApiError(409, "That's the last playlist kia uses. Turn another one on first.")
        try:
            web_sources.remove(state, url)
        except ValueError as exc:
            raise ApiError(404, str(exc)) from None
        web_sources.save_state(cfg.state_dir, state)
    return get_sources(cfg, True, unlocked=True)


def mask_url(url: str) -> str:
    """An address with any user name, password, or query hidden, and its folders: some
    providers put the login in the path (/playlist/USER/PASS/m3u_plus), so only the file's
    name shows, as in https://example.org/•••/m3u_plus."""
    from urllib.parse import urlsplit, urlunsplit

    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:  # not an address, or a port like 99999
        return "•••"
    if not u.scheme.startswith("http"):
        return url
    from kia.tls import in_url

    host = in_url(u.hostname) if u.hostname else ""  # an IPv6 address keeps its brackets
    netloc = ("•••@" if u.username or u.password else "") + host + (f":{port}" if port else "")
    folders, _, name = u.path.rpartition("/")
    path = f"/•••/{name}" if folders.strip("/") else u.path
    return urlunsplit((u.scheme, netloc, path, "•••" if u.query else "", ""))


# "Check now": a test check of one playlist, one at a time. It writes to work/check-test/
# and leaves the playlists players use alone.
# The waits and the clock api uses, through names of its own, so a test can stand in for them
# here without changing the time module for everything else in the process.
_sleep = time.sleep
_clock = time.monotonic

CHECK: dict = {"running": False}
CHECK_REPORT: dict = {}  # the test report's file time when the check started
CHECK_PROC: dict = {}  # the running check's process, so Stop can end it


def _summary(report: Path) -> dict:
    streams = items(read_object(report).get("streams"), dict)  # kia's test report; damaged parts count as missing
    by = Counter(s.get("status") for s in streams)
    working = [str(s.get("name", "")) for s in streams if s.get("status") == "ok"]
    return {"total": len(streams), "ok": by["ok"], "flaky": by["flaky"], "dead": by["dead"], "sample": working[:8]}


def start_check(cfg: ApiConfig, body: dict) -> dict:
    url = body.get("url") if isinstance(body, dict) else None
    if not isinstance(url, str):
        raise ApiError(400, "Expected the playlist's url.")
    text, catalog = _sources_texts(cfg)
    row = next((r for r in web_sources.listing(text, catalog, web_sources.load_state(cfg.state_dir), {}) if r["url"] == url),
               None)  # fmt: skip
    if row is None:
        raise ApiError(404, "kia doesn't know that playlist.")
    if read_object(cfg.output_dir / "status.json").get("result") == "running":
        raise ApiError(409, "kia is refreshing everything right now. Try again when it's done.")
    with LOCK:
        if CHECK.get("running"):
            raise ApiError(409, f"kia is already checking {CHECK.get('name')}. Wait for it to finish, or stop it.")
        CHECK.clear()
        before = cfg.test_report.stat().st_mtime_ns if cfg.test_report and cfg.test_report.exists() else None
        CHECK.update({"running": True, "url": url, "name": row["name"], "started": time.time()})
        CHECK_REPORT["before"] = before
    line = f"{url} name={row['name']}" if is_station_line(url) else f"{url} {row['name']}"
    if row["radio"] and not is_station_line(url):
        line += " radio"
    if row.get("lan") and not is_station_line(url):
        line += " lan"  # as the nightly check reads the line (kia.lan)
    threading.Thread(target=_run_check, args=(cfg, line), daemon=True, name="kia-check-now").start()
    return {"check": check_state(unlocked=True)}


def _run_check(cfg: ApiConfig, line: str) -> None:
    cmd = [sys.executable, "-m", "kia", "check", "-c", str(cfg.config_file), "-u", line]
    try:
        # Its own process group, so Stop ends the ffprobe runs it starts as well.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)  # fmt: skip
        with LOCK:
            CHECK_PROC["proc"] = proc
            stopped = CHECK.get("stopped")
        if stopped:  # Stop came before the process had started
            _end(proc)
        try:
            out, err = proc.communicate(timeout=1800)
        except subprocess.TimeoutExpired:
            _end(proc)
            raise
        if CHECK.get("stopped"):
            CHECK["error"] = "Stopped. Nothing changed."
        # A new report: there was none, or it changed (file times are too coarse to compare to time.time()).
        elif cfg.test_report and cfg.test_report.exists() and cfg.test_report.stat().st_mtime_ns != CHECK_REPORT.get("before"):
            CHECK["result"] = _summary(cfg.test_report)
        else:
            last = (out + err).strip().splitlines()[-1:] or ["no report"]
            CHECK["error"] = f"The check didn't finish: {last[0][:200]}"
    except subprocess.TimeoutExpired:
        CHECK["error"] = "The check took more than 30 minutes and was stopped."
    except Exception as exc:  # report, never crash the server
        CHECK["error"] = f"The check couldn't run: {exc}"
    CHECK_PROC.pop("proc", None)
    CHECK["running"] = False
    CHECK["finished"] = time.time()


def _end(proc) -> None:
    """End a check and everything it started: politely, then for sure."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
            proc.wait(timeout=10)
            break
        except subprocess.TimeoutExpired:
            continue
        except (ProcessLookupError, PermissionError):  # it ended by itself meanwhile
            break
    # A program the check was starting just as the first signal came can miss it (macOS lets
    # a new process slip past a signal sent to its group mid-start). It would keep the check's
    # output open, and the check "running", until it ended by itself, so end what's left.
    # The group's number can't belong to anyone else while a process is still in it.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # the group is gone: nothing was left
        pass


# Adding kia's channels to Plex as Live TV now (kia plex-tuners), from the Stack page once
# Plex runs. kia does the same by itself after each guide refresh, within 4 hours; this
# skips the wait, and the terminal. One at a time; the first run can take a few minutes,
# while Plex downloads its codecs.
PLEX_TUNERS: dict = {"running": False}
_TUNERS_LOCK = threading.Lock()


def plex_tuners_state() -> dict:
    return dict(PLEX_TUNERS)


def start_plex_tuners(cfg: ApiConfig, wait: bool = False) -> dict:
    """Start adding kia's channels to Plex; wait: run it here and return when it's done (an
    assistant's request, which already runs on a thread of its own)."""
    if not os.environ.get("KIA_PLEX_URL"):
        raise ApiError(409, "kia doesn't know where Plex is yet (KIA_PLEX_URL). Apply a choice with Plex first.")
    with _TUNERS_LOCK:
        if PLEX_TUNERS.get("running"):
            raise ApiError(409, "kia is already adding its channels to Plex. Wait for it to finish.")
        PLEX_TUNERS.clear()
        PLEX_TUNERS.update({"running": True, "started": time.time()})
    if wait:
        _run_plex_tuners(cfg)
    else:
        threading.Thread(target=_run_plex_tuners, args=(cfg,), daemon=True, name="kia-plex-tuners").start()
    return {"plex": plex_tuners_state()}


def _run_plex_tuners(cfg: ApiConfig) -> None:
    cmd = [sys.executable, "-m", "kia", "plex-tuners", "-c", str(cfg.config_file)]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=False)
        lines = (done.stdout + done.stderr).strip().splitlines()
        PLEX_TUNERS["output"] = "\n".join(lines[-30:])[-4000:]
        PLEX_TUNERS["ok"] = done.returncode == 0
    except subprocess.TimeoutExpired:
        PLEX_TUNERS.update({"ok": False, "output": "It took more than 30 minutes and was stopped."})
    except Exception as exc:  # report, never crash the server
        PLEX_TUNERS.update({"ok": False, "output": f"It couldn't run: {exc}"})
    PLEX_TUNERS["running"] = False
    PLEX_TUNERS["finished"] = time.time()


def stop_check() -> dict:
    with LOCK:
        proc = CHECK_PROC.get("proc")
        if not CHECK.get("running"):
            raise ApiError(409, "No check is running.")
        CHECK["stopped"] = True  # one that's only starting ends as soon as it has started
    if proc is not None:
        _end(proc)
    return {"check": check_state(unlocked=True)}


def _project_dir(cfg: ApiConfig) -> Path:
    """The kia folder: where kia.toml, stacks/, and scripts/ are."""
    return cfg.config_file.parent if cfg.config_file else cfg.state_dir.parent


def get_stack(cfg: ApiConfig, secure: bool = False) -> dict:
    """The Stack page: what runs, the parts and presets to choose from, and whether the
    kia-stack service is there to make a change. Nothing in it is secret."""
    return {"editable": cfg.auth.source != "off", "secure": secure,
            **stack.page_state(cfg.state_dir, _project_dir(cfg))}  # fmt: skip


def ask_stack(cfg: ApiConfig, action: str, data: dict) -> dict:
    """Ask the kia-stack service to check or apply a choice of parts. Its answer comes
    later, in GET /api/stack; the page asks every few seconds."""
    if stack.profiles() is None:
        raise ApiError(409, "Choosing the parts here is for kia with Docker. Without Docker, there's nothing to choose.")
    if not isinstance(data, dict):
        raise ApiError(400, "Send the parts as {\"parts\": [...]}.")
    try:
        parts = stack.parts_from(data.get("parts"))
        claim = stack.claim_from(data.get("claim"))
        with STACK_LOCK:
            rid = stack.ask(cfg.state_dir, _project_dir(cfg) / stack.SERVICE_DIR, action, parts, claim)
    except stack.StackError as exc:
        raise ApiError(409 if "still working" in str(exc) else 400, str(exc)) from None
    return {"id": rid, **get_stack(cfg, True)}


def get_accounts(cfg: ApiConfig, secure: bool = False) -> dict:
    """The Accounts section: the front-door accounts and their roles, and whether the service is
    there to change them. The accounts themselves aren't secret (names and roles, no passwords)."""
    return {"editable": cfg.auth.source != "off", "secure": secure,
            **stack.account_state(cfg.state_dir, _project_dir(cfg))}  # fmt: skip


def change_account(cfg: ApiConfig, op: str, data: dict) -> dict:
    """Ask the kia-stack service to add, remove, or set the role of a front-door account. kia
    doesn't touch the account store itself; the service (which holds its admin credential) does.
    The answer comes later, in GET /api/accounts, which the page asks for every few seconds."""
    if stack.profiles() is None:
        raise ApiError(409, "Managing accounts here is for kia with Docker.")
    if not isinstance(data, dict):
        raise ApiError(400, "Send the account as JSON.")
    try:
        with STACK_LOCK:
            rid = stack.ask_account(cfg.state_dir, _project_dir(cfg) / stack.SERVICE_DIR, op,
                                    data.get("name"), bool(data.get("admin")), data.get("password") or "")  # fmt: skip
    except stack.StackError as exc:
        raise ApiError(409 if "still working" in str(exc) else 400, str(exc)) from None
    return {"id": rid, **get_accounts(cfg, True)}


def front_door_user(cfg: ApiConfig, headers) -> tuple[str, bool] | None:
    """Who the front door says you are, as (name, is_admin), trusted only when the gateway's
    own token proves the request came through the front door. A direct connection to the web
    server can set X-User-* headers too, so without that token they're ignored, and the
    password is still required. Off unless KIA_TRUST_FRONTDOOR is on and the gateway part runs.
    Admin is the "admins" group (the front door puts the groups in X-User-Groups)."""
    import hmac

    from kia import mcp, stack

    if not cfg.trust_frontdoor:
        return None
    name = (headers.get("X-User-Id") or "").strip()
    if not name or "gateway" not in (stack.profiles() or []):
        return None
    # The gateway sends its own token in X-Kia-Gateway, a header of its own (not Authorization,
    # which the login flow uses and strips). Only that proves the request came through the front
    # door; without it the X-User-* headers could be a direct client's, so they're ignored.
    token = mcp.gateway_token(cfg.config_file.parent if cfg.config_file else None)
    given = (headers.get("X-Kia-Gateway") or "").strip()
    if not (token and given and hmac.compare_digest(given.encode(), token.encode())):
        return None
    # The groups header may be a plain list or a bracketed one, depending on the login; take
    # the group names out of it either way.
    groups = re.findall(r"[A-Za-z0-9_-]+", headers.get("X-User-Groups") or "")
    return name, "admins" in groups


def check_password(cfg: ApiConfig, given: str | None, secure: bool) -> None:
    if cfg.auth.source == "off":
        raise ApiError(403, "Editing is off (KIA_EDITING=false in kia's .env file).")
    if not secure:
        raise ApiError(403, "Changes are accepted only on the secure (https://) Dashboard.")
    if not given:
        raise ApiError(401, "Enter kia's password first.")
    _slowed("password", lambda: cfg.auth.check(given), "Wrong password.", "Too many wrong passwords.", cfg.alert)


def _who(address: str | None) -> str | None:
    """Whom a wrong guess counts against: the address, or for IPv6 its /64, since one computer
    can pick a new address in its network for every guess."""
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return address
    return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ip.version == 6 and not ip.ipv4_mapped else address


GUESSES = 10  # wrong ones in a row from one address: someone guessing, or a device with an old one


def _slowed(kind: str, right: Callable[[], bool], wrong_text: str, too_many: str,
            alert: Callable[[str, str, str], None] | None = None) -> None:  # fmt: skip
    """Check a password or token, slowing down whoever keeps getting it wrong: a second
    after each wrong one, then a wait that doubles from the second in a row, up to a minute,
    per kind and address (_CLIENT). Raises ApiError when it's wrong or they must wait. The
    GUESSES-th wrong one in a row is an alert, once a streak (a right one ends it)."""
    client = (kind, _who(_CLIENT.get()))
    # One check at a time (each costs 16 MB), with the count of wrong ones read and updated
    # in the same turn: read before waiting, a burst of guesses sent at once would all pass
    # the "too many" check, and each would count as only the first.
    with PASSWORD_LOCK:
        wrong, until = _WRONG.get(client, (0, 0.0))
        if until > _clock():
            raise ApiError(429, f"{too_many} Try again in {math.ceil(until - _clock())} seconds.")
        if right():
            _WRONG.pop(client, None)
            return
        if len(_WRONG) > 1000:  # many addresses: keep only those still waiting
            now = _clock()
            for k in [k for k, (_, u) in _WRONG.items() if u < now]:
                del _WRONG[k]
        _WRONG[client] = (wrong + 1, _clock() + (min(2.0**wrong, 60.0) if wrong else 0))
    if alert and wrong + 1 == GUESSES:
        what = "the Dashboard's password" if kind == "password" else "kia's MCP token"
        # The address is kia's own reading of who connected (_CLIENT), never text from the request.
        address = _CLIENT.get() or "an unknown address"
        # On its own thread: sending can take seconds, and the answer shouldn't wait.
        threading.Thread(target=alert, daemon=True, args=(
            "guessing", f"kia: {GUESSES} wrong tries at {what} from {address}",
            f"{address} got {what} wrong {GUESSES} times in a row. kia slows each try down, up to a minute "
            "apart. It may be a device with an old one saved (an assistant after a new MCP token, say); if "
            "you don't know the address, someone on your network may be guessing.")).start()  # fmt: skip
    _sleep(1)  # outside the lock: someone guessing doesn't hold up the right password
    raise ApiError(401, wrong_text)


def mcp_request(cfg: ApiConfig, method: str, headers, body: bytes, secure: bool) -> tuple[int, dict | None]:
    """kia's MCP server (kia.mcp) at /api/mcp: off until its token is made, then HTTPS and the
    token only. Tools only read, but they describe your setup, so they're kept to whoever
    holds the token; a wrong one is slowed down like a wrong password."""
    import hmac

    from kia import mcp
    from kia.config import load

    # kia's own token, and the gateway part's while it's chosen (scripts/gateway.sh).
    from kia import stack

    project = cfg.config_file.parent if cfg.config_file else None
    gateway = "gateway" in (stack.profiles() or [])
    expected = mcp.tokens(cfg.state_dir, project, gateway)
    if not expected:
        raise ApiError(404, "kia's MCP server is off. Turn it on with: kia mcp-token")
    if method != "POST":
        raise ApiError(405, "kia's MCP server takes POST only: one JSON answer per request, no event stream.")
    if not secure:
        raise ApiError(403, "kia's MCP server answers only on the secure (https://) port.")
    scheme, _, given = (headers.get("Authorization") or "").strip().partition(" ")
    given = given.strip() if scheme.lower() == "bearer" else ""  # "Bearer" in any case (RFC 6750)
    if not given:
        raise ApiError(401, "Send kia's MCP token: Authorization: Bearer <token> (kia mcp-token shows it).")
    try:
        # Every token compared, not just up to the first that matches: how long it takes
        # doesn't say which one a guess was close to.
        _slowed("mcp", lambda: any([hmac.compare_digest(given.encode(), t.encode()) for t in expected]),
                "Wrong MCP token.", "Too many wrong MCP tokens.", cfg.alert)  # fmt: skip
    except ApiError as exc:
        print(f"MCP: refused a token from {_CLIENT.get() or 'a client'} ({exc.status})", flush=True)
        raise
    if not cfg.config_file:
        raise ApiError(409, "kia's MCP server needs kia's settings file, which this server wasn't given.")
    who = _CLIENT.get()
    # The device's name counts only with the gateway's token: the gateway sets it from the
    # device's key. With kia's own token, anyone could send any name.
    gateway_token = mcp.gateway_token(project) if gateway else None
    device = ""
    if gateway_token and hmac.compare_digest(given.encode(), gateway_token.encode()):
        who = mcp.via_gateway(headers.get("X-Kia-Device"), who)
        device = headers.get("X-Kia-Device") or ""
    return mcp.handle(lambda: load(cfg.config_file), cfg, headers, body, lambda m: print(m, flush=True), who, device)


# Requests from AI assistants (kia.asks): what each kind runs once you approve it, with the
# same code as the Dashboard's own button, or the same kia command the server runs by itself.
# Each slow kind runs on a thread of its own, so Approve answers at once; the page and the
# assistant read how it went later.
SLOW_ASKS = {"refresh_guide", "add_to_plex", "check_stack"}
STACK_CHECK_UP_TO = stack.REQUEST_EXPIRES + 120  # seconds to wait for the kia-stack service's answer


def _last_lines(text: str, n: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-n:])[-3000:]


def _refresh_guide(cfg: ApiConfig) -> dict:
    """kia refresh-guide, as the server runs it every few hours. It takes kia's refresh lock
    (kia.locks): while the nightly refresh runs, it stops at once and says why."""
    cmd = [sys.executable, "-m", "kia", "refresh-guide", "-c", str(cfg.config_file)]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "summary": "The guide refresh took more than 15 minutes and was stopped."}
    out = _last_lines(done.stdout + done.stderr)
    said = next((line for line in reversed(out.splitlines()) if "quick guide sources" in line), "")
    if done.returncode == 0:
        summary = f"{said}, and the guide was rebuilt." if said else "The guide was rebuilt."
    else:
        summary = "The guide refresh didn't finish: " + (out.splitlines()[-1:] or ["no reason given"])[0][:200]
    return {"ok": done.returncode == 0, "summary": summary, "output": out}


def _add_to_plex(cfg: ApiConfig) -> dict:
    """The Stack page's button: kia plex-tuners, then what it said."""
    state = start_plex_tuners(cfg, wait=True)["plex"]
    ok = bool(state.get("ok"))
    return {"ok": ok, "summary": "kia's channels are in Plex." if ok else "Adding kia's channels to Plex didn't work.",
            "output": _last_lines(state.get("output", ""))}  # fmt: skip


def _check_stack(cfg: ApiConfig, parts: list) -> dict:
    """The Stack page's Check: the kia-stack service checks the parts and answers in its own
    folder, which kia reads until the answer to this request comes."""
    rid = ask_stack(cfg, "check", {"parts": parts})["id"]
    service_dir = _project_dir(cfg) / stack.SERVICE_DIR
    end = _clock() + STACK_CHECK_UP_TO
    while _clock() < end:
        res = stack.result(service_dir) or {}
        if res.get("id") == rid and res.get("state") in ("finished", "refused"):
            ok = res.get("state") == "finished" and res.get("exit") == "0"
            return {"ok": ok, "summary": "The check passed: these parts can run together." if ok else
                    "The check found problems; its output says which.", "output": _last_lines(res.get("output", ""), 40)}  # fmt: skip
        _sleep(2)
    return {"ok": False, "summary": "The kia-stack service didn't answer. Is it running? (docs/stack.md)"}


def _run_ask(cfg: ApiConfig, kind: str, args: dict) -> dict:
    if kind == "test_channel":
        return test_channel(cfg, {"key": args.get("key")})
    if kind == "refresh_guide":
        return _refresh_guide(cfg)
    if kind == "add_to_plex":
        return _add_to_plex(cfg)
    if kind == "check_stack":
        return _check_stack(cfg, args.get("parts"))
    raise ApiError(400, "kia doesn't know that kind of request.")


def _shown(r: dict) -> dict:
    """A request as the Dashboard and assistants see it."""
    from kia import asks

    out = {k: r[k] for k in ("id", "kind", "what", "who", "state", "asked", "auto") if k in r}
    # For the Dashboard's "allow for an hour": only a kind that may run by itself, from a
    # caller kia can tell apart from the others.
    shared = not _device_of(r) and shared_address(r.get("who", ""))
    out["can_allow"] = r["kind"] in asks.AUTO_KINDS and not shared
    if shared:
        out["shared_caller"] = True
    if r["state"] == "waiting":
        out["minutes_left"] = max(0, round((r["expires"] - time.time()) / 60))
    for k in ("result", "error"):
        if k in r:
            out[k] = r[k]
    return out


SHARED_CALLER = ("kia can't tell assistants apart at this address, so allowing one for an hour would allow them all. "
                 "Approve it on its own, or give each device a key with the gateway, or list the kind in "
                 "kia.toml's auto_approve.")  # fmt: skip


def _device_of(r: dict) -> str:
    """The gateway device a request came from, or "" (kia wrote it, but state/ is checked)."""
    device = r.get("device")
    return device if isinstance(device, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", device) else ""


def _docker_desktop() -> bool:
    """Whether kia runs under Docker Desktop (macOS, Windows): its Linux is LinuxKit."""
    try:
        return "linuxkit" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _network_gateways() -> set[str]:
    """The first address of each network kia's container is on, which Docker gives the
    network's gateway: calls from the kia computer itself arrive from it. From
    /proc/net/route, whose addresses are hex in the kernel's (little-endian) byte order."""
    found = set()
    try:
        lines = Path("/proc/net/route").read_text().splitlines()[1:]
    except OSError:
        return found
    for line in lines:
        cells = line.split()
        try:
            dest, mask = int(cells[1], 16), int(cells[7], 16)
        except (IndexError, ValueError):
            continue
        if dest and mask:
            found.add(str(ipaddress.IPv4Address(int.from_bytes(((dest & mask) + (1 << 24)).to_bytes(4, "little"), "big"))))
    return found


def shared_address(address: str) -> bool:
    """Whether several devices can reach kia from this one address, so it can't stand for one
    assistant: every address under Docker Desktop (it shows kia one for everyone), and on
    Linux the computer's own (loopback, or a Docker network's gateway). "stdio" (kia mcp) and
    a missing address count as shared too: anything on this computer, or anyone."""
    if not address or address in ("stdio", "an assistant") or _docker_desktop():
        return True
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True  # not an address kia knows
    return ip.is_loopback or ip.is_unspecified or address in _network_gateways()


def get_asks(cfg: ApiConfig) -> dict:
    from kia import asks

    return {"asks": [_shown(r) for r in asks.listing(cfg.state_dir)], "allowed": asks.allowed(cfg.state_dir)}


def answer_ask(cfg: ApiConfig, body: dict, approve: bool) -> dict:
    """Approve (run) or decline a request from an assistant. {"allow_hour": true} with an
    approval also lets that kind from that caller run without you for the next hour."""
    from kia import asks

    ask_id = body.get("id") if isinstance(body, dict) else None
    if not isinstance(ask_id, str):
        raise ApiError(400, "Expected the request's id.")
    hour = approve and body.get("allow_hour") is True
    if hour:
        r = asks.get(cfg.state_dir, ask_id)
        if r is not None and r["kind"] not in asks.AUTO_KINDS:
            raise ApiError(409, f"{asks.KINDS[r['kind']]}: kia always asks you about this one.")
        if r is not None and not _device_of(r) and shared_address(r["who"]):
            raise ApiError(409, SHARED_CALLER)
    shown = run_request(cfg, ask_id, approve)
    if hour:
        asks.allow(cfg.state_dir, shown["kind"], shown["who"])
        print(f"Requests to {shown['kind']} from {shown['who']}: allowed for an hour", flush=True)
    return {"ask": shown}


def stop_allowing(cfg: ApiConfig) -> dict:
    """End the Dashboard's allowances: requests wait for you again."""
    from kia import asks

    asks.stop_allowing(cfg.state_dir)
    print("Requests from assistants: allowances stopped", flush=True)
    return get_asks(cfg)


def run_request(cfg: ApiConfig, ask_id: str, approve: bool = True, auto: str = "") -> dict:
    """Approve and run a waiting request, or decline it, as the Dashboard shows it. A slow
    kind runs on a thread, and answers "running". auto: why it runs without you."""
    from kia import asks

    try:
        r = asks.take(cfg.state_dir, ask_id, approve, auto=auto)
    except asks.AskError as exc:
        raise ApiError(409, str(exc)) from None
    said = f"approved automatically ({auto})" if auto else "approved" if approve else "declined"
    print(f"Request {r['id']} ({r['kind']}) from {r.get('who')}: {said}", flush=True)

    def run() -> dict:
        done = asks.finish(cfg.state_dir, r, lambda kind, args: _run_ask(cfg, kind, args))
        print(f"Request {done['id']} ({done['kind']}): {done['state']}", flush=True)
        return done

    if not approve:
        return _shown(r)
    if r["kind"] in SLOW_ASKS:
        threading.Thread(target=run, daemon=True, name=f"kia-ask-{r['kind']}").start()
        return _shown(r)
    return _shown(run())


def get_name(cfg: ApiConfig) -> dict:
    """This kia's name (kia.name), where it comes from, and what it would be without the
    Dashboard's: for the page header and the System page."""
    from kia import name

    current, source = name.current(cfg.state_dir)
    return {"name": current, "from": source, "env": name.clean(os.environ.get("KIA_NAME", "")),
            "computer": name.computer_name()}  # fmt: skip


def set_name(cfg: ApiConfig, body: dict) -> dict:
    """Name this kia from the Dashboard; an empty name goes back to KIA_NAME or the computer's."""
    from kia import name

    text = body.get("name") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise ApiError(400, "Expected the name.")
    try:
        name.save(cfg.state_dir, text)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    return get_name(cfg)


def change_password(cfg: ApiConfig, body: dict) -> dict:
    new = body.get("new") if isinstance(body, dict) else None
    if not isinstance(new, str):
        raise ApiError(400, "Expected the new password.")
    with LOCK:
        try:
            cfg.auth.change(new)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None
    cfg.on_change()
    return {"password": cfg.auth.source}


# kia's chat page (kia.chat): a household member's question, answered through the gateway's AI
# route with the household's own key. Read-only, so the model can look things up with kia's tools
# and explain them, never change anything. The settings come from the environment, like
# KIA_PLEX_URL does: KIA_CHAT_URL is the gateway's AI route, and gateway/ai-key holds the one
# household key (scripts/gateway.sh writes it when the chat is set up).
CHAT_MAX_TURNS = 40  # turns kept from the browser's history (it sends the recent chat each time)
CHAT_MAX_CHARS = 8000  # characters in one turn: a question or an answer, not a pasted file


def _chat_key(cfg: ApiConfig) -> str | None:
    """The household's gateway AI key, from gateway/ai-key in the kia folder. Only the first line,
    and not a long one: anything else isn't a key scripts/gateway.sh wrote."""
    project = cfg.config_file.parent if cfg.config_file else None
    if project is None:
        return None
    try:
        with open(project / "gateway" / "ai-key", encoding="utf-8", errors="replace") as f:
            value = f.read(300).splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    return value if 8 <= len(value) <= 200 else None


def _chat_thinking() -> bool:
    """Whether the model may think (adaptive) before it answers (KIA_CHAT_THINKING, on by default)."""
    return os.environ.get("KIA_CHAT_THINKING", "true").strip().lower() not in ("false", "0", "no", "off")


def _chat_max_tokens() -> int:
    """Room for the model's answer (KIA_CHAT_MAX_TOKENS), kept in a sensible range; a bad value
    falls back to the default rather than stopping the chat."""
    from kia import chat

    try:
        return max(256, min(int(os.environ.get("KIA_CHAT_MAX_TOKENS", "")), 8192))
    except ValueError:
        return chat.MAX_TOKENS


def _chat_history(body: object) -> list[dict]:
    """The conversation the browser sent, checked: a non-empty list of plain turns, each a user or
    assistant with text content, the last one the member's new question. Raises ApiError on
    anything else, so a crafted body can't run up a bill with a huge or malformed conversation."""
    turns = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(turns, list) or not turns:
        raise ApiError(400, "Send the conversation as {\"messages\": [{\"role\": \"user\", \"content\": \"...\"}]}.")
    history = []
    for t in turns[-CHAT_MAX_TURNS:]:
        role, content = (t.get("role"), t.get("content")) if isinstance(t, dict) else (None, None)
        if role not in ("user", "assistant") or not isinstance(content, str):
            raise ApiError(400, "Each turn needs a role of user or assistant and text content.")
        if len(content) > CHAT_MAX_CHARS:
            raise ApiError(400, f"A message is longer than {CHAT_MAX_CHARS} characters.")
        history.append({"role": role, "content": content})
    if history[-1]["role"] != "user":
        raise ApiError(400, "The last turn must be the member's question.")
    return history


def chat_reply(cfg: ApiConfig, body: object, who: str) -> dict:
    """Answer a chat question: run kia's read-only tools and a model through the gateway, with the
    household key. who is the member kia logs. Returns what the page shows: the reply, the tools it
    used, and whether the household is over its budget. The AI provider's key stays at the gateway;
    kia sends only the household's gateway key."""
    from kia import chat, mcp
    from kia.config import load

    history = _chat_history(body)
    if os.environ.get("KIA_CHAT", "").strip().lower() not in ("true", "1", "yes", "on"):
        return {"available": False, "reply": "kia's chat is off. Turn it on with KIA_CHAT=true (docs/chat.md)."}
    url, key = os.environ.get("KIA_CHAT_URL", "").strip(), _chat_key(cfg)
    if not url or not key:
        return {"available": False,
                "reply": "kia's chat isn't set up yet. It needs the gateway's AI route (docs/chat.md)."}
    if not cfg.config_file:
        raise ApiError(409, "kia's chat needs kia's settings file, which this server wasn't given.")
    heavy = isinstance(body, dict) and body.get("model") == "heavy"
    model = (os.environ.get("KIA_CHAT_MODEL_HEAVY") or chat.OPUS_MODEL) if heavy else \
            (os.environ.get("KIA_CHAT_MODEL") or chat.DEFAULT_MODEL)  # fmt: skip
    conf = load(cfg.config_file)
    gateway = chat.Gateway(url, key)
    print(f"Chat: a question for {model} from {who}", flush=True)
    try:
        out = chat.reply(history, gateway.post,
                         lambda name, args: mcp.run_read_only(name, conf, cfg, args),
                         model=model, thinking=_chat_thinking(), max_tokens=_chat_max_tokens())  # fmt: skip
    except chat.BudgetExceeded as exc:
        return {"available": True, "over_budget": True, "reply": str(exc)}
    except chat.ChatError as exc:
        return {"available": True, "error": str(exc)}
    return {"available": True, **out}


def handle(cfg: ApiConfig, method: str, path: str, headers, body: bytes, secure: bool = False,
           client: str = "") -> tuple[int, dict | None]:  # fmt: skip
    """Route one /api/ request. Returns (HTTP status, JSON body), the body None for none (MCP's
    202). secure: it came over HTTPS; client: who sent it, for slowing down wrong passwords."""
    _CLIENT.set(client)
    url = urlparse(path)
    parts = [p for p in url.path.split("/") if p][1:]  # after "api"
    origin = headers.get("Origin")
    host = headers.get("Host")
    # A person the front door has logged in (trusted only with the gateway's token). A trusted
    # admin edits without kia's own password; everyone else still needs it. Changes still need
    # the secure (https) page, as with the password.
    editor = front_door_user(cfg, headers)

    def need_edit() -> None:
        if secure and editor and editor[1]:
            return
        check_password(cfg, headers.get("X-Kia-Password"), secure)

    def need_member() -> None:
        # The chat spends money, so it's for the household: a logged-in front-door member (any
        # role, not only admins) on the secure page, or kia's own password. Everyone in the house
        # can ask; a direct connection without the password can't.
        if secure and editor:
            return
        check_password(cfg, headers.get("X-Kia-Password"), secure)

    try:
        if origin and urlparse(origin).netloc != host:
            raise ApiError(403, "Requests from other websites aren't allowed.")
        if parts == ["mcp"]:  # JSON-RPC, with errors of its own: before the parsing below
            return mcp_request(cfg, method, headers, body, secure)
        data = {}
        if body:
            try:
                data = json.loads(body)
            except ValueError:
                raise ApiError(400, "The request wasn't valid JSON.") from None
        if parts == ["profiles"] and method == "GET":
            return 200, get_profiles(cfg, secure)
        if parts == ["preview"] and method == "POST":
            return 200, preview(cfg, data, headers, secure)
        if parts == ["channels"] and method == "GET":
            return 200, search_channels(cfg, parse_qs(url.query).get("q", [""])[0])
        if parts == ["doctor"] and method == "GET":
            return 200, ask_doctor(cfg)
        if parts == ["doctor", "channel"] and method == "GET":
            return 200, ask_doctor(cfg, parse_qs(url.query).get("q", [""])[0])
        if parts == ["doctor", "guide"] and method == "GET":
            return 200, ask_doctor(cfg, topic="guide")
        if parts == ["doctor", "pluto"] and method == "GET":
            return 200, ask_doctor(cfg, topic="pluto")
        if parts == ["doctor", "setup"] and method == "GET":
            return 200, ask_doctor(cfg, topic="setup")
        if parts == ["doctor", "plex"] and method == "GET":
            return 200, ask_doctor(cfg, topic="plex")
        if parts == ["doctor", "hardware"] and method == "GET":
            return 200, ask_doctor(cfg, topic="hardware")
        if parts == ["doctor", "jellyfin"] and method == "GET":
            return 200, ask_doctor(cfg, topic="jellyfin")
        if parts == ["channel-choices"] and method == "GET":
            return 200, get_choices(cfg)
        if parts == ["channel", "test"] and method == "POST":
            need_edit()
            return 200, test_channel(cfg, data)
        if parts == ["channel", "remove"] and method == "POST":
            need_edit()
            return 200, remove_channel(cfg, data)
        if parts == ["channel", "restore"] and method == "POST":
            need_edit()
            return 200, put_back(cfg, data)
        if parts == ["renumber"] and method == "POST":
            need_edit()
            return 200, renumber(cfg)
        if parts == ["channel"] and method == "PUT":
            need_edit()
            return 200, set_channel(cfg, data)
        if parts == ["players"] and method == "GET":
            # Which pseudonym in the graphs is which Plex player: names can be personal.
            need_edit()
            from kia.metrics import known_players

            return 200, {"players": known_players(cfg.state_dir)}
        if parts == ["editor"] and method == "GET":
            # Whether the viewer is a trusted front-door admin, so the Dashboard can skip asking
            # for kia's password. Only the gateway's token makes it true (front_door_user); a
            # direct connection, or anyone not an admin, gets false and still sees the prompts.
            admin = bool(secure and editor and editor[1])
            return 200, {"frontdoor_admin": admin, "name": editor[0] if admin else ""}
        if parts == ["sources"] and method == "GET":
            # A front-door admin can already edit the sources, so they see the credentials in them
            # too, without kia's password; everyone else gives the password to unlock them.
            if secure and editor and editor[1]:
                return 200, get_sources(cfg, secure, True)
            why = password_error(cfg, headers.get("X-Kia-Password"), secure)
            return 200, get_sources(cfg, secure, why is None, locked=why)
        if parts == ["sources", "check"] and method == "GET":
            return 200, {"check": check_state()}
        if parts and parts[0] == "sources" and method in ("PUT", "POST", "DELETE"):
            need_edit()
            if parts == ["sources"] and method == "PUT":
                return 200, set_source(cfg, data)
            if parts == ["sources"] and method == "POST":
                return 200, add_source(cfg, data)
            if parts == ["sources"] and method == "DELETE":
                return 200, remove_source(cfg, parse_qs(url.query).get("url", [""])[0])
            if parts == ["sources", "check"] and method == "POST":
                return 200, start_check(cfg, data)
            if parts == ["sources", "check"] and method == "DELETE":
                return 200, stop_check()
        if parts == ["stack"] and method == "GET":
            return 200, get_stack(cfg, secure)
        if parts == ["stack", "check"] and method == "POST":
            need_edit()
            return 200, ask_stack(cfg, "check", data)
        if parts == ["stack", "apply"] and method == "POST":
            need_edit()
            return 200, ask_stack(cfg, "apply", data)
        if parts == ["accounts"] and method == "GET":
            return 200, get_accounts(cfg, secure)
        if len(parts) == 2 and parts[0] == "accounts" and parts[1] in ("add", "remove", "set-admin") and method == "POST":
            need_edit()
            return 200, change_account(cfg, parts[1], data)
        if parts == ["plex", "tuners"] and method == "GET":
            return 200, {"plex": plex_tuners_state()}
        if parts == ["plex", "tuners"] and method == "POST":
            need_edit()
            return 200, start_plex_tuners(cfg)
        if parts == ["chat"] and method == "POST":
            need_member()
            who = "the Dashboard"
            if secure and editor:
                who = re.sub(r"[^A-Za-z0-9 ._@-]", "", editor[0])[:40] or "a member"
            return 200, chat_reply(cfg, data, who)
        if parts == ["asks"] and method == "GET":
            return 200, get_asks(cfg)
        if parts == ["asks", "approve"] and method == "POST":
            need_edit()
            return 200, answer_ask(cfg, data, approve=True)
        if parts == ["asks", "decline"] and method == "POST":
            need_edit()
            return 200, answer_ask(cfg, data, approve=False)
        if parts == ["asks", "stop-allowing"] and method == "POST":
            need_edit()
            return 200, stop_allowing(cfg)
        if parts == ["name"] and method == "GET":
            return 200, get_name(cfg)
        if parts == ["name"] and method == "PUT":
            need_edit()
            return 200, set_name(cfg, data)
        if parts == ["password"] and method == "POST":
            need_edit()
            return 200, change_password(cfg, data)
        if len(parts) == 2 and parts[0] == "profiles" and method in ("PUT", "DELETE"):
            need_edit()
            if method == "PUT":
                return 200, save_profile(cfg, parts[1], data)
            return 200, delete_profile(cfg, parts[1])
        raise ApiError(404, "Not found.")
    except ApiError as exc:
        return exc.status, {"error": str(exc)}
