"""The status page's profile editor: a small API behind /api/.

Reading is open to anyone who can open the status page. Changes (profiles, the password)
need the admin password (kia.auth), sent as the X-Kia-Password header, and are accepted
only over HTTPS, so the password never crosses the network in the clear. Requests from
other websites are refused: browsers send a custom header like X-Kia-Password only after
asking the server first (a CORS preflight), which this API never allows, and any Origin
header must match the page's own address.

The profiles stay in the profiles file. Saving rewrites that file, then rebuilds the
profile playlists and the status page from the last check, which takes about a second.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kia import profiles
from kia.auth import Auth
from kia.m3u import parse

# One change at a time, and none while kia check rebuilds the profiles.
LOCK = threading.Lock()


@dataclass
class ApiConfig:
    output_dir: Path
    state_dir: Path
    profiles_file: Path
    epg_url: str | None
    auth: Auth
    https_port: int | None = None  # the secure status page's port, for the "open it" link
    on_change: Callable[[], None] = lambda: None  # e.g. rewrite the status page


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
        raise ApiError(409, f"The profiles file has a mistake, so it can't be edited here until it's fixed: {exc}")


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
        "groups": [{"name": g, "count": n} for g, n in sorted(groups.items(), key=lambda kv: kv[0].casefold())],
        "sources": [{"name": s, "count": n} for s, n in sorted(playlists.items())],
        "total": len(items),
    }


def preview(cfg: ApiConfig, body: dict) -> dict:
    """How many channels a profile with these settings would have, and a few of them."""
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
        return profiles.from_rules(name, settings, "the status page")
    except ValueError as exc:
        raise ApiError(400, str(exc))


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


def check_password(cfg: ApiConfig, given: str | None, secure: bool) -> None:
    if cfg.auth.source == "off":
        raise ApiError(403, "Editing is off (KIA_EDITING=false in kia's .env file).")
    if not secure:
        raise ApiError(403, "Changes are accepted only on the secure (https://) status page.")
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
            raise ApiError(400, str(exc))
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
                raise ApiError(400, "The request wasn't valid JSON.")
        if parts == ["profiles"] and method == "GET":
            return 200, get_profiles(cfg, secure)
        if parts == ["preview"] and method == "POST":
            return 200, preview(cfg, data)
        if parts == ["channels"] and method == "GET":
            return 200, search_channels(cfg, parse_qs(url.query).get("q", [""])[0])
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
