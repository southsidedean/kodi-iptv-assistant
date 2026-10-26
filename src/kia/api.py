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


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _items(cfg: ApiConfig) -> list[tuple]:
    curated = cfg.output_dir / "curated.m3u"
    entries = parse(curated.read_text()) if curated.exists() else []
    try:
        sources = json.loads((cfg.state_dir / "channel-sources.json").read_text())
    except (OSError, ValueError):
        sources = {}
    return [(e, set(sources.get(e.key, [e.source] if e.source else []))) for e in entries]


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
    found = [
        {"id": e.tvg_id, "name": e.name, "group": e.group}
        for e, _ in _items(cfg)
        if q and e.tvg_id and (q in e.name.casefold() or q in e.tvg_id.casefold())
    ]
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
    from kia.probe import probe

    entry = _entry(cfg, body)
    if not is_stream_url(stream_url(entry.url)):  # as the nightly check: only stream addresses
        raise ApiError(400, "That channel's address isn't a stream kia can test.")
    if lan.is_local(lan.host_of(stream_url(entry.url))) and not _from_yours(cfg, entry):
        # As the nightly check (kia.lan): a playlist from the internet can't send tests onto
        # your network. A channel kept from last night can have moved to such an address.
        raise ApiError(400, "That channel's address is on your own network, and its playlist comes from the internet, "
                            "so kia doesn't test it.")  # fmt: skip
    r = asyncio.run(probe(stream_url(entry.url), timeout=20, user_agent=entry.user_agent or cfg.user_agent,
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
CHECK: dict = {"running": False}
CHECK_REPORT: dict = {}  # the test report's file time when the check started
CHECK_PROC: dict = {}  # the running check's process, so Stop can end it


def _summary(report: Path) -> dict:
    streams = json.loads(report.read_text()).get("streams", [])
    by = Counter(s["status"] for s in streams)
    working = [s["name"] for s in streams if s["status"] == "ok"]
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
    try:
        if json.loads((cfg.output_dir / "status.json").read_text()).get("result") == "running":
            raise ApiError(409, "kia is refreshing everything right now. Try again when it's done.")
    except (OSError, ValueError):
        pass
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
            return
        except subprocess.TimeoutExpired:
            continue
        except (ProcessLookupError, PermissionError):  # it ended by itself meanwhile
            return


# Adding kia's channels to Plex as Live TV now (kia plex-tuners), from the Stack page once
# Plex runs. kia does the same by itself after each guide refresh, within 4 hours; this
# skips the wait, and the terminal. One at a time; the first run can take a few minutes,
# while Plex downloads its codecs.
PLEX_TUNERS: dict = {"running": False}
_TUNERS_LOCK = threading.Lock()


def plex_tuners_state() -> dict:
    return dict(PLEX_TUNERS)


def start_plex_tuners(cfg: ApiConfig) -> dict:
    if not os.environ.get("KIA_PLEX_URL"):
        raise ApiError(409, "kia doesn't know where Plex is yet (KIA_PLEX_URL). Apply a choice with Plex first.")
    with _TUNERS_LOCK:
        if PLEX_TUNERS.get("running"):
            raise ApiError(409, "kia is already adding its channels to Plex. Wait for it to finish.")
        PLEX_TUNERS.clear()
        PLEX_TUNERS.update({"running": True, "started": time.time()})
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


def check_password(cfg: ApiConfig, given: str | None, secure: bool) -> None:
    if cfg.auth.source == "off":
        raise ApiError(403, "Editing is off (KIA_EDITING=false in kia's .env file).")
    if not secure:
        raise ApiError(403, "Changes are accepted only on the secure (https://) Dashboard.")
    if not given:
        raise ApiError(401, "Enter kia's password first.")
    _slowed("password", lambda: cfg.auth.check(given), "Wrong password.", "Too many wrong passwords.")


def _who(address: str | None) -> str | None:
    """Whom a wrong guess counts against: the address, or for IPv6 its /64, since one computer
    can pick a new address in its network for every guess."""
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return address
    return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ip.version == 6 and not ip.ipv4_mapped else address


def _slowed(kind: str, right: Callable[[], bool], wrong_text: str, too_many: str) -> None:
    """Check a password or token, slowing down whoever keeps getting it wrong: a second
    after each wrong one, then a wait that doubles from the second in a row, up to a minute,
    per kind and address (_CLIENT). Raises ApiError when it's wrong or they must wait."""
    client = (kind, _who(_CLIENT.get()))
    # One check at a time (each costs 16 MB), with the count of wrong ones read and updated
    # in the same turn: read before waiting, a burst of guesses sent at once would all pass
    # the "too many" check, and each would count as only the first.
    with PASSWORD_LOCK:
        wrong, until = _WRONG.get(client, (0, 0.0))
        if until > time.monotonic():
            raise ApiError(429, f"{too_many} Try again in {math.ceil(until - time.monotonic())} seconds.")
        if right():
            _WRONG.pop(client, None)
            return
        if len(_WRONG) > 1000:  # many addresses: keep only those still waiting
            now = time.monotonic()
            for k in [k for k, (_, u) in _WRONG.items() if u < now]:
                del _WRONG[k]
        _WRONG[client] = (wrong + 1, time.monotonic() + (min(2.0**wrong, 60.0) if wrong else 0))
    time.sleep(1)  # outside the lock: someone guessing doesn't hold up the right password
    raise ApiError(401, wrong_text)


def mcp_request(cfg: ApiConfig, method: str, headers, body: bytes, secure: bool) -> tuple[int, dict | None]:
    """kia's MCP server (kia.mcp) at /api/mcp: off until its token is made, then HTTPS and the
    token only. Tools only read, but they describe your setup, so they're kept to whoever
    holds the token; a wrong one is slowed down like a wrong password."""
    import hmac

    from kia import mcp
    from kia.config import load

    expected = mcp.token(cfg.state_dir)
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
        _slowed("mcp", lambda: hmac.compare_digest(given.encode(), expected.encode()), "Wrong MCP token.", "Too many wrong MCP tokens.")
    except ApiError as exc:
        print(f"MCP: refused a token from {_CLIENT.get() or 'a client'} ({exc.status})", flush=True)
        raise
    if not cfg.config_file:
        raise ApiError(409, "kia's MCP server needs kia's settings file, which this server wasn't given.")
    return mcp.handle(lambda: load(cfg.config_file), cfg, headers, body, lambda m: print(m, flush=True), _CLIENT.get())


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


def handle(cfg: ApiConfig, method: str, path: str, headers, body: bytes, secure: bool = False,
           client: str = "") -> tuple[int, dict | None]:  # fmt: skip
    """Route one /api/ request. Returns (HTTP status, JSON body), the body None for none (MCP's
    202). secure: it came over HTTPS; client: who sent it, for slowing down wrong passwords."""
    _CLIENT.set(client)
    url = urlparse(path)
    parts = [p for p in url.path.split("/") if p][1:]  # after "api"
    origin = headers.get("Origin")
    host = headers.get("Host")
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
            check_password(cfg, headers.get("X-Kia-Password"), secure)
            return 200, test_channel(cfg, data)
        if parts == ["channel", "remove"] and method == "POST":
            check_password(cfg, headers.get("X-Kia-Password"), secure)
            return 200, remove_channel(cfg, data)
        if parts == ["channel", "restore"] and method == "POST":
            check_password(cfg, headers.get("X-Kia-Password"), secure)
            return 200, put_back(cfg, data)
        if parts == ["renumber"] and method == "POST":
            check_password(cfg, headers.get("X-Kia-Password"), secure)
            return 200, renumber(cfg)
        if parts == ["channel"] and method == "PUT":
            check_password(cfg, headers.get("X-Kia-Password"), secure)
            return 200, set_channel(cfg, data)
        if parts == ["players"] and method == "GET":
            # Which pseudonym in the graphs is which Plex player: names can be personal.
            check_password(cfg, headers.get("X-Kia-Password"), secure)
            from kia.metrics import known_players

            return 200, {"players": known_players(cfg.state_dir)}
        if parts == ["sources"] and method == "GET":
            why = password_error(cfg, headers.get("X-Kia-Password"), secure)
            return 200, get_sources(cfg, secure, why is None, locked=why)
        if parts == ["sources", "check"] and method == "GET":
            return 200, {"check": check_state()}
        if parts and parts[0] == "sources" and method in ("PUT", "POST", "DELETE"):
            check_password(cfg, headers.get("X-Kia-Password"), secure)
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
            check_password(cfg, headers.get("X-Kia-Password"), secure)
            return 200, ask_stack(cfg, "check", data)
        if parts == ["stack", "apply"] and method == "POST":
            check_password(cfg, headers.get("X-Kia-Password"), secure)
            return 200, ask_stack(cfg, "apply", data)
        if parts == ["plex", "tuners"] and method == "GET":
            return 200, {"plex": plex_tuners_state()}
        if parts == ["plex", "tuners"] and method == "POST":
            check_password(cfg, headers.get("X-Kia-Password"), secure)
            return 200, start_plex_tuners(cfg)
        if parts == ["password"] and method == "POST":
            check_password(cfg, headers.get("X-Kia-Password"), secure)
            return 200, change_password(cfg, data)
        if len(parts) == 2 and parts[0] == "profiles" and method in ("PUT", "DELETE"):
            check_password(cfg, headers.get("X-Kia-Password"), secure)
            if method == "PUT":
                return 200, save_profile(cfg, parts[1], data)
            return 200, delete_profile(cfg, parts[1])
        raise ApiError(404, "Not found.")
    except ApiError as exc:
        return exc.status, {"error": str(exc)}
