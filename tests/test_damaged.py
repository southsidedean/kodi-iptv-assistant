"""Files kia wrote, damaged into another shape (valid JSON, but a list or null where kia wrote
an object; text where it wrote a number), count as missing: kia carries on, and a damaged
file doesn't stop a run, a Dashboard page, or a tool for good."""

import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kia import alerts, auth, backup, channels, cli, doctor, files, metrics, numbers, profiles, shows, sources, status
from kia.config import load
from kia.m3u import Entry

DAMAGED = ([], None, "text", 3)


def test_the_readers_helpers():
    assert files.obj([1]) == {} and files.obj({"a": 1}) == {"a": 1}
    assert files.items("abc") == [] and files.items(["a", 1, None]) == ["a"] and files.items([{}, "x"], dict) == [{}]


@pytest.mark.parametrize("value", DAMAGED)
def test_state_files_in_another_shape_count_as_missing(tmp_path: Path, value):
    for name in ("sources.json", "channels.json", "alert-state.json", "plex-players.json", "admin.json"):
        (tmp_path / name).write_text(json.dumps(value))
    assert sources.load_state(tmp_path) == {"on": [], "off": [], "added": []}
    assert channels.load(tmp_path) == {"favorites": [], "removed": {}, "numbers": {"tv": {}, "radio": {}}}
    assert alerts.load_state(tmp_path) == {"site_failures": {}}
    assert metrics.known_players(tmp_path) == []
    a = auth.Auth(tmp_path)
    assert not a.check("anything") and a.source == "generated"
    said = []
    a.ensure(said.append)  # a damaged password file is replaced, so the Dashboard isn't locked for good
    assert any("damaged" in m for m in said) and (tmp_path / "admin-password.txt").exists()
    assert a.check((tmp_path / "admin-password.txt").read_text().strip())


def test_inner_values_in_another_shape_are_left_out(tmp_path: Path):
    (tmp_path / "sources.json").write_text(json.dumps({"on": "https://x", "added": ["https://y", {"url": "https://z"}]}))
    assert sources.load_state(tmp_path) == {"on": [], "off": [], "added": [{"url": "https://z"}]}  # not letter by letter
    (tmp_path / "channels.json").write_text(json.dumps({"numbers": [], "favorites": "CNN.us"}))
    assert channels.load(tmp_path)["numbers"] == {"tv": {}, "radio": {}} and channels.load(tmp_path)["favorites"] == []
    (tmp_path / "alert-state.json").write_text(json.dumps({"channels": "many", "guide_channels": 900, "site_failures": {"a.tv": "x", "b.tv": 2}}))
    assert alerts.load_state(tmp_path) == {"guide_channels": 900, "site_failures": {"b.tv": 2}}


def test_damaged_channel_numbers_are_set_aside(tmp_path: Path):
    path = tmp_path / "channel-numbers.json"
    path.write_text('{"numbers": {"tv": {"CNN.us": {"num')  # cut short
    n = numbers.ChannelNumbers(path)
    e = Entry("CNN", "http://a/1", {"tvg-id": "CNN.us"})
    n.assign([e], "2026-10-25T00:00:00+00:00")
    assert e.attrs["tvg-chno"] == "1" and (tmp_path / "channel-numbers.json.bad").exists()


def test_the_dashboard_still_updates(tmp_path: Path):
    out, state = tmp_path / "output", tmp_path / "state"
    out.mkdir()
    state.mkdir()
    for name in ("run.json", "alert-state.json", "settings.json"):
        (state / name).write_text("null")
    status.update(out, state, None, result="ok")  # used to raise AttributeError after the run's work was done
    assert json.loads((out / "status.json").read_text())["result"] == "ok"


