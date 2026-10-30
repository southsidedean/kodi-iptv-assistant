"""Web asking media to do the work that needs the stream tools (0.9 privilege separation).

0.9 splits the one kia container into a serving half (`daemon-web`) and a media half
(`daemon-media`). The serving half must never run ffprobe or ffmpeg on untrusted streams, so
when the Dashboard asks for work that does (refresh the guide now, test a channel), web leaves a
request here and media picks it up. It's the same shape as kia asking the host stack service for
a Docker change (kia.stack): the two halves talk only through files, and each checks the request
again before trusting it.

  state/media-request        web writes it: id, kind, and the kind's own fields. media renames
                             it into its own folder, so a request is taken exactly once.
  media-service/request      the request media is working on (the renamed file)
  media-service/result       media's answer: id, kind, state, exit, finished, then a blank line
                             and anything to show

media-service/ belongs to media; web reads the answer there but writes nothing media reads back
(the same one-way shape stack-service/ has). The request is plain key=value, one line each,
strictly checked, and never run as a command. A request can carry a playlist address, which may
hold a login (`?username=&password=` or `/user/pass/`), so the file is written readable by its
owner only; the two halves run as the same user, so media can still read it. media ignores a
request older than REQUEST_EXPIRES, so one nobody is waiting for any more doesn't run later.

This module is the transport only: `ask` (web leaves a request), `take` (media claims and parses
one), and `answer` (media writes its result), plus `waiting`/`result` for the page to poll. What
each kind actually does is the caller's handler, so the stream tools stay out of this file and
out of the web half. media re-checks a `check` address against the known sources before acting,
exactly as the Dashboard's "Check now" does today, so an unknown one is refused.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
import time
from pathlib import Path

from kia.files import temp_name

# The work that crosses the boundary, i.e. the work that runs the stream tools. Guide and Plex
# steps that need only a token or the network stay on the web half; only these reach media.
KINDS = ("refresh-guide", "check")

REQUEST = "media-request"  # in state/, written by web
SERVICE_DIR = "media-service"  # media's folder, read-only to web
TAKEN, RESULT = "request", "result"
REQUEST_EXPIRES = 300  # seconds: media won't start a request older than this
RUNNING_UP_TO = 1800  # a request still being worked on after this long counts as stuck
MAX_OUTPUT = 20_000  # characters of a handler's output the answer keeps

# A check address as it reaches here: one line, no control characters, length-capped. This is
# the transport's own guard, so the file stays a safe, single-line value; media still looks the
# address up in the known sources before it tests anything, which is the real check.
_URL = re.compile(r"[^\x00-\x1f\x7f]{1,2048}")


class MediaError(ValueError):
    """A request the web half won't leave, or one already waiting for media."""


def _validate(kind: str, url: str | None) -> dict[str, str]:
    """The fields a kind carries, each checked. Raises MediaError on anything unexpected. Both
    halves call this: web before it leaves a request, so a mistake shows on the Dashboard at
    once, and media again when it takes one."""
    if kind not in KINDS:
        raise MediaError(f"kia doesn't know the media request {kind!r}.")
    if kind == "check":
        if not isinstance(url, str) or not _URL.fullmatch(url):
            raise MediaError("A check needs the playlist's address, on one line.")
        return {"url": url}
    if url is not None:
        raise MediaError(f"The {kind} request takes no address.")
    return {}


def _write_request(path: Path, lines: list[str]) -> None:
    """Write the request whole, at once, readable by its owner only (it can hold a login), so
    media never reads half of one. A leftover temp file from a crash goes first: the temp name
    can repeat after a restart with the same process id."""
    tmp = temp_name(path)
    tmp.unlink(missing_ok=True)
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        f.write("\n".join(lines) + "\n")
    tmp.replace(path)


def ask(state_dir: Path, service_dir: Path, kind: str, *, url: str | None = None) -> str:
    """Web leaves a request for media. Returns its id, which media's answer carries back, so the
    page knows the answer is to this request. Refuses a new one while media is still working on
    the last, so the single slot can't be overwritten unseen."""
    fields = _validate(kind, url)
    if waiting(state_dir, service_dir) is not None:
        raise MediaError("kia is still working on the last request. Wait for its answer first.")
    rid = secrets.token_hex(8)
    state_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"id={rid}", f"kind={kind}", *(f"{k}={v}" for k, v in fields.items())]
    _write_request(state_dir / REQUEST, lines)
    return rid


