"""SomaFM's channels and KEXP (with its show schedule), from what the stations publish."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kia import epgmap, native, radio, sources, xmltv
from kia.config import parse_sources

CHANNELS = {"channels": [
    {"id": "groovesalad", "title": "Groove Salad", "genre": "ambient|electronic",
     "xlimage": "https://api.somafm.com/logos/512/groovesalad512.png",
     "playlists": [{"url": "https://api.somafm.com/groovesalad256.pls", "format": "mp3", "quality": "highest"},
                   {"url": "https://api.somafm.com/groovesalad130.pls", "format": "aac", "quality": "highest"}]},
    {"id": "dronezone", "title": "Drone  Zone", "genre": "ambient", "image": "http://insecure/logo.png",
     "playlists": [{"url": "https://api.somafm.com/dronezone256.pls", "format": "mp3", "quality": "highest"}]},
    {"id": "gone", "title": "Gone", "genre": "", "playlists": [{"url": "https://api.somafm.com/gone.pls",
                                                              "format": "aac", "quality": "highest"}]},
]}  # fmt: skip
PLS = {
    "https://api.somafm.com/groovesalad130.pls": "[playlist]\nnumberofentries=2\nFile2=https://ice6.somafm.com/gs-b\nFile1=https://ice2.somafm.com/groovesalad-128-aac\n",
    "https://api.somafm.com/dronezone256.pls": "[playlist]\nFile1=https://ice2.somafm.com/dronezone-256-mp3\n",
}  # fmt: skip


def fake_get(url, timeout=30):
    if url == radio.SOMAFM_CHANNELS:
        return json.dumps(CHANNELS).encode()
    if url in PLS:
        return PLS[url].encode()
    raise OSError("404")  # the "gone" channel's playlist


def test_somafm_channels_from_its_own_list():
    entries = radio.somafm({}, "somafm", get=fake_get)
    assert [(e.name, e.url) for e in entries] == [
        ("SomaFM Groove Salad", "https://ice2.somafm.com/groovesalad-128-aac"),  # AAC first, File1 first
        ("SomaFM Drone Zone", "https://ice2.somafm.com/dronezone-256-mp3"),  # MP3 when there's no AAC
    ]  # a channel whose playlist doesn't answer is left out
    gs, dz = entries
    assert gs.attrs == {"tvg-id": "radio.somafm.groovesalad", "group-title": "Ambient", "radio": "true",
                        "tvg-logo": "https://api.somafm.com/logos/512/groovesalad512.png"}  # fmt: skip
    assert "tvg-logo" not in dz.attrs  # only https logos
    assert [e.name for e in radio.somafm({"channels": "DroneZone"}, "s", get=fake_get)] == ["SomaFM Drone Zone"]
    with pytest.raises(radio.RadioError, match="no channel nope"):
        radio.somafm({"channels": "dronezone,nope"}, "s", get=fake_get)


def test_somafm_unreachable_is_a_radio_error():
    def down(url, timeout=30):
        raise OSError("timed out")

    with pytest.raises(radio.RadioError, match="didn't load"):
        radio.somafm({}, "s", get=down)


def test_station_lines_in_the_sources_file():
    got = parse_sources("somafm:\nkexp:   # KEXP\nsomafm: channels=groovesalad name=chill\n")
    assert [(s.name, s.url, s.params, s.is_stations, s.is_local_file) for s in got] == [
        ("somafm", "somafm:", {}, True, False),
        ("kexp", "kexp:", {}, True, False),
        ("chill", "somafm: channels=groovesalad name=chill", {"channels": "groovesalad"}, True, False),  # as written, like radio-browser:
    ]
    with pytest.raises(ValueError, match="kexp: unknown choice"):
        parse_sources("kexp: top=3")
    with pytest.raises(ValueError, match="somafm: expected key=value"):
        parse_sources("somafm: groovesalad")
    kexp = radio.load_stations("kexp:", {}, "kexp")
    assert [(e.name, e.url, e.attrs["tvg-id"]) for e in kexp] == [("KEXP", radio.KEXP_STREAM, "radio.kexp")]


def test_the_dashboard_offers_station_lines_as_radio():
    text = "# --- Radio ---\n# somafm:   # SomaFM, every channel  51\n# kexp:\nhttps://a/tv.m3u tv\n"
    offered = sources.offered(text)
    assert [(o.url, o.name, o.radio, o.on) for o in offered] == [
        ("somafm:", "somafm", True, False), ("kexp:", "kexp", True, False), ("https://a/tv.m3u", "tv", False, True)]  # fmt: skip
    assert sources.check_new("somafm: channels=groovesalad", "chill", set()) == ("somafm: channels=groovesalad", "chill")


def timeslot(weekday, start, end, name, hosts=(), start_date="2020-08-20", end_date=None):
    return {"weekday": weekday, "start_time": start, "end_time": end, "program_name": name,
            "host_names": list(hosts), "start_date": start_date, "end_date": end_date}  # fmt: skip


SLOTS = [
    timeslot(5, "07:00:00", "10:00:00", "The Morning Show", ["John Richards"]),  # Friday
    timeslot(5, "22:00:00", "01:00:00", "Street Sounds", ["Larry Mizell, Jr."]),  # past midnight
    timeslot(6, "00:00:00", "02:00:00", "Overlapping"),  # starts while Street Sounds is on
    timeslot(6, "07:00:00", "10:00:00", "Saturday Morning"),
    timeslot(1, "07:00:00", "10:00:00", "The Morning Show", ["John Richards"]),  # Monday
    timeslot(5, "12:00:00", "13:00:00", "Ended", end_date="2026-01-01"),
    timeslot(5, "13:00:00", "14:00:00", "Not yet", start_date="2027-01-01"),
    timeslot(5, "oops", "14:00:00", "Broken"),
]


def test_kexp_programmes_from_its_weekly_slots():
    now = datetime(2026, 10, 23, 18, tzinfo=timezone.utc)  # Friday 11:00 in Seattle
    shows = native.kexp_programmes(SLOTS, now, 2)
    got = [(s[0].strftime("%a %H:%M"), s[1].strftime("%a %H:%M"), s[2], s[3]) for s in shows]
    assert got == [
        ("Fri 07:00", "Fri 10:00", "The Morning Show", "John Richards"),
        ("Fri 22:00", "Sat 00:00", "Street Sounds", "Larry Mizell, Jr."),  # ends when the next show starts
        ("Sat 00:00", "Sat 02:00", "Overlapping", ""),
        ("Sat 07:00", "Sat 10:00", "Saturday Morning", ""),
    ]  # no "Ended", "Not yet", or "Broken"; Monday is past the two days


def test_kexp_times_stay_in_seattle_time_across_daylight_saving():
    # US daylight saving time ends on Sunday, November 1, 2026.
    shows = native.kexp_programmes(SLOTS, datetime(2026, 10, 31, 18, tzinfo=timezone.utc), 3)
    mondays = [s for s in shows if s[0].strftime("%a") == "Mon"]
    assert [(s[0].hour, s[0].utcoffset().total_seconds() / 3600) for s in mondays] == [(7, -8)]
    fridays = native.kexp_programmes(SLOTS, datetime(2026, 10, 23, 18, tzinfo=timezone.utc), 1)
    assert fridays[0][0].utcoffset().total_seconds() / 3600 == -7


def test_grab_kexp_writes_the_schedule(tmp_path: Path, monkeypatch):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return json.dumps({"results": SLOTS}).encode()

    monkeypatch.setattr(native, "fetch", lambda url, timeout=60: Resp())
    channels = tmp_path / "kexp.org.channels.xml"
    channels.write_text('<channels><channel site="kexp.org" site_id="kexp" xmltv_id="radio.kexp">KEXP</channel></channels>')
    out = tmp_path / "kexp.xml"
    native.grab_kexp(channels, out, 2, lambda m: None, now=datetime(2026, 10, 23, 18, tzinfo=timezone.utc))
    text = out.read_text()
    assert xmltv.scan(out)["radio.kexp"]  # a complete XMLTV file that kia guide accepts
    assert ('start="20261023070000 -0700" stop="20261023100000 -0700" channel="radio.kexp">'
            "<title>The Morning Show</title><desc>With John Richards</desc>") in text  # fmt: skip


def test_the_kexp_station_gets_the_schedule():
    found = epgmap.radio_schedule_candidates({"radio.kexp": "KEXP", "radio.other": "KEXP Live Sessions"}, set())
    assert list(found) == ["radio.kexp"] and found["radio.kexp"][0].channel.site == "kexp.org"
