"""Choosing playlists on the Dashboard.

The sources file (sources.txt, or your own copy in KIA_SOURCES_FILE) stays the place for
choices made by hand. With Docker, kia can't write to it, so choices made on the status
page are kept in state/sources.json and applied on top of it at every run:

- "on": playlists to use that the file lists but leaves turned off (a "#" line)
- "off": playlists the file uses that the Dashboard turned off
- "added": playlists added on the Dashboard, with their name and whether they're radio

The page lists every playlist kia uses, and every one the sources file and sources.txt
offer turned off, grouped by the file's "# --- Section ---" headings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from kia.config import Source, is_station_line, parse_sources

STATE_FILE = "sources.json"
_SECTION_RE = re.compile(r"^#\s*-{2,}\s*(.+?)\s*-{2,}")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_PLAYLIST_RE = re.compile(r"^(https?://\S+|(radio-browser|somafm|kexp):.*)$", re.I)


@dataclass
class Offered:
    """A playlist in a sources file, whether it's turned on there or not."""

    url: str
    name: str
    radio: bool
    label: str  # the comment after it, e.g. "Canada  264"
    section: str
    on: bool  # turned on in the file


def _split_comment(line: str) -> tuple[str, str]:
    """('url name radio', 'the comment') for one sources line."""
    m = re.search(r"\s#\s*(.*)$", line)
    return (line[: m.start()].strip(), m.group(1).strip()) if m else (line.strip(), "")


def offered(text: str) -> list[Offered]:
    """Every playlist line in a sources file, on or off. A turned-off line is a "#" line
    whose text after the "#" is a playlist (https://...) or a station line (radio-browser:,
    somafm:, kexp:)."""
    out: list[Offered] = []
    section = ""
    taken: set[str] = set()
    for raw in text.removeprefix("\ufeff").splitlines():
        line = raw.strip()
        m = _SECTION_RE.match(line)
        if m:
            section = re.sub(r"\s+streams$", "", m.group(1)).strip()
            continue
        on = not line.startswith("#")
        body = line if on else line.lstrip("#").strip()
        if not body or (not on and not (re.match(r"https?://", body, re.I) or is_station_line(body))):
            continue  # a blank line, or a comment that isn't a playlist
        spec, label = _split_comment(body)
        try:
            parsed = parse_sources(spec)
        except ValueError:
            continue
        if not parsed:
            continue
        s = parsed[0]
        name = s.name
        n = 2
        while name in taken:
            name, n = f"{s.name}-{n}", n + 1
        taken.add(name)
        out.append(Offered(s.url, name, s.radio or s.is_stations, re.sub(r"\s{2,}", "  ", label),
                           section, on))  # fmt: skip
    return out


def load_state(state_dir: Path) -> dict:
    try:
        data = json.loads((state_dir / STATE_FILE).read_text())
    except (OSError, ValueError):
        data = {}
    return {"on": list(data.get("on", [])), "off": list(data.get("off", [])), "added": list(data.get("added", []))}


