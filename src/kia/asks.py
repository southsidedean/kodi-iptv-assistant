"""Requests from AI assistants, which kia carries out only once you approve them.

kia's MCP server only reads, except for a few tools that ask: test a channel's stream now,
for example. Such a tool doesn't do anything. It leaves a request here, in state/asks/,
and tells the assistant to have you approve it on the Dashboard. There, with the password
over HTTPS, you approve it (kia then runs the same code as the Dashboard's own button) or
decline it. The approval is the safety: an assistant, or whoever has its token, can ask,
but only you can make kia act.

Each request says, in kia's own words, what it will do, and is checked twice: when it's
asked for, and again just before it runs. It waits at most EXPIRES seconds, at most
WAITING are waiting at once, and one of each kind: an assistant can't pile them up.
kia.toml's [asks] section can change both numbers (config.load checks them).

You can let some requests run without you, all off until you choose to (why_auto):
kia.toml's [asks] auto_approve lists kinds, and auto_devices can narrow those to some
gateway devices; or Approve, and allow for an hour, on the Dashboard, for that kind from
that caller. Only AUTO_KINDS qualify, at most per_hour a kind, and each still becomes a
request, marked approved automatically, so the Dashboard and the assistant_asks notice
show it. The settings live in kia.toml, which kia's container can read but not write; the
hour's allowance lives in state/, needs the password to start, and lasts at most an hour.

The files are kia's own (state/ is kia's to write), named by an id kia makes, and read back
through files.read_object: one damaged into another shape counts as missing.
"""

from __future__ import annotations

import json
import math
import re
import secrets
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from kia.files import read_object, temp_name

FOLDER = "asks"  # in state/
EXPIRES = 10 * 60  # seconds a request waits for you, unless [asks] says otherwise
KEEP = 24 * 3600  # seconds an answered request stays, for the assistant to read how it went
# A request still running after this long never finished: kia restarted while it ran.
# The slowest kind, adding kia's channels to Plex, gives up after 30 minutes.
RUNNING_UP_TO = 3600
ID = re.compile(r"[0-9a-f]{16}")
# What each kind of request does, in the Dashboard's words. None of them changes your
# settings: applying a choice of parts stays on the Dashboard's Stack page.
KINDS = {
    "test_channel": "Test a channel's stream now",
    "refresh_guide": "Refresh the guide now from the quick sources",
    "add_to_plex": "Add kia's channels to Plex as Live TV now",
    "check_stack": "Check a choice of parts for the stack (nothing changes)",
}
# The kinds that may run without you: they test, refresh, or check, as kia does by itself.
# Not add_to_plex, which changes Plex's settings: that one always waits for you.
AUTO_KINDS = ("test_channel", "refresh_guide", "check_stack")
WAITING = len(KINDS)  # requests waiting at once, unless [asks] says fewer: one of each kind
AUTO_PER_HOUR = 20  # automatic approvals a kind, an hour, unless [asks] says otherwise
ALLOWED = "asks-allowed.json"  # in state/, beside asks/ (whose files must all be requests)
ALLOW_FOR = 3600  # seconds the Dashboard's "allow for an hour" lasts
# waiting for you; running once approved; done or failed after; declined; expired unanswered
STATES = ("waiting", "running", "done", "failed", "declined", "expired")
_LOCK = threading.Lock()


class AskError(Exception):
    """A request that can't be made, approved, or declined, with why in plain words."""


def _folder(state_dir: Path) -> Path:
    return state_dir / FOLDER


def _read(path: Path) -> dict | None:
    """One request, or None when the file isn't one kia wrote: every field kia reads back is
    checked, so one damaged file can't stop the others (or the Dashboard's list)."""
    r = read_object(path)
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)  # noqa: E731
    if not (ID.fullmatch(str(r.get("id", ""))) and r.get("kind") in KINDS and r.get("state") in STATES
            and isinstance(r.get("args"), dict) and isinstance(r.get("what"), str) and isinstance(r.get("who"), str)
            and number(r.get("expires")) and ("answered_at" not in r or number(r["answered_at"]))):  # fmt: skip
        return None
    return r


