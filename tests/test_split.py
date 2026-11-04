"""The 0.9 split: which half (KIA_ROLE) does what, alerts the media half hands to the web half,
the web half's own chores, and what the media half says about the parts of the kia folder it
can't see (0.9.1)."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

from kia import alerts, cli, config, doctor, files, mediareq


@pytest.fixture
def sent(monkeypatch):
    """What notify would have sent, by ntfy (the other ways go the same route)."""
    out = []
    monkeypatch.setattr(alerts, "send_ntfy", lambda url, event, title, message, log: out.append((event, title, message)))
    return out


AC = alerts.AlertConfig(ntfy_url="http://ntfy.invalid/topic")


def folder_with_config(tmp_path: Path) -> str:
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://example.com/us.m3u\n")
    return str(tmp_path / "kia.toml")


def handed(kia: Path) -> list[Path]:
    folder = kia / "media-service" / "alerts"
    return sorted(p for p in folder.iterdir() if p.suffix == ".json") if folder.exists() else []


# The media half hands alerts over instead of sending them.

def test_the_media_half_leaves_alerts_for_the_web_half(tmp_path: Path, monkeypatch, sent):
    monkeypatch.setenv("KIA_ROLE", "media")
    state = tmp_path / "state"
    said = []
    assert alerts.notify("run_failed", "kia: nightly run failed", "exit 1", state, AC, said.append) is True
    assert alerts.notify("daily_summary", "kia: done", "ok", state, AC, said.append) is False  # a notice
    assert sent == [] and not (state / "alerts.log").exists()  # media sends nothing and logs no alert
    files = handed(tmp_path)
    assert len(files) == 2 and json.loads(files[0].read_text())["event"] == "run_failed"
    assert any("for the web half" in m for m in said)


def test_the_media_half_removes_its_own_after_a_day(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KIA_ROLE", "media")
    state = tmp_path / "state"
    alerts.hand_off("run_failed", "old", "x", state, lambda m: None)
    old = handed(tmp_path)[0]
    os.utime(old, (time.time() - 2 * alerts.HAND_OFF_KEEP,) * 2)
    alerts.hand_off("run_failed", "new", "x", state, lambda m: None)
    assert [json.loads(p.read_text())["title"] for p in handed(tmp_path)] == ["new"]


# The web half sends them, once each, checked as untrusted.

def test_the_web_half_sends_each_handed_over_alert_once(tmp_path: Path, monkeypatch, sent):
    state = tmp_path / "state"
    monkeypatch.setenv("KIA_ROLE", "media")
    alerts.hand_off("run_failed", "kia: nightly run failed", "exit 1", state, lambda m: None)
    alerts.hand_off("site_failing", "kia: a site fails", "tvguide.com", state, lambda m: None)
    monkeypatch.setenv("KIA_ROLE", "web")
    assert alerts.send_handed_off(state, AC, lambda m: None) == 2
    assert [e for e, _t, _m in sent] == ["run_failed", "site_failing"]  # in the order media left them
    assert "nightly run failed" in (state / "alerts.log").read_text()  # the web half keeps the log
    assert alerts.send_handed_off(state, AC, lambda m: None) == 0 and len(sent) == 2  # not again
    alerts.hand_off("run_failed", "kia: again", "exit 1", state, lambda m: None)
    assert alerts.send_handed_off(state, AC, lambda m: None) == 1 and sent[-1][1].endswith("kia: again")


def _plant(kia: Path, name: str, data) -> None:
    folder = kia / "media-service" / "alerts"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(data if isinstance(data, str) else json.dumps(data))


def test_the_web_half_skips_what_isnt_a_real_alert(tmp_path: Path, sent):
    # A media half that a stream took over can write anything in its own folder.
    state = tmp_path / "state"
    _plant(tmp_path, "20261103T010000-000000001-0000aaaa.json", {"event": "no_such_event", "title": "t", "message": "m"})
    _plant(tmp_path, "20261103T010000-000000002-0000aaab.json", {"event": "run_failed", "title": ["t"], "message": "m"})
    _plant(tmp_path, "20261103T010000-000000003-0000aaac.json", "[1, 2]")
    _plant(tmp_path, "../escape.json", {"event": "run_failed", "title": "t", "message": "m"})
    _plant(tmp_path, "not-a-handed-name.json", {"event": "run_failed", "title": "t", "message": "m"})
    _plant(tmp_path, "20261103T010000-000000004-0000aaad.json",
           {"event": "run_failed", "title": "line one\nline two " + "x" * 400,
            "message": "from http://user:hunter22@example.com/live.m3u " + "y" * 5000})  # fmt: skip
    assert alerts.send_handed_off(state, AC, lambda m: None) == 1
    (event, title, message), = sent
    assert event == "run_failed" and "\n" not in title and len(title) <= 220  # kia's name may be added
    assert "hunter22" not in message and len(message) <= 2000


def test_the_web_half_sends_a_few_at_a_time_and_nothing_stale(tmp_path: Path, sent):
    state = tmp_path / "state"
    for i in range(alerts.HAND_OFF_BATCH + 5):
        _plant(tmp_path, f"20261103T010000-{i:09d}-0000aaaa.json", {"event": "run_failed", "title": f"t{i}", "message": "m"})
    stale = "20261103T020000-000000000-0000bbbb.json"
    _plant(tmp_path, stale, {"event": "run_failed", "title": "stale", "message": "m"})
    old = tmp_path / "media-service" / "alerts" / stale
    os.utime(old, (time.time() - 2 * alerts.HAND_OFF_KEEP,) * 2)
    assert alerts.send_handed_off(state, AC, lambda m: None) == alerts.HAND_OFF_BATCH  # the rest wait for the next look
    assert alerts.send_handed_off(state, AC, lambda m: None) == 5  # then the rest; the stale one is skipped
    assert not any("stale" in t for _e, t, _m in sent)


def test_without_anything_handed_over_the_web_half_sends_nothing(tmp_path: Path, sent):
    assert alerts.send_handed_off(tmp_path / "state", AC, lambda m: None) == 0 and sent == []


# Which half does what.

@pytest.mark.parametrize("command", ["run", "check", "refresh-guide", "grab", "sync"])
def test_the_web_half_wont_run_the_stream_commands(tmp_path: Path, monkeypatch, capsys, command):
    config = folder_with_config(tmp_path)
    monkeypatch.setenv("KIA_ROLE", "web")
    monkeypatch.setattr(cli, "REFRESHES", set())  # nothing may actually run if the guard fails
    assert cli.main([command, "-c", config]) == 2
    assert "docker compose exec kia-media python3 -m kia " + command in capsys.readouterr().err


def test_the_web_half_wont_probe_a_stream(monkeypatch, capsys):
    monkeypatch.setenv("KIA_ROLE", "web")
    assert cli.main(["probe", "http://example.com/a.m3u8"]) == 2
    assert "kia-media" in capsys.readouterr().err


def test_the_media_half_doesnt_keep_plex_in_step(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "main", ran.append)
    monkeypatch.setenv("KIA_PLEX_URL", "http://plex.invalid:32400")
    monkeypatch.setenv("KIA_ROLE", "media")
    cli.keep_plex_in_step("kia.toml")
    assert ran == []  # it has no Plex token; the web half does this
    monkeypatch.setenv("KIA_ROLE", "web")
    cli.keep_plex_in_step("kia.toml")
    assert ran == [["plex-tuners", "-c", "kia.toml", "--after-refresh"]]


def test_the_media_half_doesnt_make_a_password_or_certificate(tmp_path: Path, monkeypatch):
    made = []
    monkeypatch.setattr(cli, "secure_setup", made.append)
    args = cli.argparse.Namespace(config=folder_with_config(tmp_path), at="04:00", guide_every=0)
    monkeypatch.setenv("KIA_ROLE", "media")
    cli._daemon_setup(args)
    assert made == []  # no private/ or tls/ on this half; a password made here would land in state/
    monkeypatch.setenv("KIA_ROLE", "web")
    cli._daemon_setup(args)
    assert len(made) == 1


def test_the_media_half_keeps_its_liveness_file_fresh(tmp_path: Path, monkeypatch):
    class Stop(Exception):
        pass

    def stop(seconds):
        raise Stop

    monkeypatch.setattr(mediareq.time, "sleep", stop)
    with pytest.raises(Stop):
        mediareq.keep_alive(tmp_path / "media-service")
    assert time.time() - (tmp_path / "media-service" / "alive").stat().st_mtime < 60


# The web half's chores, in place of the refresh loop.

def test_the_web_halfs_chores(tmp_path: Path, monkeypatch):
    cfg = cli.load(Path(folder_with_config(tmp_path)))
    guide = cfg.output_dir / "guide.xml.gz"
    guide.parent.mkdir(parents=True)
    guide.write_bytes(b"old")
    did, looks = [], iter(range(3))

    def sleep(seconds):
        try:
            n = next(looks)
        except StopIteration:
            raise KeyboardInterrupt from None  # ends the loop, as Ctrl-C would
        if n == 1:  # a refresh on the media half wrote a new guide
            os.utime(guide, (time.time() + 60,) * 2)

    monkeypatch.setattr(cli.time, "sleep", sleep)
    monkeypatch.setattr(cli.alerts, "send_handed_off", lambda state, ac, log: did.append("alerts"))
    monkeypatch.setattr(cli, "keep_plex_in_step", lambda config: did.append("plex"))
    monkeypatch.setattr(cli, "secure_setup", lambda c: did.append("renew"))
    monkeypatch.setattr(cli, "RENEW_EVERY", 0)
    args = cli.argparse.Namespace(config=str(tmp_path / "kia.toml"))
    cli._web_chores(cfg, args)
    # Alerts every look; Plex only after the new guide (not for the one already there); the
    # certificate when it's due (every look here).
    assert did == ["alerts", "renew", "alerts", "plex", "renew", "alerts", "renew"]


# The Dashboard's tile for kia-media, from its liveness file (kia can't reach it to knock).

def test_kia_medias_tile_comes_from_its_liveness_file(tmp_path: Path):
    from kia import stack

    folder = tmp_path / "media-service"
    assert not stack.media_alive(folder)  # never started
    folder.mkdir()
    (folder / "alive").touch()
    assert stack.media_alive(folder)
    os.utime(folder / "alive", (time.time() - 5 * 60,) * 2)
    assert not stack.media_alive(folder)  # stopped a few minutes ago
    tiles = stack.check([], knock=lambda host, port: True, media=lambda: True)
    assert tiles == [{"service": "kia-media", "title": "kia-media", "ok": True}]
    assert stack.check([], knock=lambda host, port: True) == []  # one container: no tile


# What the media half can't see (0.9.1): compose.yaml mounts only part of the kia folder.


def test_copies_of_env_are_found_by_name(tmp_path: Path):
    for name in ("env.before-stack-20261024-134455", ".env.bak-20261021", ".env", "env.before-pin-1", "status.lock"):
        (tmp_path / name).write_text("x")
    (tmp_path / ".env.d").mkdir()  # a folder isn't a copy
    assert files.env_copies(tmp_path) == [".env", ".env.bak-20261021", "env.before-pin-1", "env.before-stack-20261024-134455"]
    assert files.env_copies(tmp_path / "missing") == []


def test_the_media_half_names_copies_of_env_it_can_read(tmp_path: Path, monkeypatch):
    folder_with_config(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "env.before-stack-20261024-134455").write_text("KIA_PLEX_TOKEN=x\n")
    cfg = config.load(tmp_path / "kia.toml")
    said = []
    monkeypatch.setattr(cli, "log", said.append)
    monkeypatch.setattr(cli, "_daemon_setup", lambda args: (cfg, False))
    monkeypatch.setattr(cli, "_daemon_media_serve", lambda cfg, args: None)
    monkeypatch.setattr(cli, "_daemon_refresh_loop", lambda cfg, args, cut_short: None)
    monkeypatch.setattr(cli.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    cli.cmd_daemon_media(None)
    assert any("env.before-stack-20261024-134455" in s and "stack-service/" in s for s in said)
    assert not any("KIA_PLEX_TOKEN" in s for s in said)  # names only, never what's inside


def test_kia_doctor_names_them_only_when_kia_media_runs(tmp_path: Path, monkeypatch):
    folder_with_config(tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "status.json").write_text(json.dumps({"result": "ok"}))
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / ".env.bak-20261021").write_text("x")
    cfg = config.load(tmp_path / "kia.toml")
    title = "Copies of .env are in state/, where kia-media can read them"
    monkeypatch.setenv("KIA_ROLE", "web")
    found = [f for f in doctor.overview(cfg).findings if f.title == title]
    assert found and found[0].level == doctor.PROBLEM and found[0].evidence == ["state/.env.bak-20261021"]
    monkeypatch.delenv("KIA_ROLE")  # without Docker: one service, nothing to keep them from
    assert title not in [f.title for f in doctor.overview(cfg).findings]


def test_the_media_half_says_when_it_cant_see_a_sources_file(tmp_path: Path, monkeypatch):
    folder_with_config(tmp_path)
    monkeypatch.setenv("KIA_SOURCES_FILE", "my-list.txt")
    with pytest.raises(FileNotFoundError):
        config.load(tmp_path / "kia.toml")  # anywhere else, the usual error
    monkeypatch.setenv("KIA_ROLE", "media")
    with pytest.raises(ValueError, match=re.escape("my-list.txt: kia-media can't see it")):
        config.load(tmp_path / "kia.toml")
    (tmp_path / "my-list.txt").write_text("https://example.com/uk.m3u\n")
    assert config.load(tmp_path / "kia.toml").sources[0].url == "https://example.com/uk.m3u"


def test_the_media_half_says_when_its_profiles_file_is_out_of_sight(tmp_path: Path, monkeypatch):
    folder_with_config(tmp_path)
    monkeypatch.setenv("KIA_PROFILES_FILE", "my-profiles.toml")
    assert config.load(tmp_path / "kia.toml").profiles_file == tmp_path / "my-profiles.toml"
    monkeypatch.setenv("KIA_ROLE", "media")
    with pytest.raises(ValueError, match=re.escape("kia-media can't see it. With Docker, keep the profiles file in profiles/")):
        config.load(tmp_path / "kia.toml")
    for inside in ("profiles/profiles.toml", "profiles/family.toml"):
        monkeypatch.setenv("KIA_PROFILES_FILE", inside)
        assert config.load(tmp_path / "kia.toml").profiles_file == tmp_path / inside
    monkeypatch.setenv("KIA_PROFILES_FILE", "profiles/../my-profiles.toml")  # out again
    with pytest.raises(ValueError):
        config.load(tmp_path / "kia.toml")


def test_the_media_half_says_when_it_cant_see_a_playlist_of_your_own(tmp_path: Path, monkeypatch):
    folder_with_config(tmp_path)
    cfg = config.load(tmp_path / "kia.toml")
    src = config.Source(name="mine", url=str(tmp_path / "mine.m3u"))
    monkeypatch.setenv("KIA_ROLE", "media")
    with pytest.raises(FileNotFoundError, match=re.escape("kia-media can't see it. With Docker, keep your own playlist files in playlists/")):
        cli.fetch_source(cfg, src)
    (tmp_path / "mine.m3u").write_text("#EXTM3U\n")
    assert cli.fetch_source(cfg, src) == "#EXTM3U\n"