def test_profiles_with_damaged_records(tmp_path: Path):
    out, state = tmp_path / "output", tmp_path / "state"
    out.mkdir()
    state.mkdir()
    (out / "curated.m3u").write_text('#EXTM3U\n#EXTINF:-1 tvg-id="A.us" group-title="News",A\nhttp://a/1\n')
    (state / "channel-sources.json").write_text("[]")
    (out / "profiles.json").write_text('{"profiles": ["x", {"name": 3}]}')
    assert "profiles" in profiles.rebuild(out, state, None, None, lambda m: None)
    # A guide.xml that can't be read leaves no half-written profile guides behind.
    (out / "guide.xml").write_text("<tv><programme")
    with pytest.raises(Exception):  # noqa: B017 - the parser's own error
        profiles.write_guides(out, {"news": {"A.us"}})
    assert not list(out.glob("*.tmp"))
    # Text that profiles.toml can't hold is refused, not a 500 with a stray file.
    for title in ("Kids\x7f", "Kids\ud800"):
        with pytest.raises(ValueError, match="control characters"):
            profiles.from_rules("kids", {"title": title})


def test_backups_damaged_or_odd(tmp_path: Path, capsys):
    def archive(manifest, extra=()):
        path = tmp_path / f"b{len(list(tmp_path.glob('b*')))}.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for name, data in (("kia-backup.json", json.dumps(manifest).encode()), *extra):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return path

    for manifest in ([], "x", None, {"files": "kia.toml"}):
        with pytest.raises(ValueError, match="another shape"):
            backup.read_manifest(archive(manifest))
    good = archive({"files": ["kia.toml"]}, [("kia.toml", b"")])
    cut = tmp_path / "cut.tar.gz"
    cut.write_bytes(good.read_bytes()[:40])
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://example.com/a.m3u\n")
    assert cli.main(["restore", "-c", str(tmp_path / "kia.toml"), str(cut)]) == 1  # one line, not a traceback
    said = capsys.readouterr()
    assert "error:" in said.err + said.out
    home = tmp_path / "home"
    home.mkdir()
    (home / "state").mkdir()
    restored, _ = backup.restore(archive({"files": []}, [("state", b"not a folder")]), home, tmp_path / "safety", lambda m: None)
    assert restored == [] and (home / "state").is_dir()


def test_doctor_and_mcp_with_damaged_files(tmp_path: Path):
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://example.com/a.m3u\n")
    for d in ("output", "state"):
        (tmp_path / d).mkdir()
    out = tmp_path / "output"
    (out / "status.json").write_text(json.dumps({"result": "ok", "last_run": "x", "sources": [{"name": "us", "ok": "x", "dead": 3}]}))
    (out / "curated.m3u").write_text('#EXTM3U\n#EXTINF:-1 tvg-id="CNN.us",CNN\nhttp://a/1\n')
    (out / "report.json").write_text(json.dumps({"streams": [{"name": "CNN", "tvg_id": "CNN.us", "url": ["x"], "selected": True},
                                                             {"name": ["CNN"], "url": "http://a/1"}]}))  # fmt: skip
    (out / "pluto.json").write_text(json.dumps({"channels": [{"id": ["x"], "fallback_minutes": 1}]}))
    cfg = load(tmp_path / "kia.toml")
    doctor.overview(cfg)  # used to raise AttributeError on last_run
    assert doctor.channel(cfg, "cnn")
    doctor.pluto(cfg)
    from kia import mcp

    (out / "guide-preview.json").write_text(json.dumps({"channels": {"CNN.us": [["a", "b", "c"], [1, 2]]}}))
    assert mcp._whats_on(cfg, {"query": "CNN"}, None, now=0)["channels"][0]["on_now"] is None


def test_jellyfin_times_without_a_zone_are_utc():
    now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    assert doctor._jellyfin_refresh({"Status": "Completed", "EndTimeUtc": "2026-10-25T01:00:00.1234567"}, now)[0].level == doctor.OK
    assert shows._jellyfin_time("2026-10-25T01:00:00") == datetime(2026, 10, 25, 1, tzinfo=timezone.utc)
