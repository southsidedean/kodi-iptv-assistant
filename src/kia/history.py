"""Per-stream check history, so one bad night doesn't drop a channel."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia.probe import ProbeResult


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def readable(iso: str | None) -> str:
    """'2026-10-19T20:55:33+00:00' -> 'Mon Oct 19, 15:55 CDT', in this computer's time zone."""
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return iso
    return f"{dt:%a %b} {dt.day}, {dt:%H:%M %Z}"


class History:
    def __init__(self, path: Path):
        self.path = path
        self.records: dict[str, dict] = {}
        if path.exists():
            self.records = json.loads(path.read_text())

    def get(self, url: str) -> dict:
        return self.records.get(url, {})

    def record(self, url: str, result: ProbeResult, when: str | None = None) -> dict:
        when = when or now_iso()
        rec = self.records.setdefault(url, {"first_seen": when, "checks": 0, "successes": 0})
        rec["checks"] += 1
        rec["last_checked"] = when
        if result.ok:
            rec["successes"] += 1
            rec["last_ok"] = when
            rec["consecutive_failures"] = 0
            rec["height"] = result.height
            rec.pop("last_error", None)
        else:
            rec["consecutive_failures"] = rec.get("consecutive_failures", 0) + 1
            rec["last_error"] = result.error
        return rec

    def prune(self, max_age_days: int = 30) -> None:
        """Forget streams that haven't appeared in any playlist for a while."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        self.records = {
            url: rec
            for url, rec in self.records.items()
            if datetime.fromisoformat(rec["last_checked"]) >= cutoff
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.records, indent=1, sort_keys=True))
        tmp.replace(self.path)
