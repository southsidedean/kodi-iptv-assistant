import gzip
import io
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kia import grab, native
from kia.config import EpgConfig

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)

PLUTO = """<?xml version="1.0"?><tv>
<channel id="aaa"><display-name>Alpha</display-name></channel>
<channel id="bbb"><display-name>Beta</display-name></channel>
<programme start="20261015230000 +0000" stop="20261016000000 +0000" channel="aaa"><title>Yesterday</title></programme>
<programme start="20261016010000 +0000" stop="20261016020000 +0000" channel="aaa"><title>Alpha news</title><desc>Today's news &amp; more</desc><category>News</category></programme>
<programme start="20261016010000 +0000" stop="20261016020000 +0000" channel="bbb"><title>Not ours</title></programme>
<programme start="20261017200000 +0000" stop="20261017210000 +0000" channel="aaa"><title>Tomorrow night</title></programme>
<programme start="20261018000000 +0000" stop="20261018010000 +0000" channel="aaa"><title>Too late</title></programme>
</tv>"""

PLEX = """<?xml version="1.0"?><tv>
<programme start="20261016030000 +0000" stop="20261016040000 +0000" channel="ccc"><title>Gamma show</title></programme>
<programme start="20261016030000 +0000" stop="20261016040000 +0000" channel="newprefix-ddd"><title>Delta show</title></programme>
</tv>"""


def site_files(tmp_path: Path) -> str:
    base = tmp_path / "imjh"
    for rel, xml in {"PlutoTV/us.xml.gz": PLUTO, "Plex/us.xml.gz": PLEX}.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(xml.encode()))
    return base.as_uri()


def channels_file(tmp_path: Path, rows: list[tuple[str, str, str]]) -> Path:
    path = tmp_path / "i.mjh.nz.channels.xml"
    body = "".join(
        f'<channel site="i.mjh.nz" site_id="{s}" lang="en" xmltv_id="{x}">{n}</channel>' for s, x, n in rows
    )
    path.write_text(f"<channels>{body}</channels>")
    return path


def test_grab_imjh_filters_channels_and_days(tmp_path: Path):
    chans = channels_file(
        tmp_path,
        [
            ("PlutoTV/us#aaa", "Alpha.us@SD", "Alpha"),
            ("PlutoTV/us#aaa", "AlphaWest.us@West", "Alpha West"),  # same source, two playlist channels
            ("Plex/us#ccc", "Gamma.us@SD", "Gamma"),
            ("Plex/us#oldprefix-ddd", "Delta.us@SD", "Delta"),  # provider prefix changed upstream
            ("Roku/all#zzz", "Missing.us@SD", "Missing"),  # file doesn't exist: logged, not fatal
        ],
    )
    out = tmp_path / "out.xml"
    logs = []
    native.grab_imjh(chans, out, days=2, log=logs.append, base_url=site_files(tmp_path), now=NOW)

    root = ET.parse(out).getroot()
    assert {c.get("id") for c in root.findall("channel")} == {
        "Alpha.us@SD", "AlphaWest.us@West", "Gamma.us@SD", "Delta.us@SD", "Missing.us@SD"
    }  # fmt: skip
    progs = [(p.get("channel"), p.findtext("title")) for p in root.findall("programme")]
    assert ("Alpha.us@SD", "Alpha news") in progs and ("AlphaWest.us@West", "Alpha news") in progs
    assert ("Alpha.us@SD", "Tomorrow night") in progs
    assert ("Gamma.us@SD", "Gamma show") in progs
    assert ("Delta.us@SD", "Delta show") in progs
    titles = {t for _, t in progs}
    assert not titles & {"Yesterday", "Too late", "Not ours"}
    # Programme details pass through untouched.
    news = next(p for p in root.findall("programme") if p.findtext("title") == "Alpha news")
    assert news.findtext("desc") == "Today's news & more" and news.findtext("category") == "News"
    assert any("Roku/all: ERR" in m for m in logs)


def test_grab_imjh_fails_when_every_file_fails(tmp_path: Path):
    chans = channels_file(tmp_path, [("Roku/all#zzz", "Missing.us@SD", "Missing")])
    with pytest.raises(RuntimeError):
        native.grab_imjh(chans, tmp_path / "o.xml", 1, lambda m: None, base_url=site_files(tmp_path), now=NOW)


def test_grab_site_uses_native_grabber_and_stages(tmp_path: Path, monkeypatch):
    calls = []

    def fake(channels_file, output, days, log):
        calls.append(days)
        output.write_text("<tv></tv>")

    monkeypatch.setitem(native.NATIVE_GRABBERS, "i.mjh.nz", fake)
    monkeypatch.setattr(grab, "NATIVE_GRABBERS", native.NATIVE_GRABBERS)
    cfg = EpgConfig(work_dir=tmp_path / "work", repo_dir=tmp_path / "nope", days=2)
    chans = channels_file(tmp_path, [("PlutoTV/us#aaa", "Alpha.us@SD", "Alpha")])
    r = grab.grab_site(cfg, "i.mjh.nz", chans)
    assert r.returncode == 0 and calls == [2]
    assert r.output.read_text() == "<tv></tv>"

    def broken(channels_file, output, days, log):
        raise RuntimeError("boom")

    monkeypatch.setitem(native.NATIVE_GRABBERS, "i.mjh.nz", broken)
    r = grab.grab_site(cfg, "i.mjh.nz", chans)
    assert r.returncode == 1
    assert r.output.read_text() == "<tv></tv>"  # previous guide kept
    assert "boom" in r.log.read_text()


