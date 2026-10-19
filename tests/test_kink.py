import json
from datetime import datetime, timezone
from pathlib import Path

from kia import epgmap, native, xmltv


def kink_page(slots: list[dict]) -> str:
    """A page shaped like kink.nl/programmering: data in a Next.js script chunk."""
    rsc = 'a:["$","$L19",null,{"start":{"date":"2026-10-19","timeslots":{"data":' + json.dumps(slots) + "}}}]\n"
    return f"<html><script>self.__next_f.push([1,{json.dumps(rsc)}])</script></html>"


def slot(start, end, title, dj="", alt_title=""):
    return {"id": 1, "start": start, "end": end, "alt_title": alt_title, "alt_dj": "",
            "program": {"data": {"id": 9, "title": title, "slug": "x", "alt_title": dj, "djs": {"data": []}}}}  # fmt: skip


def test_parse_kink_schedule():
    page = kink_page([
        slot("2026-10-19T06:00:00+02:00", "2026-10-19T09:00:00+02:00", "KINKSTART", "Michiel Veenstra"),
        slot("2026-10-19T00:00:00+02:00", "2026-10-19T06:00:00+02:00", "KINK NON-STOP"),
        slot("2026-10-19T09:00:00+02:00", "2026-10-19T12:00:00+02:00", "KINK 1500", alt_title="Special"),
    ])  # fmt: skip
    slots = native.parse_kink_schedule(page)
    assert [(s[0].hour, s[2], s[3]) for s in slots] == [(0, "KINK NON-STOP", ""), (6, "KINKSTART", "Michiel Veenstra"),
                                                        (9, "Special", "")]  # fmt: skip
    assert native.parse_kink_schedule("<html>no data</html>") == []


def test_grab_kink_writes_the_schedule(tmp_path: Path, monkeypatch):
    page = kink_page([slot("2026-10-19T21:00:00+02:00", "2026-10-20T00:00:00+02:00", "KINK 1500", "Diane Kok")])

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return page.encode()

    monkeypatch.setattr(native, "fetch", lambda url, timeout=60: Resp())
    channels = tmp_path / "kink.nl.channels.xml"
    channels.write_text('<channels><channel site="kink.nl" site_id="kink" xmltv_id="radio.kink">KINK</channel></channels>')
    out = tmp_path / "kink.xml"
    native.grab_kink(channels, out, 2, lambda m: None, now=datetime(2026, 10, 19, 12, tzinfo=timezone.utc))
    text = out.read_text()
    assert xmltv.scan(out)["radio.kink"]  # a complete XMLTV file that kia guide accepts
    assert 'channel="radio.kink"><title>KINK 1500</title><desc>Diane Kok</desc>' in text
    assert 'start="20261019210000 +0200"' in text


def test_only_the_main_kink_station_gets_the_schedule():
    found = epgmap.radio_schedule_candidates({"radio.kink": "KINK", "radio.kink-dna": "KINK DNA"}, set())
    assert list(found) == ["radio.kink"] and found["radio.kink"][0].channel.site == "kink.nl"
    assert epgmap.radio_schedule_candidates({"radio.kink": "KINK"}, {"kink.nl"}) == {}
