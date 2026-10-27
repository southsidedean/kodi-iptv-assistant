"""NTS Radio's show schedule (native.grab_nts): two live channels, from nts.live's public API."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kia import epgmap, native

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def day(*shows) -> dict:
    return {"date": "2026-10-26", "broadcasts": [{"broadcast_title": t, "start_timestamp": a, "end_timestamp": b} for t, a, b in shows]}


SCHEDULE = {"results": [
    day(("Earlier", "2026-10-26T10:00:00Z", "2026-10-26T11:00:00Z"),  # over: left out
        ("On now  w/  Guests &amp; Friends&#039;", "2026-10-26T11:00:00Z", "2026-10-26T13:00:00Z"),
        ("Bad time", "soon", "later"), ("", "2026-10-26T13:00:00Z", "2026-10-26T14:00:00Z")),
    day(("Tomorrow", "2026-10-27T09:00:00Z", "2026-10-27T10:00:00Z"), ("Too far", "2026-11-05T09:00:00Z", "2026-11-05T10:00:00Z")),
    "not a day", {"broadcasts": [{"broadcast_title": 5}]},
]}  # fmt: skip


def test_the_shows_ahead():
    shows = native.nts_programmes(SCHEDULE["results"], NOW, 3)
    assert [s[2] for s in shows] == ["On now w/ Guests & Friends'", "Tomorrow"]  # NTS's HTML escapes undone once
    assert native.nts_programmes("nope", NOW, 3) == []


def test_each_station_gets_its_channels_schedule(tmp_path: Path):
    for ch in ("1", "2"):
        (tmp_path / f"nts{ch}.json").write_text(json.dumps(SCHEDULE))
    chans = tmp_path / "nts.live.channels.xml"
    chans.write_text('<channels><channel site="nts.live" site_id="1" xmltv_id="radio.a">NTS Radio 1</channel>'
                     '<channel site="nts.live" site_id="2" xmltv_id="radio.b">NTS Radio 2</channel>'
                     '<channel site="nts.live" site_id="../x" xmltv_id="radio.c">Evil</channel></channels>')  # fmt: skip
    out, said = tmp_path / "out.xml", []
    native.grab_nts(chans, out, 3, said.append, url=tmp_path.as_uri() + "/nts{channel}.json", now=NOW)
    text = out.read_text()
    assert text.count("On now w/ Guests &amp; Friends'") == 2  # escaped once, for XML and 'channel="radio.a"' in text and 'channel="radio.b"' in text
    assert "radio.c" not in text and said == ["nts.live: 4 shows in the next 3 days, for 2 station(s)"]


def test_one_station_failing_keeps_the_other(tmp_path: Path):
    (tmp_path / "nts1.json").write_text(json.dumps(SCHEDULE))  # no nts2.json: NTS 2 can't be read
    chans = tmp_path / "nts.live.channels.xml"
    chans.write_text('<channels><channel site="nts.live" site_id="1" xmltv_id="radio.a">NTS Radio 1</channel>'
                     '<channel site="nts.live" site_id="2" xmltv_id="radio.b">NTS Radio 2</channel></channels>')  # fmt: skip
    out, said = tmp_path / "out.xml", []
    native.grab_nts(chans, out, 3, said.append, url=tmp_path.as_uri() + "/nts{channel}.json", now=NOW)
    assert 'channel="radio.a"><title>Tomorrow' in out.read_text() and 'channel="radio.b"><title>' not in out.read_text()
    assert said[0].startswith("nts.live: couldn't read NTS 2")
    (tmp_path / "nts1.json").unlink()
    with pytest.raises(RuntimeError, match="no NTS schedule"):
        native.grab_nts(chans, out, 3, said.append, url=tmp_path.as_uri() + "/nts{channel}.json", now=NOW)


def test_a_time_without_its_offset_is_skipped():
    shows = native.nts_programmes([day(("Mixed", "2026-10-26T13:00:00Z", "2026-10-26T14:00:00"))], NOW, 3)
    assert shows == []


def test_matched_by_the_stations_name():
    found = epgmap.radio_schedule_candidates({"radio.x": "NTS Radio 1", "radio.y": "NTS 2", "radio.z": "Slow Focus | NTS"}, set())
    assert {tid: c[0].channel.site_id for tid, c in found.items()} == {"radio.x": "1", "radio.y": "2"}
    assert "nts.live" in native.QUICK_GRABBERS
