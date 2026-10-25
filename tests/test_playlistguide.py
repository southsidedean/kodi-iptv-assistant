import gzip
import time
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kia import cli, grab, m3u, playlistguide, xmltv
from kia.config import EpgConfig
from kia.probe import ProbeResult

from test_deploy import server  # noqa: F401 - a fixture

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def guide(channels: dict[str, list[str]], now: datetime = NOW) -> bytes:
    """An XMLTV guide: channel id -> titles, one an hour from midnight UTC."""
    t = lambda h: xmltv.format_time(now.replace(hour=0) + timedelta(hours=h))
    body = "".join(
        f'<programme start="{t(h)}" stop="{t(h + 1)}" channel="{cid}"><title>{title}</title></programme>'
        for cid, titles in channels.items()
        for h, title in enumerate(titles)
    )
    return f'<?xml version="1.0"?><tv><channel id="x"/>{body}</tv>'.encode()


def test_header_guide_urls():
    assert m3u.header_guide_urls('#EXTM3U url-tvg="https://a/roku.xml"\n#EXTINF:-1,A\nu\n') == ["https://a/roku.xml"]
    assert m3u.header_guide_urls('﻿#EXTM3U x-tvg-url="https://a/1.xml.gz, https://b/2.xml.gz,file:///etc/passwd"') == [
        "https://a/1.xml.gz", "https://b/2.xml.gz"]  # fmt: skip
    assert m3u.header_guide_urls("#EXTM3U\n") == [] and m3u.header_guide_urls("#EXTINF:-1,A\n") == []


def test_plan_groups_channels_by_playlist():
    def entry(tid, source, urls=("https://g/x.xml",)):
        return m3u.Entry(tid, f"http://s/{tid}", {"tvg-id": tid}, source=source, guide_urls=urls)

    spec = playlistguide.plan([entry("A", "roku"), entry("B", "roku"), entry("C", "us", ()), entry("D", "My List!")])
    assert spec == {
        "playlist-roku": {"urls": ["https://g/x.xml"], "channels": {"A": ["A"], "B": ["B"]}},
        "playlist-My-List": {"urls": ["https://g/x.xml"], "channels": {"D": ["D"]}},
    }
    assert playlistguide.sites_by_channel(spec)["A"] == ["playlist-roku"]


def test_download_keeps_only_our_channels(server, tmp_path: Path):
    www, base = server
    (www / "a.xml.gz").write_bytes(gzip.compress(guide({"A": ["News", "Weather"], "Z": ["Not ours"]})))
    (www / "b.xml").write_bytes(guide({"B": ["Movie"]}))
    info = {"urls": [f"{base}/a.xml.gz", f"{base}/missing.xml", f"{base}/b.xml"], "channels": {"A": ["A.us"], "B": ["B"]}}
    out = tmp_path / "g.xml"
    logs = []
    counts = playlistguide.download(info, out, 1, logs.append, NOW)
    assert counts == {f"{base}/a.xml.gz": 2, f"{base}/b.xml": 1}
    text = out.read_text()
    assert 'channel="A.us"' in text and "Not ours" not in text and "<title>Movie</title>" in text
    assert any("missing.xml: ERR" in m for m in logs)  # one bad guide doesn't stop the rest

    with pytest.raises(RuntimeError):
        playlistguide.download({"urls": [f"{base}/missing.xml"], "channels": {"A": ["A"]}}, out, 1, logs.append, NOW)
    with pytest.raises(RuntimeError):  # past site_time_limit: none is started
        playlistguide.download(info, out, 1, logs.append, NOW, deadline=time.monotonic() - 1)