def save_state(state_dir: Path, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n")
    tmp.replace(state_dir / STATE_FILE)


def _key(url: str) -> str:
    return url.strip()


def apply(sources: list[Source], offers: list[Offered], state: dict) -> list[Source]:
    """The sources file's playlists with the Dashboard's choices applied."""
    off = {_key(u) for u in state["off"]}
    out = [s for s in sources if _key(s.url) not in off]
    have = {_key(s.url) for s in out}
    names = {s.name for s in out}

    def add(spec: str, name: str | None, radio: bool) -> None:
        line = spec + (f" {name}" if name and not is_station_line(spec) else "") + (" radio" if radio else "")
        if is_station_line(spec) and name:
            line = f"{spec} name={name}"
        try:
            parsed = parse_sources(line)
        except ValueError:
            return
        for s in parsed:
            if _key(s.url) in have or _key(spec) in have:
                continue
            base, n = s.name, 2
            while s.name in names:
                s.name, n = f"{base}-{n}", n + 1
            out.append(s)
            have.add(_key(s.url))
            names.add(s.name)

    by_url = {_key(o.url): o for o in offers}
    for url in state["on"]:
        o = by_url.get(_key(url))
        if o and _key(url) not in off:
            add(o.url, o.name, o.radio)
    for a in state["added"]:
        if _key(a.get("url", "")) not in off:
            add(a["url"], a.get("name"), bool(a.get("radio")))
    return out


def listing(sources_text: str, catalog_text: str | None, state: dict, counts: dict[str, dict]) -> list[dict]:
    """Everything for the Dashboard: each playlist with where it comes from, whether
    it's in use, and its streams from the last check (by name)."""
    offers = offered(sources_text)
    seen = {_key(o.url) for o in offers}
    if catalog_text:
        for o in offered(catalog_text):
            if _key(o.url) not in seen:
                o.on = False  # in sources.txt, but not in your own copy
                offers.append(o)
                seen.add(_key(o.url))
    on = {_key(u) for u in state["on"]}
    off = {_key(u) for u in state["off"]}
    rows = []
    for o in offers:
        k = _key(o.url)
        used = (o.on or k in on) and k not in off
        rows.append({"url": o.url, "name": o.name, "label": o.label, "section": o.section or "Playlists",
                     "radio": o.radio, "on": used, "origin": "file", "changed": k in on or k in off,
                     "streams": counts.get(o.name)})  # fmt: skip
    for a in state["added"]:
        k = _key(a["url"])
        if k in seen:
            continue
        rows.append({"url": a["url"], "name": a.get("name") or "", "label": "", "section": "Added on the Dashboard",
                     "radio": bool(a.get("radio")), "on": k not in off, "origin": "added", "changed": True,
                     "streams": counts.get(a.get("name") or "")})  # fmt: skip
    return rows


def set_on(state: dict, url: str, on: bool, in_file: bool, known: bool) -> dict:
    """Turn a playlist on or off. in_file: the sources file turns it on itself."""
    k = _key(url)
    state["on"] = [u for u in state["on"] if _key(u) != k]
    state["off"] = [u for u in state["off"] if _key(u) != k]
    added = any(_key(a["url"]) == k for a in state["added"])
    if not (known or added):
        raise ValueError("kia doesn't know that playlist. Add it first.")
    if on and not in_file and not added:
        state["on"].append(url)
    elif not on and (in_file or added):
        state["off"].append(url)
    return state


def check_new(url: str, name: str, taken: set[str]) -> tuple[str, str]:
    """Check a playlist someone adds on the Dashboard: (url, name)."""
    url = url.strip()
    name = name.strip().lower()
    if not _PLAYLIST_RE.match(url) or (" " in url and not is_station_line(url)):
        raise ValueError("Enter a playlist address starting with http:// or https://.")
    if len(url) > 2000:
        raise ValueError("That address is too long.")
    if name and not _NAME_RE.match(name):
        raise ValueError("Use lowercase letters, numbers, and dashes for the name, up to 40.")
    try:
        parsed = parse_sources(url + (f" name={name}" if name and is_station_line(url) else f" {name}" if name else ""))
    except ValueError as exc:
        raise ValueError(str(exc)) from None
    name = name or parsed[0].name
    if name in taken:
        raise ValueError(f"There's already a playlist named {name!r}. Choose another name.")
    return url, name


def add(state: dict, url: str, name: str, radio: bool) -> dict:
    k = _key(url)
    state["added"] = [a for a in state["added"] if _key(a["url"]) != k] + [{"url": url, "name": name, "radio": radio}]
    state["off"] = [u for u in state["off"] if _key(u) != k]
    return state


def remove(state: dict, url: str) -> dict:
    k = _key(url)
    if not any(_key(a["url"]) == k for a in state["added"]):
        raise ValueError("Only playlists added on the Dashboard can be removed here.")
    state["added"] = [a for a in state["added"] if _key(a["url"]) != k]
    state["off"] = [u for u in state["off"] if _key(u) != k]
    return state
