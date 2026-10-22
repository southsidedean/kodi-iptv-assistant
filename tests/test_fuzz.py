"""Property tests: kia reads other people's playlists and writes guides from their text, so
it must cope with anything in them. Hypothesis tries many odd inputs for each property.
(A control character in a channel name once made the whole guide unreadable.)"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from kia import m3u, xmltv  # noqa: E402
from kia.m3u import Entry  # noqa: E402

SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
# Any text, including control characters, quotes, commas and line breaks.
ANY = st.text(st.characters(codec="utf-8"), max_size=40)
NAME = ANY.filter(lambda s: s.strip())
URL = st.from_regex(r"https?://[a-z0-9.-]{1,20}/[A-Za-z0-9/_.?=&-]{0,30}", fullmatch=True)
NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


@SETTINGS
@given(st.text(max_size=400))
def test_any_text_parses_without_an_error(text):
    for e in m3u.parse(text):
        assert "\n" not in e.name and "\n" not in e.url


@SETTINGS
@given(st.lists(st.tuples(NAME, URL, ANY, ANY), min_size=1, max_size=5))
def test_a_written_playlist_reads_back_the_same(rows):
    entries = [Entry(name, url, {"tvg-id": tid, "group-title": group}) for name, url, tid, group in rows]
    first = m3u.parse(m3u.write(entries))
    again = m3u.parse(m3u.write(first))
    assert [(e.name, e.url, e.attrs) for e in again] == [(e.name, e.url, e.attrs) for e in first]
    assert [e.url for e in first] == [url for _, url, _, _ in rows]  # no entry lost or split by its text


@SETTINGS
@given(st.lists(st.tuples(NAME, ANY.filter(lambda s: s.strip()), ANY), min_size=1, max_size=4, unique_by=lambda r: r[1]))
def test_the_guide_is_always_valid_xml(tmp_path_factory, rows):
    entries = [Entry(name, "http://x/1", {"tvg-id": tid, "tvg-logo": logo}) for name, tid, logo in rows]
    out = Path(tmp_path_factory.mktemp("guide")) / "guide.xml"
    choices = xmltv.choose_sources([e.tvg_id for e in entries], {}, {}, NOW)
    xmltv.write_guide(out, entries, choices, {}, placeholders=True, days=1, now=NOW)
    ET.parse(out)  # raises if one odd name broke it