def test_a_show_on_air_at_midnight_utc_is_kept():
    # A refresh at 00:30 UTC (evening in the Americas): the show that began at 23:30 is on.
    xml = b"""<tv><programme start="20261015233000 +0000" stop="20261016013000 +0000" channel="aaa"><title>Late show</title></programme>
<programme start="20261015220000 +0000" stop="20261015233000 +0000" channel="aaa"><title>Over</title></programme></tv>"""
    got = []
    start = datetime(2026, 10, 16, tzinfo=timezone.utc)
    native.filter_xmltv(io.BytesIO(xml), {"aaa": ["A"]}, start, start.replace(day=17), got.append)
    assert len(got) == 1 and "Late show" in got[0]


def test_a_failed_file_keeps_last_nights_programmes_for_its_channels(tmp_path: Path):
    chans = channels_file(tmp_path, [("PlutoTV/us#aaa", "Alpha.us@SD", "Alpha"), ("Roku/all#zzz", "Roku.us@SD", "Roku")])
    previous = tmp_path / "last-night.xml"
    previous.write_text("""<tv>
<programme start="20261016100000 +0000" stop="20261016110000 +0000" channel="Roku.us@SD"><title>Already over</title></programme>
<programme start="20261016113000 +0000" stop="20261016130000 +0000" channel="Roku.us@SD"><title>On now</title></programme>
<programme start="20261016200000 +0000" stop="20261016210000 +0000" channel="Roku.us@SD"><title>Tonight</title></programme>
<programme start="20261016200000 +0000" stop="20261016210000 +0000" channel="Alpha.us@SD"><title>Old Alpha</title></programme>
</tv>""")
    out, logs = tmp_path / "out.xml", []
    native.grab_imjh(chans, out, 2, logs.append, base_url=site_files(tmp_path), now=NOW, previous=previous)
    titles = {p.findtext("title") for p in ET.parse(out).getroot().findall("programme")}
    assert {"On now", "Tonight", "Alpha news"} <= titles  # Roku/all failed: last night's, from now on
    assert not titles & {"Already over", "Old Alpha"}  # Pluto's own file worked: only tonight's
    assert any("kept 2 programmes from last time" in m for m in logs)


def test_the_time_limit_stops_a_slow_guide(tmp_path: Path):
    chans = channels_file(tmp_path, [("PlutoTV/us#aaa", "Alpha.us@SD", "Alpha")])
    with pytest.raises(RuntimeError):  # its only file ran out of time: kia keeps the previous guide
        native.grab_imjh(chans, tmp_path / "o.xml", 1, lambda m: None, base_url=site_files(tmp_path), now=NOW,
                         deadline=time.monotonic() - 1)  # fmt: skip


def test_a_file_cut_off_mid_download_fails_only_itself(tmp_path: Path, monkeypatch):
    import http.client

    chans = channels_file(tmp_path, [("PlutoTV/us#aaa", "Alpha.us@SD", "Alpha"), ("Plex/us#ccc", "Gamma.us@SD", "Gamma")])
    base = site_files(tmp_path)
    real = native.fetch

    def fetch(url, timeout=120):
        if "Plex" in url:
            raise http.client.IncompleteRead(b"partial")
        return real(url, timeout)

    monkeypatch.setattr(native, "fetch", fetch)
    out, logs = tmp_path / "out.xml", []
    native.grab_imjh(chans, out, 2, logs.append, base_url=base, now=NOW)
    titles = {p.findtext("title") for p in ET.parse(out).getroot().findall("programme")}
    assert "Alpha news" in titles and "Gamma show" not in titles
    assert any("Plex/us: ERR" in m for m in logs)


def test_filtering_a_big_guide_keeps_nothing_it_has_read(tmp_path: Path):
    import tracemalloc

    def guide(n: int) -> io.BytesIO:
        body = "".join(f'<programme start="20261016010000 +0000" stop="20261016020000 +0000" channel="x{i}">'
                       f"<title>Show {i}</title></programme>" for i in range(n))  # fmt: skip
        return io.BytesIO(f"<tv>{body}</tv>".encode())

    def peak(n: int) -> int:
        stream = guide(n)
        tracemalloc.start()
        native.filter_xmltv(stream, {"x0": ["X"]}, NOW.replace(hour=0), NOW.replace(hour=23), lambda s: None)
        top = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return top

    assert peak(40000) < peak(4000) * 3  # ten times the programmes, about the same memory


def test_filter_reports_the_channels_it_wrote():
    seen: set[str] = set()
    native.filter_xmltv(io.BytesIO(PLUTO.encode()), {"aaa": ["Alpha.us"], "zzz": ["Zed"]}, NOW.replace(hour=0),
                        NOW.replace(hour=23), lambda s: None, seen=seen)  # fmt: skip
    assert seen == {"Alpha.us"}


def test_a_damaged_gzip_file_fails_only_itself(tmp_path: Path):
    base = site_files(tmp_path)
    damaged = tmp_path / "imjh" / "Plex" / "us.xml.gz"
    data = bytearray(damaged.read_bytes())
    data[len(data) // 2] ^= 0xFF  # one flipped byte in the compressed part: zlib.error, not OSError
    damaged.write_bytes(bytes(data))
    chans = channels_file(tmp_path, [("PlutoTV/us#aaa", "Alpha.us@SD", "Alpha"), ("Plex/us#ccc", "Gamma.us@SD", "Gamma")])
    out, logs = tmp_path / "out.xml", []
    native.grab_imjh(chans, out, 2, logs.append, base_url=base, now=NOW)
    titles = {p.findtext("title") for p in ET.parse(out).getroot().findall("programme")}
    assert "Alpha news" in titles and any("Plex/us: ERR" in m for m in logs)
