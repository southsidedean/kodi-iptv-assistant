"""kia.newchannels: channels new to the playlists, and gone from them."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia import newchannels
from kia.m3u import Entry

DAY0 = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)


def night(n: int) -> str:
    return (DAY0 + timedelta(days=n)).isoformat()


def ch(tvg_id: str, name: str = "", group: str = "News") -> Entry:
    return Entry(name or tvg_id.split(".")[0], f"http://a/{tvg_id}", {"tvg-id": tvg_id, "group-title": group})


def sent(tmp_path: Path, guided=None, **filters) -> list[tuple[str, str, str]]:
    out = []
    newchannels.notify(tmp_path, guided or {}, lambda *m: out.append(m), **filters)
    return out


def test_the_first_night_has_nothing_new(tmp_path: Path):
    assert newchannels.track(tmp_path, [ch("CNN.us"), ch("BBC.uk")], set(), night(0), 30) == {"new": [], "gone": []}
    assert sent(tmp_path) == [] and newchannels.this_week(tmp_path, DAY0 + timedelta(days=1)) == {"new": [], "gone": []}


def test_new_and_gone(tmp_path: Path):
    newchannels.track(tmp_path, [ch("CNN.us"), ch("BBC.uk")], set(), night(0), 30)
    news = newchannels.track(tmp_path, [ch("CNN.us"), ch("Bloomberg.us", "Bloomberg TV")], set(), night(1), 30)
    assert news == {"new": ["Bloomberg.us"], "gone": ["BBC.uk"]}
    assert sent(tmp_path) == [("new_channels", "kia: 1 new channel", "New in your playlists: Bloomberg TV."),
                              ("channels_gone", "kia: 1 channel gone",
                               "No longer in your playlists, as their streams stopped working: BBC.")]  # fmt: skip
    assert sent(tmp_path) == []  # sent once
    week = newchannels.this_week(tmp_path, DAY0 + timedelta(days=2))
    assert [c["name"] for c in week["new"]] == ["Bloomberg TV"] and [c["name"] for c in week["gone"]] == ["BBC"]
    assert newchannels.this_week(tmp_path, DAY0 + timedelta(days=9)) == {"new": [], "gone": []}  # a week on


def test_back_within_the_days_kia_remembers_isnt_new(tmp_path: Path):
    newchannels.track(tmp_path, [ch("CNN.us"), ch("BBC.uk")], set(), night(0), 30)
    newchannels.track(tmp_path, [ch("CNN.us")], set(), night(1), 30)
    assert newchannels.track(tmp_path, [ch("CNN.us"), ch("BBC.uk")], set(), night(3), 30)["new"] == []
    assert newchannels.this_week(tmp_path, DAY0 + timedelta(days=3))["gone"] == []  # back: no longer gone
    newchannels.track(tmp_path, [ch("CNN.us")], set(), night(4), 30)
    newchannels.track(tmp_path, [ch("CNN.us")], set(), night(5), 30)  # gone two nights: only the first says so
    newchannels.track(tmp_path, [ch("CNN.us")], set(), night(39), 30)
    assert newchannels.track(tmp_path, [ch("CNN.us"), ch("BBC.uk")], set(), night(40), 30)["new"] == ["BBC.uk"]  # long gone


def test_removed_on_the_dashboard_isnt_gone(tmp_path: Path):
    newchannels.track(tmp_path, [ch("CNN.us"), ch("BBC.uk")], set(), night(0), 30)
    assert newchannels.track(tmp_path, [ch("CNN.us")], {"BBC.uk"}, night(1), 30)["gone"] == []


def test_the_filters(tmp_path: Path):
    newchannels.track(tmp_path, [ch("CNN.us")], set(), night(0), 30)
    newchannels.track(tmp_path, [ch("CNN.us"), ch("ESPN.us", "ESPN", "Sports;News"), ch("Food.us", "Food", "Cooking")],
                      set(), night(1), 30)  # fmt: skip
    state = (tmp_path / newchannels.FILE).read_text()
    assert [m[2] for m in sent(tmp_path, {"ESPN.us": True}, with_guide=True)] == ["New in your playlists: ESPN."]
    (tmp_path / newchannels.FILE).write_text(state)  # the same night's news again
    assert [m[2] for m in sent(tmp_path, categories=("sports",))] == ["New in your playlists: ESPN."]
    (tmp_path / newchannels.FILE).write_text(state)
    assert sent(tmp_path, categories=("Kids",)) == []


def test_ten_named_the_rest_counted(tmp_path: Path):
    newchannels.track(tmp_path, [ch("CNN.us")], set(), night(0), 30)
    newchannels.track(tmp_path, [ch("CNN.us")] + [ch(f"C{i:02}.us") for i in range(13)], set(), night(1), 30)
    [(_event, title, message)] = sent(tmp_path)
    assert title == "kia: 13 new channels" and message.endswith("C09, and 3 more.")


def test_a_damaged_record_counts_as_none(tmp_path: Path):
    for text in ("[1, 2]", "{", json.dumps({"started": 5, "channels": {"x": "y", "z": {"first": 1}}, "news": {"new": "abc"}})):
        (tmp_path / newchannels.FILE).write_text(text)
        assert newchannels.this_week(tmp_path) == {"new": [], "gone": []}
        assert sent(tmp_path) == []
        assert newchannels.track(tmp_path, [ch("CNN.us")], set(), night(0), 30) == {"new": [], "gone": []}  # a first night
        (tmp_path / newchannels.FILE).unlink()
    # A gone record without its time (the shape a channel's record has) is left out, not a KeyError.
    (tmp_path / newchannels.FILE).write_text(json.dumps({"started": night(0), "run": night(0), "channels": {},
                                                         "gone": {"x": {"name": "X", "first": night(0)}}}))  # fmt: skip
    assert newchannels.track(tmp_path, [ch("CNN.us")], set(), night(1), 30)["gone"] == []
