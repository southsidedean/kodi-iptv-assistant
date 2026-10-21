"""The nightly run keeps going when one piece fails, and treats third-party input carefully."""

import asyncio
import gzip
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kia import alerts, cli, dnscheck, epgmap, grab, m3u, upstream, xmltv
from kia.config import EpgConfig, load
from kia.probe import ProbeResult

from test_deploy import git, server  # noqa: F401 - server is a fixture


def test_bad_ntfy_url_is_logged_not_raised(tmp_path: Path):
    messages = []
    cfg = alerts.AlertConfig(ntfy_url="ntfy.sh/no-scheme")
    assert alerts.notify("run_failed", "t", "m", tmp_path, cfg, messages.append) is True
    assert any("ntfy notification failed" in m for m in messages)


def test_truncated_upstream_guide_is_rejected(server, tmp_path: Path):
    www, base = server
    dest = tmp_path / "guide.xml.gz"
    good = gzip.compress(b'<?xml version="1.0"?><tv>' + b"<x/>" * 50_000 + b"</tv>")
    (www / "guide.xml.gz").write_bytes(good)
    upstream.download_guide(base, dest)
    (www / "guide.xml.gz").write_bytes(good[: len(good) // 2])
    with pytest.raises(upstream.UpstreamError):
        upstream.download_guide(base, dest)
    assert dest.read_bytes() == good  # the previous copy is kept


def test_site_with_only_old_listings_does_not_win():
    now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    old = [(now - timedelta(days=5, hours=h + 1), now - timedelta(days=5, hours=h)) for h in range(24)]
    choices = xmltv.choose_sources(["A.us"], {"stale.tv": {"A.us": old}}, {"A.us": ["stale.tv"]}, now)
    assert choices["A.us"].site is None  # gets a placeholder with the channel's name instead


def test_unsafe_site_names_are_ignored(tmp_path: Path):
    path = tmp_path / "sites" / "x" / "x.channels.xml"
    path.parent.mkdir(parents=True)
    rows = [("good.tv", "1"), ("../../evil", "2"), ("/abs", "3"), ("a..b", "4")]
    body = "".join(f'<channel site="{s}" site_id="{i}" xmltv_id="C{i}.us">C</channel>' for s, i in rows)
    path.write_text(f"<channels>{body}</channels>")
    assert [c.site for c in epgmap.load_site_channels(tmp_path)] == ["good.tv"]


def test_only_playback_kodiprops_are_kept():
    text = (
        "#EXTM3U\n#EXTINF:-1,A\n"
        "#KODIPROP:inputstream=inputstream.adaptive\n"
        "#KODIPROP:inputstream.adaptive.manifest_type=mpd\n"
        "#KODIPROP:something.else=1\n"
        "http://a/1.mpd\n"
    )
    (entry,) = m3u.parse(text)
    assert entry.kodiprops == ["inputstream=inputstream.adaptive", "inputstream.adaptive.manifest_type=mpd"]


def make_cfg(tmp_path: Path, extra: str = "") -> Path:
    (tmp_path / "sources.txt").write_text(str(tmp_path / "list.m3u") + "\n")
    (tmp_path / "kia.toml").write_text('[check]\nffprobe = "true"\nper_host = 2\n' + extra)
    return tmp_path / "kia.toml"


def test_only_network_streams_reach_ffprobe(tmp_path: Path, monkeypatch):
    called = []

    async def fake_probe(url, **kw):
        called.append(url)
        return ProbeResult(ok=True, height=720)

    monkeypatch.setattr(cli, "probe", fake_probe)
    cfg = load(make_cfg(tmp_path))
    entries = [m3u.Entry(n, u) for n, u in [("A", "http://a/x"), ("F", "file:///etc/passwd"), ("O", "-i x"), ("P", "/tmp/x")]]
    results = asyncio.run(cli.probe_all(entries, cfg, verbose=False))
    assert called == ["http://a/x"]
    assert all(not results[u].ok and "not a network stream" in results[u].error for u in ("file:///etc/passwd", "-i x", "/tmp/x"))


def test_retry_keeps_the_per_server_limit(tmp_path: Path, monkeypatch):
    seen = {"now": 0, "max": 0, "calls": 0}

    async def fake_probe(url, **kw):
        seen["calls"] += 1
        seen["now"] += 1
        seen["max"] = max(seen["max"], seen["now"])
        await asyncio.sleep(0.01)
        seen["now"] -= 1
        return ProbeResult(ok=False, error="timed out")

    monkeypatch.setattr(cli, "probe", fake_probe)
    cfg = load(make_cfg(tmp_path))
    entries = [m3u.Entry(str(i), f"http://one.host/{i}") for i in range(10)]
    asyncio.run(cli.probe_all(entries, cfg, verbose=False))
    assert seen["calls"] == 20 and seen["max"] <= cfg.per_host


def test_test_runs_leave_the_live_playlist_alone(tmp_path: Path, monkeypatch):
    async def fake_probe(url, **kw):
        return ProbeResult(ok=True, height=720)

    monkeypatch.setattr(cli, "probe", fake_probe)
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    cfg_path = make_cfg(tmp_path)
    (tmp_path / "list.m3u").write_text('#EXTM3U\n#EXTINF:-1 tvg-id="A.us",A\nhttp://a/1\n')
    live = tmp_path / "output" / "curated.m3u"
    live.parent.mkdir()
    live.write_text("live")
    assert cli.main(["check", "-c", str(cfg_path), "--limit", "1"]) == 0
    assert live.read_text() == "live"
    assert "http://a/1" in (tmp_path / "work" / "check-test" / "curated.m3u").read_text()
    assert cli.main(["check", "-c", str(cfg_path)]) == 0
    assert "http://a/1" in live.read_text()


def test_grab_with_nothing_mapped(tmp_path: Path):
    cfg_path = make_cfg(tmp_path, f'[epg]\nrepo_dir = "{tmp_path / "epg"}"\n')
    (tmp_path / "epg" / ".git").mkdir(parents=True)  # an existing checkout, so nothing is fetched
    assert cli.main(["grab", "-c", str(cfg_path)]) == 0
    assert cli.main(["grab", "-c", str(cfg_path), "--site", "nope.tv"]) == 2


def test_damaged_guide_file_is_skipped(tmp_path: Path):
    cfg = load(make_cfg(tmp_path))
    now = datetime.now(timezone.utc)
    t = lambda h: xmltv.format_time(now + timedelta(hours=h))
    good = tmp_path / "good.xml"
    good.write_text(f'<tv><programme start="{t(0)}" stop="{t(2)}" channel="A.us"><title>x</title></programme></tv>')
    bad = tmp_path / "bad.xml"
    bad.write_text("<tv><programme")
    entries = [m3u.Entry("A", "u", {"tvg-id": "A.us"}), m3u.Entry("B", "u2", {"tvg-id": "B.us"})]
    cli.build_guide(cfg, entries, {"good.tv": good, "bad.tv": bad}, {"A.us": ["good.tv"], "B.us": ["bad.tv"]})
    guide = (tmp_path / "output" / "guide.xml").read_text()
    assert "<title>x</title>" in guide and 'channel="B.us" kia-placeholder="1"' in guide


def test_grab_dns_ignores_names_that_resolve_again(monkeypatch, tmp_path: Path):
    log = tmp_path / "site.log"
    log.write_text("getaddrinfo ENOTFOUND flaky.example\ngetaddrinfo ENOTFOUND down.example\n")
    monkeypatch.setattr(cli, "resolves_locally", lambda h: h == "flaky.example")
    asked = []
    monkeypatch.setattr(cli, "lookup_all", lambda hosts, r: asked.extend(hosts) or {
        h: dnscheck.PublicLookup(h, "nxdomain", []) for h in hosts})  # fmt: skip
    monkeypatch.setattr(cli, "log", lambda m: None)
    result = grab.GrabResult("s.tv", 1, 0.0, log, tmp_path / "out.xml")
    cli.report_grab_dns([result], ("8.8.8.8",))
    assert asked == ["down.example"]


def make_origin(tmp_path: Path, branch: str = "main") -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", branch)
    git(origin, "config", "user.email", "t@example.com")
    git(origin, "config", "user.name", "t")
    (origin / "package-lock.json").write_text("v1")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "one")
    return origin


def test_latest_follows_the_default_branch_and_survives_outages(tmp_path: Path, monkeypatch):
    origin = make_origin(tmp_path, branch="main")  # not called master
    monkeypatch.setattr(grab, "install_deps", lambda cfg, log: None)
    real_run = subprocess.run
    monkeypatch.setattr(grab.subprocess, "run", lambda cmd, *a, **k: real_run(cmd, *a, **k) if cmd[0] == "git"
                        else subprocess.CompletedProcess(cmd, 0))  # fmt: skip
    repo = tmp_path / "vendor" / "epg"
    cfg = EpgConfig(repo_dir=repo, work_dir=tmp_path / "work", git_url=str(origin), runner="local")
    assert cfg.git_ref == "latest"
    grab.ensure_repo(cfg, update=False, log=lambda m: None)
    assert (repo / "package-lock.json").read_text() == "v1"

    # A planted hook in the checkout doesn't run when kia updates it.
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {tmp_path / 'pwned'}\n")
    hook.chmod(0o755)
    grab.ensure_repo(cfg, update=True, log=lambda m: None)
    assert not (tmp_path / "pwned").exists()

    messages = []
    cfg.git_url = str(tmp_path / "gone")  # GitHub is down
    grab.ensure_repo(cfg, update=True, log=messages.append)
    assert (repo / "package-lock.json").read_text() == "v1"
    assert any("Couldn't update" in m for m in messages)


def test_a_site_that_runs_too_long_is_stopped(tmp_path: Path, monkeypatch):
    import time as _time

    marker = tmp_path / "child-alive"
    # Like npm: a parent that starts a child, both of which would run for a minute.
    monkeypatch.setattr(grab, "runner_prefix", lambda cfg, name=None: ["sh", "-c", f"(sleep 60; touch {marker}) & sleep 60", "sh"])
    cfg = EpgConfig(work_dir=tmp_path / "work", repo_dir=tmp_path, runner="local", native_grabbers=False, site_time_limit=0.02)
    previous = tmp_path / "work" / "guides" / "slow.tv.xml"
    previous.parent.mkdir(parents=True)
    previous.write_text("<tv>yesterday</tv>")
    channels = tmp_path / "slow.tv.channels.xml"
    channels.write_text("<channels/>")
    start = _time.monotonic()
    r = grab.grab_site(cfg, "slow.tv", channels)
    assert r.returncode == 124 and _time.monotonic() - start < 15
    assert previous.read_text() == "<tv>yesterday</tv>"
    assert "stopped after" in r.log.read_text()
    _time.sleep(0.5)
    assert subprocess.run(["pgrep", "-f", str(marker)], capture_output=True).returncode == 1  # child gone too


def test_per_server_limits_and_spacing(tmp_path: Path, monkeypatch):
    starts = {"jmp2.uk": [], "other.tv": []}
    now = {"jmp2.uk": 0, "other.tv": 0}
    peak = {"jmp2.uk": 0, "other.tv": 0}

    async def fake_probe(url, **kw):
        host = url.split("/")[2]
        starts[host].append(asyncio.get_running_loop().time())
        now[host] += 1
        peak[host] = max(peak[host], now[host])
        await asyncio.sleep(0.6)
        now[host] -= 1
        return ProbeResult(ok=True, height=720)

    monkeypatch.setattr(cli, "probe", fake_probe)
    cfg = load(make_cfg(tmp_path, ""))
    cfg.per_host_limits, cfg.host_delay_ms = {"jmp2.uk": 6}, 50
    entries = [m3u.Entry(str(i), f"http://jmp2.uk/{i}") for i in range(12)] + [m3u.Entry(f"o{i}", f"http://other.tv/{i}") for i in range(4)]
    asyncio.run(cli.probe_all(entries, cfg, verbose=False))
    assert peak == {"jmp2.uk": 6, "other.tv": cfg.per_host}
    gaps = [b - a for a, b in zip(starts["jmp2.uk"], starts["jmp2.uk"][1:], strict=False)]
    assert min(gaps) >= 0.045  # starts at least host_delay_ms apart


def test_per_site_connections_and_delay(tmp_path: Path, monkeypatch):
    seen = []
    monkeypatch.setattr(grab, "runner_prefix", lambda cfg, name=None: ["sh", "-c", 'echo "$@" > "$0"', str(tmp_path / "args")])
    cfg = EpgConfig(work_dir=tmp_path / "work", repo_dir=tmp_path, runner="local", native_grabbers=False,
                    site_connections={"tvpassport.com": 100}, site_delay_ms={"tvpassport.com": 100})  # fmt: skip
    for site in ("tvpassport.com", "other.tv"):
        channels = tmp_path / f"{site}.channels.xml"
        channels.write_text("<channels/>")
        grab.grab_site(cfg, site, channels)
        seen.append((tmp_path / "args").read_text())
    assert "--maxConnections=100" in seen[0] and "--delay=100" in seen[0]
    assert "--maxConnections=5" in seen[1] and "--delay" not in seen[1]
