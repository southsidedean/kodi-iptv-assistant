"""Channel numbers that stay the same from night to night.

Kodi numbers channels by their place in the playlist unless the playlist gives numbers
(tvg-chno). Without them, one stream dropping out or a new playlist renumbers everything
after it. kia gives each channel a number the first time it's kept and remembers it, so a
channel keeps its number, and one that comes back after a few bad nights gets its old one.
TV and radio are numbered separately, as Kodi shows them separately.

A tvg-chno in one of your own playlist files pins that channel's number; the automatic
numbering works around pinned numbers.

Numbers aren't reused, unless recycling is on (recycle_after_days): then a new channel
takes the lowest number whose channel was last seen at least that many days ago. renumber()
numbers everything 1, 2, 3 in playlist order, once, keeping pins. cli.load_entries marks those pins; numbers in
playlists from the internet are ignored, so they can't reshuffle yours.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia.files import obj, read_object, temp_name
from kia.m3u import Entry


def pinned_number(entry: Entry) -> int | None:
    return entry.pinned_number


class ChannelNumbers:
    def __init__(self, path: Path, recycle_after_days: int = 0):
        self.path = path
        self.recycle_after_days = recycle_after_days
        data = read_object(path)
        if path.exists() and not data:
            # Cut short or damaged into another shape: set it aside as channel-numbers.json.bad
            # (as kia.history does) and start again, so the nightly check still runs. The
            # numbers come back from the playlists' own and the Dashboard's choices.
            path.replace(path.with_name(path.name + ".bad"))
        data = {k: obj(data.get(k)) for k in ("numbers", "next", "retired")} if data else {}
        # numbers[kind][channel key] = {"number": n, "last_seen": iso time, "pinned": bool}
        self.numbers: dict[str, dict[str, dict]] = {k: {c: r for c, r in obj(v).items() if isinstance(r, dict)}
                                                    for k, v in data.get("numbers", {"tv": {}, "radio": {}}).items()}  # fmt: skip
        # Automatic numbers are never reused, even after a channel is forgotten, so an old
        # number never points at a different channel.
        self.next: dict[str, int] = {k: v for k, v in data.get("next", {"tv": 1, "radio": 1}).items() if isinstance(v, int)}
        # Numbers of forgotten channels, with when each channel was last seen, for recycling.
        self.retired: dict[str, dict[str, str]] = {k: obj(v) for k, v in data.get("retired", {"tv": {}, "radio": {}}).items()}

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
                known[e.key] = {"number": n, "pinned": e.pinned_by}
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
                    n = self._recycled(kind, taken, when)
                    if n is None:
                        n = self.next.get(kind, 1)
                        # A forgotten channel's number above the counter (a pinned or chosen
                        # one) is retired too: never reused, unless recycling picks it.
                        retired = self.retired.get(kind, {})
                        while n in taken or str(n) in retired:
                            n += 1
                        self.next[kind] = n + 1
                    else:
                        log(f"Channel {e.key!r} gets number {n} again (its last channel left long ago)")
                    self.retired.get(kind, {}).pop(str(n), None)
                    rec = known[e.key] = {"number": n}
                    taken.add(n)
                rec["last_seen"] = when
                e.attrs["tvg-chno"] = str(rec["number"])

    def _recycled(self, kind: str, taken: set[int], when: str) -> int | None:
        """The lowest number free for reuse, or None (recycling off, or nothing old enough)."""
        if not self.recycle_after_days:
            return None
        cutoff = datetime.fromisoformat(when) - timedelta(days=self.recycle_after_days)
        free = [int(n) for n, seen in self.retired.get(kind, {}).items()
                if int(n) not in taken and datetime.fromisoformat(seen) <= cutoff]  # fmt: skip
        return min(free) if free else None

    def forget_older_than(self, days: int) -> None:
        """Drop channels not kept for `days`; their numbers stay retired (see _recycled)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        for kind, known in self.numbers.items():
            retired = self.retired.setdefault(kind, {})
            for key, rec in list(known.items()):
                if "last_seen" in rec and datetime.fromisoformat(rec["last_seen"]) < cutoff:
                    retired[str(rec["number"])] = rec["last_seen"]
                    del known[key]

    def renumber(self, entries: list[Entry], when: str, log: Callable[[str], None] = lambda m: None) -> None:
        """Number every channel 1, 2, 3... in the order given (TV and radio apart), once,
        keeping pinned numbers. Forgotten channels' numbers are forgotten too."""
        self.numbers = {"tv": {}, "radio": {}}
        self.next = {"tv": 1, "radio": 1}
        self.retired = {"tv": {}, "radio": {}}
        self.assign(entries, when, log)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = temp_name(self.path)
        tmp.write_text(json.dumps({"next": self.next, "numbers": self.numbers, "retired": self.retired}, indent=1, sort_keys=True))
        tmp.replace(self.path)
