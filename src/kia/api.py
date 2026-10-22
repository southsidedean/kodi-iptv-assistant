"""The Dashboard's profile editor: a small API behind /api/.

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

import json
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

from kia import channels, locks, profiles
from kia import sources as web_sources
from kia.auth import Auth
from kia.m3u import parse

# One change at a time, and none while kia check rebuilds the profiles.
# Writing kia's state: shared with a refresh, even one in another process (kia.locks).
LOCK = locks.FilesLock()
PASSWORD_LOCK = threading.Lock()


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


def _profile_from(body: dict, name: str = "preview") -> profiles.Profile:
    if not isinstance(body, dict):
        raise ApiError(400, "Expected the profile's settings as a JSON object.")
    settings = {k: v for k, v in body.items() if k != "name"}
    try:
        return profiles.from_rules(name, settings, "the Dashboard")
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None


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


def set_channel(cfg: ApiConfig, body: dict) -> dict:
    if not isinstance(body, dict) or not isinstance(body.get("key"), str):
        raise ApiError(400, "Expected the channel's key.")
    favorite = body.get("favorite")
    if favorite is not None and not isinstance(favorite, bool):
        raise ApiError(400, "favorite is true or false.")
    number = body.get("number", "keep")
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


def stream_url(url: str) -> str:
    """The stream to test: Pluto channels' own address rather than kia's relay for them."""
    from kia import pluto

    m = re.search(r"/pluto/([0-9a-f]{24})\.m3u8$", url)
    return pluto.FALLBACK_URL.format(channel=m.group(1)) if m else url


def test_channel(cfg: ApiConfig, body: dict) -> dict:
    """Try a channel's stream now, the way the nightly check does."""
    import asyncio

    from kia.cli import is_stream_url
    from kia.config import DEFAULT_USER_AGENT
    from kia.probe import probe

    entry = _entry(cfg, body)
    if not is_stream_url(stream_url(entry.url)):  # as the nightly check: only stream addresses
        raise ApiError(400, "That channel's address isn't a stream kia can test.")
    r = asyncio.run(probe(stream_url(entry.url), timeout=20, user_agent=entry.user_agent or DEFAULT_USER_AGENT,
                          referrer=entry.referrer, require_video=not entry.is_radio))  # fmt: skip
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
    from kia.m3u import parse

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


def get_sources(cfg: ApiConfig, secure: bool = False, unlocked: bool = False) -> dict:
    """The channel sources. Addresses can carry a provider's user name and password, so
    they're shown in full only with kia's password, on the secure page."""
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
    }


def check_state(unlocked: bool = False) -> dict:
    state = dict(CHECK)
    if "url" in state and not unlocked:
        state["url"] = mask_url(state["url"])
    return state


def password_ok(cfg: ApiConfig, given: str | None, secure: bool) -> bool:
    """check_password without the error: for reading what only the password may show."""
    if not given:
        return False
    try:
        check_password(cfg, given, secure)
    except ApiError:
        return False
    return True


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
        web_sources.add(state, url, name, bool(body.get("radio")))
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
    """An address with any user name, password, or query hidden."""
    from urllib.parse import urlsplit, urlunsplit

    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:  # not an address, or a port like 99999
        return "•••"
    if not u.scheme.startswith("http"):
        return url
    host = u.hostname or ""
    netloc = ("•••@" if u.username or u.password else "") + host + (f":{port}" if port else "")
    return urlunsplit((u.scheme, netloc, u.path, "•••" if u.query else "", ""))


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
    line = f"{url} name={row['name']}" if url.startswith("radio-browser:") else f"{url} {row['name']}"
    if row["radio"] and not url.startswith("radio-browser:"):
        line += " radio"
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


def stop_check() -> dict:
    with LOCK:
        proc = CHECK_PROC.get("proc")
        if not CHECK.get("running"):
            raise ApiError(409, "No check is running.")
        CHECK["stopped"] = True  # one that's only starting ends as soon as it has started
    if proc is not None:
        _end(proc)
    return {"check": check_state(unlocked=True)}


def check_password(cfg: ApiConfig, given: str | None, secure: bool) -> None:
    if cfg.auth.source == "off":
        raise ApiError(403, "Editing is off (KIA_EDITING=false in kia's .env file).")
    if not secure:
        raise ApiError(403, "Changes are accepted only on the secure (https://) Dashboard.")
    with PASSWORD_LOCK:  # one check at a time: each costs 16 MB and a second when wrong
        if not cfg.auth.check(given):
            time.sleep(1)  # slows down guessing
            raise ApiError(401, "Wrong password.")


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


def handle(cfg: ApiConfig, method: str, path: str, headers, body: bytes, secure: bool = False) -> tuple[int, dict]:
    """Route one /api/ request. Returns (HTTP status, JSON body). secure: it came over HTTPS."""
    url = urlparse(path)
    parts = [p for p in url.path.split("/") if p][1:]  # after "api"
    origin = headers.get("Origin")
    host = headers.get("Host")
    try:
        if origin and urlparse(origin).netloc != host:
            raise ApiError(403, "Requests from other websites aren't allowed.")
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
            return 200, get_sources(cfg, secure, password_ok(cfg, headers.get("X-Kia-Password"), secure))
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