def _write(state_dir: Path, r: dict) -> None:
    folder = _folder(state_dir)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(folder / f"{r['id']}.json")
    tmp.write_text(json.dumps(r, indent=1, ensure_ascii=False))
    tmp.replace(folder / f"{r['id']}.json")


def _all(state_dir: Path, now: float) -> list[dict]:
    """Every request, newest first. A waiting one past its time becomes expired; answered
    ones older than KEEP are removed."""
    found = []
    for path in _folder(state_dir).glob("*.json"):
        r = _read(path)
        if r is None or path.stem != r["id"]:
            path.unlink(missing_ok=True)  # not one of kia's: it can't be approved
            continue
        if r["state"] == "waiting" and now > r["expires"]:
            r["state"] = "expired"
            _write(state_dir, r)
        elif r["state"] == "running" and now - r.get("answered_at", now) > RUNNING_UP_TO:
            r["state"], r["error"] = "failed", "It didn't finish: kia restarted while it ran."
            _write(state_dir, r)
        if r["state"] != "waiting" and now - r.get("answered_at", r["expires"]) > KEEP:
            path.unlink(missing_ok=True)
            continue
        found.append(r)
    return sorted(found, key=lambda r: r["expires"], reverse=True)


def _stamp(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).astimezone().isoformat(timespec="seconds")


def ask(state_dir: Path, kind: str, args: dict, what: str, who: str = "", now: float | None = None,
        expires: float = EXPIRES, waiting: int = WAITING, device: str = "") -> dict:  # fmt: skip
    """A new request, waiting for you; or the one of the same kind already waiting. Raises
    AskError when `waiting` are waiting. expires: seconds it waits; device: the gateway
    device that asked, if one did."""
    now = time.time() if now is None else now
    with _LOCK:
        pending = [r for r in _all(state_dir, now) if r["state"] == "waiting"]
        same = next((r for r in pending if r["kind"] == kind), None)
        if same is not None:
            return {**same, "already": True}
        if len(pending) >= waiting:
            many = "1 request is" if len(pending) == 1 else f"{len(pending)} requests are"
            raise AskError(f"{many} already waiting for the person to approve on the Dashboard.")
        r = {"id": secrets.token_hex(8), "kind": kind, "args": args, "what": what, "who": who or "an assistant",
             "state": "waiting", "asked": _stamp(now), "expires": now + expires}  # fmt: skip
        if device:
            r["device"] = device
        _write(state_dir, r)
        return r


def listing(state_dir: Path, now: float | None = None) -> list[dict]:
    """Every request kept, waiting ones first, for the Dashboard."""
    now = time.time() if now is None else now
    with _LOCK:
        found = _all(state_dir, now)
    return sorted(found, key=lambda r: r["state"] != "waiting")


def get(state_dir: Path, ask_id: str, now: float | None = None) -> dict | None:
    """One request by its id, or None."""
    if not ID.fullmatch(ask_id or ""):
        return None
    now = time.time() if now is None else now
    with _LOCK:
        return next((r for r in _all(state_dir, now) if r["id"] == ask_id), None)


def take(state_dir: Path, ask_id: str, approve: bool, now: float | None = None, auto: str = "") -> dict:
    """Answer a waiting request: it's running once approved (finish runs it), or declined.
    Raises AskError for one that isn't waiting any more. auto: why it was approved without
    you, kept with it."""
    now = time.time() if now is None else now
    with _LOCK:
        r = next((x for x in _all(state_dir, now) if x["id"] == ask_id), None) if ID.fullmatch(ask_id or "") else None
        if r is None:
            raise AskError("There's no such request.")
        if r["state"] != "waiting":
            raise AskError(f"That request is {r['state']} already.")
        # Marked before it runs, so a second click can't run it twice.
        r["state"], r["answered_at"] = ("running" if approve else "declined"), now
        if auto:
            r["auto"] = auto
        _write(state_dir, r)
    return r


