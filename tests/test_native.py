import gzip
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
