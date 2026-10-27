"""Records inside kia's state files, damaged into other shapes (a hand edit, a full disk, a
container that was taken over): each counts as missing, and the rest of the file still counts."""

import json
from datetime import datetime, timezone
from pathlib import Path

from kia import channels
from kia.history import History
from kia.m3u import Entry
from kia.numbers import ChannelNumbers
from kia.probe import ProbeResult

WHEN = "2026-10-26T12:00:00+00:00"


def test_stream_history(tmp_path: Path):
    path = tmp_path / "history.json"
    now = datetime.now(timezone.utc).isoformat()  # prune goes by the real clock
    good = {"first_seen": now, "last_checked": now, "checks": 3, "successes": 2}
    path.write_text(json.dumps({"http://a/1": good, "http://a/2": {**good, "checks": "3"}, "http://a/3": {**good, "last_checked": 5},
                                "http://a/4": {"checks": 1, "successes": 1}, "http://a/5": "x",
                                "http://a/6": {**good, "last_checked": "2026-10-26T12:00:00"}}))  # fmt: skip
    h = History(path)
    assert list(h.records) == ["http://a/1"]
    h.record("http://a/2", ProbeResult(ok=True, height=720), now)  # was damaged: starts again
    h.prune(30)
    assert set(h.records) == {"http://a/1", "http://a/2"} and h.records["http://a/2"]["checks"] == 1


def test_channel_numbers(tmp_path: Path):
    path = tmp_path / "channel-numbers.json"
    path.write_text(json.dumps({"next": {"tv": 5}, "numbers": {"tv": {
        "A.us": {"number": 1, "last_seen": WHEN}, "B.us": {"number": "2"}, "C.us": {"number": 3, "last_seen": 7},
        "D.us": {"number": True}}}, "retired": {"tv": {"4": WHEN, "9": 5}}}))  # fmt: skip
    n = ChannelNumbers(path)
    assert list(n.numbers["tv"]) == ["A.us"] and n.retired["tv"] == {"4": WHEN}
    es = [Entry(x, f"u{x}", {"tvg-id": f"{x}.us"}) for x in "ABCD"]
    n.assign(es, WHEN)
    n.forget_older_than(30)
    assert es[0].attrs["tvg-chno"] == "1" and len({e.attrs["tvg-chno"] for e in es}) == 4


def test_dashboard_choices(tmp_path: Path):
    (tmp_path / channels.STATE_FILE).write_text(json.dumps({
        "numbers": {"tv": {"A.us": "7", "B.us": 8, "C.us": 0, "D.us": 10**9}},
        "removed": {"x": "gone", "y": {"name": "Y", "good_nights": "2"}}}))  # fmt: skip
    choices = channels.load(tmp_path)
    assert choices["numbers"]["tv"] == {"B.us": 8} and list(choices["removed"]) == ["y"]
    entries = channels.apply([Entry(x, f"u{x}", {"tvg-id": f"{x}.us"}) for x in "AB"], choices)
    assert [e.pinned_number for e in entries] == [None, 8]
    assert channels.count_good_nights(choices, {"y": "ok"}) == [] and choices["removed"]["y"]["good_nights"] == 1