def finish(state_dir: Path, r: dict, run: Callable[[str, dict], dict], now: float | None = None) -> dict:
    """Run an approved request, and keep how it went. run(kind, args) is kia's own action,
    which checks the arguments again."""
    try:
        r["result"], r["state"] = run(r["kind"], r["args"]), "done"
    except Exception as exc:  # what went wrong, for the person and the assistant
        r["error"], r["state"] = str(exc)[:300] or type(exc).__name__, "failed"
    r["answered_at"] = time.time() if now is None else now
    with _LOCK:
        _write(state_dir, r)
    return r


def answer(state_dir: Path, ask_id: str, approve: bool, run: Callable[[str, dict], dict],
           now: float | None = None) -> dict:  # fmt: skip
    """take and finish in one go: approve (run it, and keep how it went) or decline."""
    r = take(state_dir, ask_id, approve, now)
    return finish(state_dir, r, run, now) if approve else r


def _allowances(state_dir: Path, now: float) -> list[dict]:
    """The Dashboard's allowances still running. The file is in state/, so each entry is
    checked: a kind that may run without you, the caller as kia names them, and an end no
    more than an hour away."""
    found = read_object(state_dir / ALLOWED).get("allowed")
    out = []
    for a in found if isinstance(found, list) else []:
        if (isinstance(a, dict) and a.get("kind") in AUTO_KINDS and isinstance(a.get("who"), str)
                and 0 < len(a["who"]) <= 100 and isinstance(a.get("until"), (int, float))
                and now < a["until"] <= now + ALLOW_FOR):  # fmt: skip
            out.append({"kind": a["kind"], "who": a["who"], "until": a["until"]})
    return out


def allowed(state_dir: Path, now: float | None = None) -> list[dict]:
    """What the Dashboard allows for now: each kind and caller, and minutes left."""
    now = time.time() if now is None else now
    with _LOCK:
        found = _allowances(state_dir, now)
    return [{"kind": a["kind"], "who": a["who"], "minutes_left": max(1, round((a["until"] - now) / 60))} for a in found]


def _save_allowances(state_dir: Path, found: list[dict]) -> None:
    path = state_dir / ALLOWED
    if not found:
        path.unlink(missing_ok=True)
        return
    tmp = temp_name(path)
    tmp.write_text(json.dumps({"allowed": found}, indent=1, ensure_ascii=False))
    tmp.replace(path)


def allow(state_dir: Path, kind: str, who: str, now: float | None = None) -> None:
    """Let this kind from this caller run without you for the next hour. Raises AskError for a
    kind that always waits for you."""
    if kind not in AUTO_KINDS:
        raise AskError(f"{KINDS.get(kind, 'That')}: kia always asks you about this one.")
    now = time.time() if now is None else now
    with _LOCK:
        found = [a for a in _allowances(state_dir, now) if (a["kind"], a["who"]) != (kind, who)]
        _save_allowances(state_dir, [*found, {"kind": kind, "who": who, "until": now + ALLOW_FOR}])


def stop_allowing(state_dir: Path) -> None:
    """End every allowance now: requests wait for you again."""
    with _LOCK:
        _save_allowances(state_dir, [])


def why_auto(state_dir: Path, kind: str, who: str, device: str, kinds: tuple[str, ...] = (),
             devices: tuple[str, ...] = (), per_hour: int = AUTO_PER_HOUR, now: float | None = None) -> str:  # fmt: skip
    """Why a new request may run without you, in the Dashboard's words, or "" when it waits.
    kinds and devices: kia.toml's auto_approve and auto_devices (devices empty: any caller);
    device: the gateway device that asked, "" otherwise. A kind past per_hour automatic
    approvals in the last hour waits for you, so an assistant stuck in a loop stops there."""
    if kind not in AUTO_KINDS:
        return ""
    now = time.time() if now is None else now
    with _LOCK:
        recent = sum(1 for r in _all(state_dir, now)
                     if r["kind"] == kind and r.get("auto") and now - r.get("answered_at", 0) < 3600)  # fmt: skip
        if recent >= per_hour:
            return ""
        if kind in kinds and (not devices or device in devices):
            return "kia.toml's auto_approve" + (f" for {device}" if devices else "")
        if any((a["kind"], a["who"]) == (kind, who) for a in _allowances(state_dir, now)):
            return "allowed for an hour on the Dashboard"
    return ""
