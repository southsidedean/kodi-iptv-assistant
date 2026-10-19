"""Channel numbers that stay the same from night to night.

Kodi numbers channels by their place in the playlist unless the playlist gives numbers
(tvg-chno). Without them, one stream dropping out or a new playlist renumbers everything
after it. kia gives each channel a number the first time it's kept and remembers it, so a
channel keeps its number, and one that comes back after a few bad nights gets its old one.
TV and radio are numbered separately, as Kodi shows them separately.

A tvg-chno in one of your own playlist files pins that channel's number; the automatic
numbering works around pinned numbers. cli.load_entries marks those pins; numbers in
playlists from the internet are ignored, so they can't reshuffle yours.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia.m3u import Entry


def pinned_number(entry: Entry) -> int | None:
    return entry.pinned_number


class ChannelNumbers:
    def __init__(self, path: Path):
        self.path = path
        data = {}
        if path.exists():
            data = json.loads(path.read_text())
        # numbers[kind][channel key] = {"number": n, "last_seen": iso time, "pinned": bool}
        self.numbers: dict[str, dict[str, dict]] = data.get("numbers", {"tv": {}, "radio": {}})
        # Automatic numbers are never reused, even after a channel is forgotten, so an old
        # number never points at a different channel.
        self.next: dict[str, int] = data.get("next", {"tv": 1, "radio": 1})

    def assign(self, entries: list[Entry], when: str, log: Callable[[str], None] = lambda m: None) -> None:
        """Set tvg-chno on each entry: its pinned number, its remembered one, or the next free one."""
        kinds = {"tv": [e for e in entries if not e.is_radio], "radio": [e for e in entries if e.is_radio]}
        for kind, group in kinds.items():
            known = self.numbers.setdefault(kind, {})
            pins: dict[int, str] = {}
            for e in group:
                n = pinned_number(e)
                if n is None:
                    continue
                if n in pins:
                    log(f"Channel number {n} is set for both {pins[n]!r} and {e.key!r}; numbering {e.key!r} automatically")
                    e.pinned_number = None
                    continue
                pins[n] = e.key
                known[e.key] = {"number": n, "pinned": True}
            # A channel that held a number now pinned to another channel gets a new one.
            for key, rec in list(known.items()):
                if rec["number"] in pins and pins[rec["number"]] != key:
                    log(f"Channel number {rec['number']} is now pinned to {pins[rec['number']]!r}; renumbering {key!r}")
                    del known[key]
            taken = {rec["number"] for rec in known.values()}
            for e in group:
                rec = known.get(e.key)
                if rec is not None and rec.get("pinned") and pinned_number(e) is None:
                    rec.pop("pinned")  # pin removed: the channel keeps the number it has
                if rec is None:
                    n = self.next.get(kind, 1)
                    while n in taken:
                        n += 1
                    rec = known[e.key] = {"number": n}
                    taken.add(n)
                    self.next[kind] = n + 1
                rec["last_seen"] = when
                e.attrs["tvg-chno"] = str(rec["number"])

    def forget_older_than(self, days: int) -> None:
        """Drop channels not kept for `days`; their numbers stay retired."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        for kind, known in self.numbers.items():
            self.numbers[kind] = {
                key: rec for key, rec in known.items() if datetime.fromisoformat(rec["last_seen"]) >= cutoff
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"next": self.next, "numbers": self.numbers}, indent=1, sort_keys=True))
        tmp.replace(self.path)