def test_check_map_grab_guide_use_the_playlists_guide(server, tmp_path: Path, monkeypatch):
    www, base = server
    now = datetime.now(timezone.utc)
    (www / "roku.xml").write_bytes(guide({"r1": [f"Show {h}" for h in range(24)]}, now))
    (tmp_path / "roku.m3u").write_text(f'#EXTM3U url-tvg="{base}/roku.xml"\n#EXTINF:-1 tvg-id="r1",Roku One\nhttp://s/1\n')
    (tmp_path / "sources.txt").write_text(f"{tmp_path / 'roku.m3u'} roku\n")
    repo = tmp_path / "epg"
    (repo / ".git").mkdir(parents=True)  # an existing (empty) checkout: no iptv-org sites
    (repo / "sites").mkdir()
    (tmp_path / "kia.toml").write_text(f'[check]\nffprobe = "true"\n[epg]\nrepo_dir = "{repo}"\n')
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)

    async def fake_probe(url, **kw):
        return ProbeResult(ok=True, height=720)

    monkeypatch.setattr(cli, "probe", fake_probe)
    cfg = str(tmp_path / "kia.toml")
    assert cli.main(["check", "-c", cfg]) == 0
    spec = json.loads((tmp_path / "work" / "playlist-guides.json").read_text())
    assert spec == {"playlist-roku": {"urls": [f"{base}/roku.xml"], "channels": {"r1": ["r1"]}}}
    assert cli.main(["map", "-c", cfg]) == 0
    assert json.loads((tmp_path / "work" / "mapping.json").read_text()) == {"r1": ["playlist-roku"]}
    assert "| Covered by their playlist's own guide | 1 |" in (tmp_path / "output" / "mapping-report.md").read_text()
    assert cli.main(["grab", "-c", cfg]) == 0
    assert cli.main(["guide", "-c", cfg]) == 0
    text = (tmp_path / "output" / "guide.xml").read_text()
    assert "<title>Show 12</title>" in text
    # Name blocks only after the real listings end.
    first_fill = text.index('channel="r1" kia-placeholder')
    assert text.rindex("<title>Show 23</title>") < first_fill

    (tmp_path / "kia.toml").write_text(f'[check]\nffprobe = "true"\n[epg]\nrepo_dir = "{repo}"\nplaylist_guides = false\n')
    assert cli.main(["check", "-c", cfg]) == 0
    assert json.loads((tmp_path / "work" / "playlist-guides.json").read_text()) == {}


def test_failed_playlist_guide_keeps_the_previous_one(tmp_path: Path):
    cfg = EpgConfig(work_dir=tmp_path / "work")
    previous = tmp_path / "work" / "guides" / "playlist-x.xml"
    previous.parent.mkdir(parents=True)
    previous.write_text("<tv>old</tv>")
    (r,) = grab.grab_playlist_guides(cfg, {"playlist-x": {"urls": ["http://127.0.0.1:9/g.xml"], "channels": {"a": ["a"]}}},
                                     lambda m: None)  # fmt: skip
    assert r.returncode == 1 and previous.read_text() == "<tv>old</tv>"


def test_quick_refresh_fetches_only_the_guides_that_had_our_channels(server, tmp_path: Path):
    www, base = server
    now = datetime.now(timezone.utc)
    (www / "ours.xml").write_bytes(guide({"A": ["News"]}, now))
    (www / "other.xml").write_bytes(guide({"Z": ["Not ours"]}, now))
    cfg = EpgConfig(work_dir=tmp_path / "work")
    spec = {"playlist-free": {"urls": [f"{base}/ours.xml", f"{base}/other.xml"], "channels": {"A": ["A"]}}}
    grab.grab_playlist_guides(cfg, spec, lambda m: None)  # nightly: tries both, notes which helped
    used = json.loads((tmp_path / "work" / "playlist-guides-used.json").read_text())
    assert used == {"playlist-free": [f"{base}/ours.xml"]}
    (www / "other.xml").unlink()  # a quick refresh that tried it would log an error
    grab.grab_playlist_guides(cfg, spec, lambda m: None, quick=True)
    log = (tmp_path / "work" / "logs" / "playlist-free.log").read_text()
    assert "ours.xml" in log and "other.xml" not in log


def test_a_guide_that_fails_tonight_keeps_its_channels_and_its_place(server, tmp_path: Path):
    www, base = server
    now = datetime.now(timezone.utc)
    (www / "a.xml").write_bytes(guide({"A": ["News"]}, now))
    (www / "b.xml").write_bytes(guide({"B": [f"Film {h}" for h in range(24)]}, now))
    cfg = EpgConfig(work_dir=tmp_path / "work")
    spec = {"playlist-free": {"urls": [f"{base}/a.xml", f"{base}/b.xml"], "channels": {"A": ["A"], "B": ["B"]}}}
    grab.grab_playlist_guides(cfg, spec, lambda m: None)
    (www / "b.xml").unlink()  # tonight b.xml is gone
    (r,) = grab.grab_playlist_guides(cfg, spec, lambda m: None)
    assert r.returncode == 0  # a.xml still worked
    text = (tmp_path / "work" / "guides" / "playlist-free.xml").read_text()
    later = f"Film {min(now.hour + 2, 23)}"
    assert "<title>News</title>" in text and f"<title>{later}</title>" in text  # B's still to come, from last time
    assert "<title>Film 0</title>" not in text or now.hour == 0  # but not what's over
    used = json.loads((tmp_path / "work" / "playlist-guides-used.json").read_text())
    assert used == {"playlist-free": [f"{base}/a.xml", f"{base}/b.xml"]}  # quick refreshes keep trying b.xml