def _parse(text: str) -> dict[str, str]:
    """A request file's fields: id, kind, and url, each capped. Keys it doesn't know are left
    out, so a longer or reshaped file still parses to just what a kind can carry."""
    out: dict[str, str] = {}
    for line in text.splitlines()[:10]:
        key, sep, value = line.partition("=")
        if sep and key in ("id", "kind", "url"):
            out[key] = value[:2048]
    return out


def _age(path: Path) -> float | None:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def _request_id(path: Path) -> str:
    try:
        return _parse(path.read_text(errors="replace")).get("id", "")
    except OSError:
        return ""


def waiting(state_dir: Path, service_dir: Path) -> str | None:
    """The id of a request still current: one web left that media hasn't taken (for up to
    REQUEST_EXPIRES), or one media is working on. None when the slot is free."""
    left = _age(state_dir / REQUEST)
    if left is not None and left < REQUEST_EXPIRES:
        return _request_id(state_dir / REQUEST)
    taken = _age(service_dir / TAKEN)
    if taken is not None and taken < RUNNING_UP_TO:
        return _request_id(service_dir / TAKEN)
    return None


def take(state_dir: Path, service_dir: Path) -> dict[str, str] | None:
    """media claims the waiting request, if there is a fresh one, by renaming it into its own
    folder, so a request is taken exactly once. Returns its checked fields ({"id", "kind", and
    for a check "url"}), or None when there's nothing fresh and good to do. The caller runs the
    work and calls `answer`, which frees the slot. A request that's too old, isn't a plain file,
    or fails its checks is dropped here instead, never run (a bad kind gets an answer saying so,
    so the page learns why; junk is dropped silently)."""
    path = state_dir / REQUEST
    age = _age(path)
    if age is None:
        return None
    service_dir.mkdir(parents=True, exist_ok=True)
    taken = service_dir / TAKEN
    try:
        os.replace(path, taken)  # claim it atomically: a second media turn finds nothing here
    except OSError:
        return None
    # Check again now it's claimed: web could have swapped a link in after the age check above,
    # and a rename moves a link as the link, so this catches one before anything reads it.
    try:
        if not stat.S_ISREG(taken.lstat().st_mode):
            taken.unlink(missing_ok=True)
            return None
    except OSError:
        return None
    if age >= REQUEST_EXPIRES:
        taken.unlink(missing_ok=True)  # nobody is waiting for it any more; free the slot, don't run
        return None
    fields = _parse(taken.read_text(errors="replace"))
    rid, kind = fields.get("id", ""), fields.get("kind", "")
    if not re.fullmatch(r"[0-9a-f]{1,64}", rid):  # ids are secrets.token_hex; anything else is junk
        taken.unlink(missing_ok=True)
        return None
    try:
        checked = _validate(kind, fields.get("url"))
    except MediaError as exc:
        answer(service_dir, rid, kind, state="failed", exit_code=2, output=str(exc))
        return None
    return {"id": rid, "kind": kind, **checked}


def answer(service_dir: Path, rid: str, kind: str, *, state: str, exit_code: int, output: str) -> None:
    """media writes its result where web can read it, then frees the slot by removing the claimed
    request, so the next one can be left. state is "done" or "failed"; output is whatever the
    handler produced, already with any logins hidden, kept to MAX_OUTPUT."""
    service_dir.mkdir(parents=True, exist_ok=True)
    head = (f"id={rid[:64]}\nkind={kind[:64]}\nstate={state[:16]}\n"
            f"exit={int(exit_code)}\nfinished={int(time.time())}\n")
    _write_request(service_dir / RESULT, [head + "\n" + output[-MAX_OUTPUT:]])
    (service_dir / TAKEN).unlink(missing_ok=True)  # work's done: free the slot


def result(service_dir: Path) -> dict[str, str] | None:
    """media's answer to the last request, or None before the first one. The page matches its id
    against the one `ask` returned, so it knows the answer is to the request it's waiting on."""
    try:
        text = (service_dir / RESULT).read_text(errors="replace")
    except OSError:
        return None
    head, _, said = text.partition("\n\n")
    fields: dict[str, str] = {}
    for line in head.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in ("id", "kind", "state", "exit", "finished"):
            fields[key] = value[:64]
    fields["output"] = said[-MAX_OUTPUT:]
    return fields
