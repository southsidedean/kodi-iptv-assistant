import os
import time
from datetime import timedelta
from pathlib import Path

import pytest

from kia import cli, grab, mediareq, status
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
    monkeypatch.setattr(cli, "_daemon_serve", lambda cfg, args, **kw: calls.append(("serve", kw)))
    monkeypatch.setattr(cli, "_daemon_refresh_loop", lambda cfg, args, cut: calls.append(("loop", cut)))
    monkeypatch.setattr(cli, "_idle_forever", lambda: calls.append("idle"))
    return calls


def test_daemon_web_serves_and_never_runs_the_refresh_loop(tmp_path: Path, monkeypatch):
    calls = _stub_daemon_halves(monkeypatch, instance(tmp_path), cut_short=True)
    assert cli.main(["daemon-web", "-c", str(tmp_path / "kia.toml")]) == 0
    # Serves with media work routed to the other half, then waits; the refresh is daemon-media's.
    assert calls == [("serve", {"media_via_request": True}), "idle"]


def test_daemon_media_refreshes_and_never_serves(tmp_path: Path, monkeypatch):
    calls = _stub_daemon_halves(monkeypatch, instance(tmp_path), cut_short=False)
    cli.main(["daemon-media", "-c", str(tmp_path / "kia.toml")])
    assert calls == [("loop", False)]  # only the refresh loop; nothing is served


def test_daemon_runs_both_halves_in_order(tmp_path: Path, monkeypatch):
    # The single-container default is unchanged: it serves first, then runs the refresh loop,
    # carrying through whether the last run was cut short.
    calls = _stub_daemon_halves(monkeypatch, instance(tmp_path), cut_short=True)
    cli.main(["daemon", "-c", str(tmp_path / "kia.toml")])
    assert calls == [("serve", {}), ("loop", True)]  # serves in-process (no media routing), then refreshes


# 0.9 privilege separation: daemon-media runs the on-demand media work the web half leaves over
# the request channel (kia.mediareq). These drive one request at a time through the handler.

class _Done:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _media_cfg(tmp_path):
    cfg = cli.load(Path(instance(tmp_path)))
    return cfg, tmp_path / "media-service"


def test_daemon_media_runs_a_guide_refresh_request(tmp_path: Path, monkeypatch):
    cfg, service = _media_cfg(tmp_path)
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: _Done(0, "Refreshed 3 of 3 quick guide sources\n"))
    mediareq.ask(cfg.state_dir, service, "refresh-guide")
    assert cli._handle_media_request(cfg, service) is True
    answer = mediareq.result(service)
    assert answer["state"] == "done" and answer["exit"] == "0"
    assert "Refreshed 3 of 3" in answer["output"]
    assert mediareq.waiting(cfg.state_dir, service) is None  # answered: slot free


def test_a_guide_refresh_answer_hides_logins(tmp_path: Path, monkeypatch):
    cfg, service = _media_cfg(tmp_path)
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: _Done(1, "", "failed on http://dad:secret@host/p.m3u\n"))
    mediareq.ask(cfg.state_dir, service, "refresh-guide")
    cli._handle_media_request(cfg, service)
    out = mediareq.result(service)["output"]
    assert "secret" not in out and "dad:secret" not in out  # the login is masked before the answer


def test_a_kind_media_cannot_run_yet_is_answered_not_run(tmp_path: Path, monkeypatch):
    cfg, service = _media_cfg(tmp_path)
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: pytest.fail("the stream tools must not run for this"))
    mediareq.ask(cfg.state_dir, service, "check", url="http://example.com/p.m3u")
    assert cli._handle_media_request(cfg, service) is True
    answer = mediareq.result(service)
    assert answer["state"] == "failed" and answer["exit"] == "2"


def test_daemon_media_runs_a_channel_test_request(tmp_path: Path, monkeypatch):
    cfg, service = _media_cfg(tmp_path)
    from kia import api
    monkeypatch.setattr(api, "test_channel",
                        lambda c, body: {"key": body["key"], "name": "CNN", "ok": True, "seconds": 1.2})
    mediareq.ask(cfg.state_dir, service, "test-channel", key="CNN.us")
    assert cli._handle_media_request(cfg, service) is True
    answer = mediareq.result(service)
    assert answer["state"] == "done"
    import json as _json
    assert _json.loads(answer["output"]) == {"key": "CNN.us", "name": "CNN", "ok": True, "seconds": 1.2}


def test_a_channel_test_that_cannot_run_is_a_failed_answer(tmp_path: Path, monkeypatch):
    cfg, service = _media_cfg(tmp_path)
    from kia import api
    def refuse(c, body):
        raise api.ApiError(400, "That channel's address isn't a stream kia can test.")
    monkeypatch.setattr(api, "test_channel", refuse)
    mediareq.ask(cfg.state_dir, service, "test-channel", key="CNN.us")
    cli._handle_media_request(cfg, service)
    answer = mediareq.result(service)
    assert answer["state"] == "failed" and "isn't a stream" in answer["output"]


def test_a_failing_handler_becomes_a_failed_answer(tmp_path: Path, monkeypatch):
    cfg, service = _media_cfg(tmp_path)

    def boom(_cfg):
        raise RuntimeError("the guide grabber fell over")

    monkeypatch.setattr(cli, "_refresh_guide_for_request", boom)
    mediareq.ask(cfg.state_dir, service, "refresh-guide")
    assert cli._handle_media_request(cfg, service) is True  # survives, so the servicer keeps going
    answer = mediareq.result(service)
    assert answer["state"] == "failed" and "RuntimeError" in answer["output"]


def test_handle_media_request_does_nothing_when_there_is_no_request(tmp_path: Path):
    cfg, service = _media_cfg(tmp_path)
    assert cli._handle_media_request(cfg, service) is False


def test_daemon_web_tells_the_api_to_route_media_work(tmp_path: Path):
    # The web half builds its API config with media_via_request on, so stream-tool work goes to
    # the media half; the single-container daemon leaves it off and runs the work itself.
    cfg = cli.load(Path(instance(tmp_path)))
    assert cli.api_config(cfg, media_via_request=True).media_via_request is True
    assert cli.api_config(cfg).media_via_request is False


def test_daemon_media_starts_the_request_servicer(tmp_path: Path, monkeypatch):
    cfg_path = instance(tmp_path)
    started = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            started["target"], started["name"] = target, name

        def start(self):
            started["started"] = True

    monkeypatch.setattr(cli, "_daemon_setup", lambda args: (cli.load(Path(cfg_path)), False))
    monkeypatch.setattr(cli, "_daemon_refresh_loop", lambda *a: None)
    monkeypatch.setattr(cli.threading, "Thread", FakeThread)
    cli.main(["daemon-media", "-c", str(tmp_path / "kia.toml")])
    assert started.get("started") and started["target"] is cli._serve_media_requests
