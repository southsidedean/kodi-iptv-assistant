"""Channels new to your playlists, and channels gone from them: the new_channels and
channels_gone notices, the Playlists page's "New this week", and kia_whats_new.

After each nightly check, kia notes which channels made it into the playlists, in
state/channels-seen.json: when each was first and last there. A channel is new when it
wasn't there for history_days (the same days kia keeps each stream's record), so one that
drops out for a night or two and comes back isn't new again. It's gone when it was there
the night before and isn't now: the check already gave its stream the grace nights
([check] grace_failures) before leaving it out. Channels you removed on the Dashboard
aren't gone, and the first night has nothing new: everything is.

The check (track) only writes down the night's news; the nightly run sends it (notify)
after the guide is built, so the "only with a guide" filter knows which channels have one.
Channel names and categories come from playlists: the messages and the page only quote
them, cut short.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from kia.files import obj, read_object, temp_name
from kia.m3u import Entry

FILE = "channels-seen.json"  # in state/
WEEK = 7  # days a channel shows as new, or gone, on the Playlists page and in kia_whats_new
NAMED = 10  # channels named in one message; the rest are counted


def _load(state_dir: Path) -> dict:
    """The record, with anything damaged into another shape left out."""
    data = read_object(state_dir / FILE)

    def records(value) -> dict[str, dict]:
        return {k: r for k, r in obj(value).items() if isinstance(k, str) and isinstance(r, dict)
                and isinstance(r.get("name"), str) and isinstance(r.get("first", r.get("gone")), str)}  # fmt: skip

    news = obj(data.get("news"))
    pending = {k: [x for x in news[k] if isinstance(x, str)] if isinstance(news.get(k), list) else [] for k in ("new", "gone")}
    return {
        "started": data.get("started") if isinstance(data.get("started"), str) else "",
        "run": data.get("run") if isinstance(data.get("run"), str) else "",
        "channels": records(data.get("channels")),
        "gone": records(data.get("gone")),
        "news": pending,
    }


def _save(state_dir: Path, data: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(state_dir / FILE)
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(state_dir / FILE)


def _days_between(earlier: str, later: str) -> float:
    try:
        return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds() / 86400
    except (TypeError, ValueError):
        return float("inf")  # a damaged time: as if long ago


def track(state_dir: Path, entries: list[Entry], removed: set[str], when: str, history_days: int) -> dict:
    """Note tonight's channels (the playlists' entries) and work out what's new and gone.
    removed: keys of channels you removed on the Dashboard. Returns {"new": [...], "gone":
    [...]}, each a list of channel keys, kept as the news the nightly run sends."""
    data = _load(state_dir)
    first_night = not data["started"]
    if first_night:
        data["started"] = when
    seen, gone = data["channels"], data["gone"]
    previous = data["run"]
    here = {}
    for e in entries:
        here.setdefault(e.key, e)
    new = []
    for key, e in here.items():
        old = seen.get(key)
        if old is None or _days_between(old.get("last", ""), when) > history_days:
            seen[key] = {"first": when}
            if not first_night:
                new.append(key)
        seen[key].update({"name": e.name[:120], "group": e.group[:120], "radio": e.is_radio, "last": when})
        gone.pop(key, None)  # back
    left = [k for k, r in seen.items() if k not in here and previous and r.get("last") == previous and k not in removed]
    for key in left:
        gone[key] = {"name": seen[key]["name"], "group": seen[key].get("group", ""), "radio": seen[key].get("radio", False),
                     "guide": seen[key].get("guide"), "gone": when}  # fmt: skip
    # Forgotten after history_days away; a gone note after a week.
    data["channels"] = {k: r for k, r in seen.items() if _days_between(r.get("last", ""), when) <= history_days}
    data["gone"] = {k: r for k, r in gone.items() if _days_between(r["gone"], when) <= WEEK}
    data["run"] = when
    # Tonight's news, added to any not sent yet (a check run by hand, say).
    data["news"] = {"new": sorted(set(data["news"]["new"]) | set(new)), "gone": sorted(set(data["news"]["gone"]) | set(left))}
    _save(state_dir, data)
    return {"new": new, "gone": left}


def _wanted(record: dict, with_guide: bool, categories: tuple[str, ...]) -> bool:
    """Whether a channel passes [alerts]' filters: a guide, and one of the categories (a
    playlist's group-title can hold several, split by ";")."""
    if with_guide and not record.get("guide"):
        return False
    if categories:
        groups = {g.strip().casefold() for g in record.get("group", "").split(";")}
        return any(c.casefold() in groups for c in categories)
    return True


def _listed(records: list[dict]) -> str:
    names = sorted(r["name"] for r in records)
    more = len(names) - NAMED
    return ", ".join(n[:60] for n in names[:NAMED]) + (f", and {more} more" if more > 0 else "")


def notify(state_dir: Path, guided: dict[str, bool], send: Callable[[str, str, str], None],
           with_guide: bool = False, categories: tuple[str, ...] = ()) -> None:  # fmt: skip
    """Send the news the checks noted, then clear it: one message for new channels and one
    for gone ones, each only with any left after the filters. guided: which channel IDs
    have guide data tonight (work/guide-choices.json). send(event, title, message) is
    alerts.notify, which sends only the notices you turned on."""
    data = _load(state_dir)
    for key, r in data["channels"].items():
        if r.get("last") == data["run"]:
            r["guide"] = bool(guided.get(key) or guided.get(key.split("@")[0]))  # an ID's @SD/@HD variant shares its guide
    new = [r for k in data["news"]["new"] if (r := data["channels"].get(k)) and _wanted(r, with_guide, categories)]
    gone = [r for k in data["news"]["gone"] if (r := data["gone"].get(k)) and _wanted(r, with_guide, categories)]
    if new:
        many = "1 new channel" if len(new) == 1 else f"{len(new)} new channels"
        send("new_channels", f"kia: {many}", f"New in your playlists: {_listed(new)}.")
    if gone:
        many = "1 channel" if len(gone) == 1 else f"{len(gone)} channels"
        send("channels_gone", f"kia: {many} gone", f"No longer in your playlists, as their streams stopped working: {_listed(gone)}.")
    data["news"] = {"new": [], "gone": []}
    _save(state_dir, data)


def this_week(state_dir: Path, now: datetime | None = None) -> dict:
    """Channels new in the last WEEK days (not the first night's) and gone in them, newest
    first: each one's key, name, category, and when, for the Playlists page and kia_whats_new."""
    data = _load(state_dir)
    now = now or datetime.now().astimezone()
    since = (now - timedelta(days=WEEK)).isoformat(timespec="seconds")

    def recent(when: str) -> bool:
        return _days_between(since, when) >= 0 and when != data["started"]

    new = [{"key": k, "name": r["name"], "group": r.get("group", ""), "radio": r.get("radio", False), "since": r["first"]}
           for k, r in data["channels"].items() if recent(r["first"])]  # fmt: skip
    gone = [{"key": k, "name": r["name"], "group": r.get("group", ""), "radio": r.get("radio", False), "since": r["gone"]}
            for k, r in data["gone"].items() if recent(r["gone"])]  # fmt: skip
    return {"new": sorted(new, key=lambda c: c["since"], reverse=True),
            "gone": sorted(gone, key=lambda c: c["since"], reverse=True)}  # fmt: skip
