"""Channel numbers that stay the same from night to night.

Kodi numbers channels by their place in the playlist unless the playlist gives numbers
(tvg-chno). Without them, one stream dropping out or a new playlist renumbers everything
after it. kia gives each channel a number the first time it's kept and remembers it, so a
channel keeps its number, and one that comes back after a few bad nights gets its old one.
TV and radio are numbered separately, as Kodi shows them separately.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia.m3u import Entry


class ChannelNumbers:
    def __init__(self, path: Path):
        self.path = path
        data = {}
        if path.exists():
            data = json.loads(path.read_text())
        # numbers[kind][channel key] = {"number": n, "last_seen": iso time}
        self.numbers: dict[str, dict[str, dict]] = data.get("numbers", {"tv": {}, "radio": {}})
        # Numbers are never reused, even after a channel is forgotten, so an old number
        # never points at a different channel.
        self.next: dict[str, int] = data.get("next", {"tv": 1, "radio": 1})

    def assign(self, entries: list[Entry], when: str) -> None:
        """Set tvg-chno on each entry, giving new channels the next free number in list order."""
        for e in entries:
            kind = "radio" if e.is_radio else "tv"
            known = self.numbers.setdefault(kind, {})
            rec = known.get(e.key)
            if rec is None:
                rec = known[e.key] = {"number": self.next.get(kind, 1)}
                self.next[kind] = rec["number"] + 1
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
