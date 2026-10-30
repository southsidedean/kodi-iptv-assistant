import os
import time
from datetime import timedelta
from pathlib import Path

from kia import cli, grab, status
import contextlib


def instance(tmp_path: Path, extra: str = "") -> str:
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    (tmp_path / "kia.toml").write_text(extra)
    channels = tmp_path / "work" / "channels"
    channels.mkdir(parents=True)
    for site in ("i.mjh.nz", "kink.nl", "tvguide.com"):
        (channels / f"{site}.channels.xml").write_text("<channels/>")
    return str(tmp_path / "kia.toml")


def test_refresh_guide_downloads_only_the_quick_sources(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_UPSTREAM_URL", raising=False)
    cfg = instance(tmp_path)
    calls = []
    monkeypatch.setattr(grab, "grab_all", lambda epg, sites, log: calls.append(("sites", sites)) or [])
    monkeypatch.setattr(grab, "grab_playlist_guides",
                        lambda epg, spec, log, quick: calls.append(("playlists", spec, quick)) or [])  # fmt: skip
    monkeypatch.setattr(cli, "cmd_guide", lambda args: calls.append(("guide",)) or 0)
    assert cli.main(["refresh-guide", "-c", cfg]) == 0
    # tvguide.com needs iptv-org's downloader (minutes, lots of memory): left for the nightly run.
    assert calls == [("sites", ["i.mjh.nz", "kink.nl"]), ("playlists", {}, True), ("guide",)]


def test_refresh_guide_with_an_upstream_copies_its_guide(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_UPSTREAM_URL", raising=False)
    cfg = instance(tmp_path, '[upstream]\nurl = "http://home:8780"\n')
    monkeypatch.setattr(cli, "cmd_sync", lambda args: cli.EXIT_UPSTREAM_UNAVAILABLE)
    monkeypatch.setattr(grab, "grab_all", lambda *a: (_ for _ in ()).throw(AssertionError("no downloads")))
    assert cli.main(["refresh-guide", "-c", cfg]) == 0  # away from home: keep the last copy, quietly


def test_guide_refresh_timing(tmp_path: Path):
    cfg = cli.load(Path(instance(tmp_path)))
    guide = cfg.output_dir / "guide.xml.gz"
    guide.parent.mkdir(parents=True)
    guide.write_bytes(b"x")
    two_hours_ago = time.time() - 7200
    os.utime(guide, (two_hours_ago, two_hours_ago))
    due = cli.guide_age_due(cfg, timedelta(hours=4))
    assert abs((due - cli.datetime.now().astimezone()).total_seconds() - 7200) < 5
    cli.touch_guide(cfg)
    assert cli.guide_age_due(cfg, timedelta(hours=4)) > due


def test_daemon_option_and_status_line(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KIA_GUIDE_REFRESH_HOURS", "6")
    seen = {}
    monkeypatch.setattr(cli, "cmd_daemon", lambda args: seen.setdefault("every", args.guide_every) and 0)
    cli.main(["daemon"])
    assert seen["every"] == 6.0
    s = status.collect(tmp_path, tmp_path, {"guide_updated": "2026-10-19T18:00:00+00:00"}, None)
    assert "Guide updated" in status.render_html(s) and s["guide_updated"].startswith("2026-10-19")


def test_daemon_refreshes_the_guide_between_nightly_runs(tmp_path: Path, monkeypatch):
    cfg_path = instance(tmp_path)
    cfg = cli.load(Path(cfg_path))
    guide = cfg.output_dir / "guide.xml.gz"
    guide.parent.mkdir(parents=True)
    guide.write_bytes(b"x")
    now = cli.datetime.now().astimezone()
    os.utime(guide, (now.timestamp() - 5 * 3600,) * 2)  # built 5 hours ago: a refresh is due now
    nightly = now + timedelta(hours=9)
    monkeypatch.setattr(cli, "next_run_local", lambda at: nightly)
    refreshes, waits = [], []
    real_main = cli.main

    def fake_main(argv):
        if argv[0] == "refresh-guide":
            refreshes.append(1)
            return 1  # fails without an exception: must not loop
        return real_main(argv)

    class Stop(Exception):
        pass

    def fake_wait(when):
        waits.append(when)
        if when == nightly:
            raise Stop

    # A fake clock: marking the guide records the time the daemon waited until.
    monkeypatch.setattr(cli, "touch_guide", lambda c: os.utime(guide, (max(waits[-1], now).timestamp(),) * 2))

    monkeypatch.setattr(cli, "main", fake_main)
    monkeypatch.setattr(cli, "wait_until", fake_wait)
    monkeypatch.setattr(cli, "update_status", lambda *a, **k: None)
    with contextlib.suppress(Stop):
        real_main(["daemon", "-c", cfg_path, "--guide-every", "4"])
    # Refreshes now, +4 h, and +8 h; the next (+12 h) is after the nightly run (+9 h), which
    # rebuilds the guide anyway, so the daemon waits for that. Failed refreshes don't loop.
    assert len(refreshes) == 3 and waits[-1] == nightly


# 0.9 privilege separation: `daemon` splits into a serving half (`daemon-web`) and a refresh
# half (`daemon-media`). The single-container `daemon` still runs both, in that order. These
# check which half each command runs, with the three pieces stubbed so nothing actually serves
# or loops forever.

def _stub_daemon_halves(monkeypatch, cfg_path: str, cut_short: bool):
    calls = []
    monkeypatch.setattr(cli, "_daemon_setup", lambda args: (cli.load(Path(cfg_path)), cut_short))
    monkeypatch.setattr(cli, "_daemon_serve", lambda cfg, args: calls.append("serve"))
    monkeypatch.setattr(cli, "_daemon_refresh_loop", lambda cfg, args, cut: calls.append(("loop", cut)))
    monkeypatch.setattr(cli, "_idle_forever", lambda: calls.append("idle"))
    return calls


def test_daemon_web_serves_and_never_runs_the_refresh_loop(tmp_path: Path, monkeypatch):
    calls = _stub_daemon_halves(monkeypatch, instance(tmp_path), cut_short=True)
    assert cli.main(["daemon-web", "-c", str(tmp_path / "kia.toml")]) == 0
    assert calls == ["serve", "idle"]  # serves, then waits; the refresh is daemon-media's job


def test_daemon_media_refreshes_and_never_serves(tmp_path: Path, monkeypatch):
    calls = _stub_daemon_halves(monkeypatch, instance(tmp_path), cut_short=False)
    cli.main(["daemon-media", "-c", str(tmp_path / "kia.toml")])
    assert calls == [("loop", False)]  # only the refresh loop; nothing is served


def test_daemon_runs_both_halves_in_order(tmp_path: Path, monkeypatch):
    # The single-container default is unchanged: it serves first, then runs the refresh loop,
    # carrying through whether the last run was cut short.
    calls = _stub_daemon_halves(monkeypatch, instance(tmp_path), cut_short=True)
    cli.main(["daemon", "-c", str(tmp_path / "kia.toml")])
    assert calls == ["serve", ("loop", True)]
