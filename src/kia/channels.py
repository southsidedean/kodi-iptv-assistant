"""Favorites and channel numbers chosen on the Dashboard.

Kept in state/channels.json:

- "favorites": channel keys. Favorites get the category "Favorites" (players show it as
  a group, and profiles can pick it) and come first in every playlist.
- "removed": {key: {"when", "name", "entry", "good_nights"}}: channels taken out of every
  playlist from the Dashboard because they don't play. The nightly check puts one back
  after its stream works BACK_AFTER_NIGHTS nights in a row ("good_nights" counts them);
  "entry" (its playlist lines) lets the page put it back by hand.
- "numbers": {"tv": {key: n}, "radio": {key: n}}. A number chosen here pins the channel
  like a tvg-chno in your own playlist file does (kia.numbers); a pin in a file wins.

The check applies them every night, and kia.api applies them straight away by rewriting
the playlists from the last check.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from kia import m3u
from kia.m3u import Entry
from kia.numbers import ChannelNumbers
from kia.files import temp_name

STATE_FILE = "channels.json"
FAVORITES = "Favorites"
MAX_NUMBER = 99999
# A removed channel comes back on its own after its stream works this many nights in a row.
# kia's check only opens the stream, and some streams that open still don't play well, so
# one good night isn't enough to undo your choice.
BACK_AFTER_NIGHTS = 3


def load(state_dir: Path) -> dict:
    try:
        data = json.loads((state_dir / STATE_FILE).read_text())
    except (OSError, ValueError):
        data = {}
    numbers = data.get("numbers", {})
    return {"favorites": list(data.get("favorites", [])), "removed": dict(data.get("removed", {})),
            "numbers": {"tv": dict(numbers.get("tv", {})), "radio": dict(numbers.get("radio", {}))}}  # fmt: skip


def save(state_dir: Path, choices: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(state_dir / STATE_FILE)
    tmp.write_text(json.dumps(choices, indent=1, sort_keys=True) + "\n")
    tmp.replace(state_dir / STATE_FILE)


def _groups(e: Entry) -> list[str]:
    return [g.strip() for g in e.group.split(";") if g.strip() and g.strip() != FAVORITES]


def apply(entries: list[Entry], choices: dict) -> list[Entry]:
    """Pin chosen numbers and tag favorites. Returns the entries in playlist order: TV,
    then radio, each with favorites first and the rest by name."""
    favorites = set(choices["favorites"])
    for e in entries:
        kind = "radio" if e.is_radio else "tv"
        chosen = choices["numbers"][kind].get(e.key)
        if chosen is not None and e.pinned_number is None:
            e.pinned_number = int(chosen)
            e.pinned_by = "page"
        groups = _groups(e)
        if e.key in favorites:
            groups.append(FAVORITES)
        if groups:
            e.attrs["group-title"] = ";".join(groups)
        else:
            e.attrs.pop("group-title", None)
    return sorted(entries, key=lambda e: (e.is_radio, e.key not in favorites, e.name.casefold()))


def count_good_nights(choices: dict, statuses: dict[str, str]) -> list[str]:
    """After a full nightly check: count another good night for each removed channel whose
    stream worked, start again for the rest. Returns the keys due to come back."""
    back = []
    for key, rec in choices["removed"].items():
        if statuses.get(key) == "ok":
            rec["good_nights"] = rec.get("good_nights", 0) + 1
            if rec["good_nights"] >= BACK_AFTER_NIGHTS:
                back.append(key)
        else:
            rec["good_nights"] = 0
    return back


def removal(e: Entry) -> dict:
    """What "removed" keeps of a channel, so it can be put back."""
    return {"when": datetime.now(timezone.utc).isoformat(timespec="seconds"), "name": e.name,
            "entry": m3u.write([e]).split("\n", 1)[1]}  # fmt: skip


def rebuild(output_dir: Path, state_dir: Path, epg_url: str | None, log=lambda m: None,
            renumber: bool = False, recycle_after_days: int = 0, add: list[Entry] | None = None,
            numbered: bool = True) -> list[Entry]:  # fmt: skip
    """Apply the choices to the playlist from the last check, now, and write it again.
    renumber: number everything 1, 2, 3 in playlist order first (kia renumber). The caller
    rebuilds the profiles."""
    curated = output_dir / "curated.m3u"
    entries = m3u.parse(curated.read_text()) if curated.exists() else []
    known = {e.key for e in entries}
    entries += [e for e in add or [] if e.key not in known]
    numbers = ChannelNumbers(state_dir / "channel-numbers.json", recycle_after_days)
    for e in entries:
        # Pins from your playlist files stay pins (the playlist only has the number).
        rec = numbers.numbers.get("radio" if e.is_radio else "tv", {}).get(e.key)
        # Page choices come back from channels.json below, so a moved or cleared one is gone.
        if rec and rec.get("pinned") in (True, "file"):
            e.pinned_number = rec["number"]
    choices = load(state_dir)
    entries = apply([e for e in entries if e.key not in choices["removed"]], choices)
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not numbered:
        pass  # channel_numbers = false in kia.toml: the playlist has no numbers to keep
    elif renumber:
        # Playlist order: TV, then radio, each with favorites first and the rest by name.
        favorites = set(choices["favorites"])
        entries.sort(key=lambda e: (e.is_radio, e.key not in favorites, e.name.casefold()))
        numbers.renumber(entries, when, log)
    else:
        numbers.assign(entries, when, log)
    if numbered:
        numbers.save()
    tmp = temp_name(curated)
    tmp.write_text(m3u.write(entries, epg_url))
    tmp.replace(curated)
    return entries


def set_choice(choices: dict, key: str, kind: str, favorite: bool | None = None, number: object = "keep") -> dict:
    """Change one channel's choices. number: an int, None to clear it, or "keep"."""
    if favorite is not None:
        choices["favorites"] = [k for k in choices["favorites"] if k != key] + ([key] if favorite else [])
    if number != "keep":
        if number is None:
            choices["numbers"][kind].pop(key, None)
        else:
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= MAX_NUMBER:
                raise ValueError(f"A channel number is a whole number from 1 to {MAX_NUMBER}.")
            taken = [k for k, n in choices["numbers"][kind].items() if n == number and k != key]
            for k in taken:
                del choices["numbers"][kind][k]  # the newest choice wins
            choices["numbers"][kind][key] = number
    return choices
