"""The nightly run keeps going when one piece fails, and treats third-party input carefully."""

import argparse
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
    cfg.host_delay_ms = 0  # this test is about the limit, not the spacing
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
    # The child is gone too (give a busy machine a few seconds to reap it).
    deadline = _time.monotonic() + 5
    while subprocess.run(["pgrep", "-f", str(marker)], capture_output=True, check=False).returncode != 1:
        assert _time.monotonic() < deadline, "the site's child process outlived the time limit"
        _time.sleep(0.05)


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
    assert min(gaps) >= 0.04  # starts host_delay_ms (50) apart, give or take the clock's resolution


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


HOSTILE = """
const fs = require("fs"), cp = require("child_process");
const out = process.argv.find(a => a.startsWith("--output=")).slice(9);
const tries = {
  secret: () => fs.readFileSync(process.env.SECRET_FILE || "SECRET_PATH", "utf8"),
  write: () => fs.writeFileSync("PWNED_PATH", "x"),
  shell: () => cp.execSync("id").toString(),
  token: () => process.env.KIA_PLEX_TOKEN || (() => { throw new Error("none") })(),
};
const seen = Object.entries(tries).map(([k, f]) => { try { f(); return k + "=ALLOWED" } catch (e) { return k + "=blocked" } });
fs.writeFileSync(out, seen.join("\\n"));
"""


def test_the_grabber_runs_in_nodes_sandbox(tmp_path: Path, monkeypatch):
    if grab.node_permission() is None:
        pytest.skip("this Node.js has no sandbox (or there's no Node.js)")
    secret, pwned = tmp_path / ".env", tmp_path / "pwned"
    secret.write_text("KIA_ADMIN_PASSWORD=hunter2")
    repo = tmp_path / "vendor" / "epg"
    (repo / ".kia").mkdir(parents=True)
    (repo / ".kia" / "grab.cjs").write_text(HOSTILE.replace("SECRET_PATH", str(secret)).replace("PWNED_PATH", str(pwned)))
    monkeypatch.setenv("KIA_PLEX_TOKEN", "plex-secret")
    cfg = EpgConfig(work_dir=tmp_path / "work", repo_dir=repo, runner="local", native_grabbers=False)
    channels = tmp_path / "work" / "channels" / "evil.tv.channels.xml"
    channels.parent.mkdir(parents=True)
    channels.write_text("<channels/>")
    r = grab.grab_site(cfg, "evil.tv", channels)
    assert r.returncode == 0, r.log.read_text()
    assert r.output.read_text().split() == ["secret=blocked", "write=blocked", "shell=blocked", "token=blocked"]
    assert not pwned.exists()


def test_the_grabber_sees_none_of_kias_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KIA_SMTP_PASSWORD", "x")
    monkeypatch.setenv("TZ", "Europe/Amsterdam")
    env = grab.epg_env(EpgConfig(work_dir=tmp_path / "work"))
    assert "KIA_SMTP_PASSWORD" not in env and env["TZ"] == "Europe/Amsterdam"
    assert env["HOME"] == env["TMPDIR"] == str((tmp_path / "work" / "epg-tmp").resolve())


def test_old_compiled_scripts_never_outlive_their_checkout(tmp_path: Path):
    repo = tmp_path / "vendor" / "epg"
    (repo / ".kia").mkdir(parents=True)
    (repo / ".kia" / "grab.cjs").write_text("// from yesterday's code")
    cfg = EpgConfig(work_dir=tmp_path / "work", repo_dir=repo, runner="local")
    said = []
    assert not grab.build_scripts(cfg, said.append)  # the scripts moved: can't compile
    assert not (repo / ".kia" / "grab.cjs").exists() and "without Node's sandbox" in said[0]
    assert grab.sandboxed(cfg, "grab", [], [], []) is None  # so kia falls back, and says so


def test_kia_always_uses_its_own_esbuild(tmp_path: Path, monkeypatch):
    repo = tmp_path / "vendor" / "epg"
    repo.mkdir(parents=True)
    cfg = EpgConfig(work_dir=tmp_path / "work", repo_dir=repo, runner="local")
    ours = tmp_path / "vendor" / "kia-tools" / "node_modules" / ".bin" / "esbuild"
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[:2])
        if cmd[:2] == ["npm", "install"]:
            assert "--ignore-scripts" in cmd and f"esbuild@{grab.ESBUILD}" in cmd
            ours.parent.mkdir(parents=True)
            ours.write_text("")
        return subprocess.CompletedProcess(cmd, 0, stdout=grab.ESBUILD + "\n")

    monkeypatch.setattr(grab.subprocess, "run", run)
    assert grab._esbuild(cfg, lambda m: None) == ours.resolve()
    assert grab._esbuild(cfg, lambda m: None) == ours.resolve()  # installed once, then reused
    assert calls.count(["npm", "install"]) == 1
    theirs = repo / "node_modules" / ".bin" / "esbuild"
    theirs.parent.mkdir(parents=True)
    theirs.write_text("")
    # Never the checkout's own: it comes from iptv-org's dependencies, and compiling runs it
    # outside Node's sandbox.
    assert grab._esbuild(cfg, lambda m: None) == ours.resolve()


@pytest.mark.parametrize("extra, named", [
    ('[filters]\nexclude_name_patterns = ["("]\n', "exclude_name_patterns"),
    ('history_days = "30"\n', "history_days"),
    ("timeout = 0\n", "timeout"),
    ("host_delay_ms = -1\n", "host_delay_ms"),
    ("grace_failures = 1.5\n", "grace_failures"),
    ("[alerts]\ndrop_percent = 150\n", "drop_percent"),
    ("[alerts]\nsite_fail_runs = 0\n", "site_fail_runs"),
    ("[epg]\nmax_connections = 0\n", "max_connections"),
    ('[epg]\nsite_time_limit = "30"\n', "site_time_limit"),
    ('[epg]\ndays = "3"\n', "days"),
])  # fmt: skip
def test_a_bad_value_in_kia_toml_is_named_at_the_start(tmp_path: Path, monkeypatch, capsys, extra, named):
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    monkeypatch.delenv("KIA_EPG_DAYS", raising=False)
    (tmp_path / "list.m3u").write_text("#EXTM3U\n")
    cfg_path = make_cfg(tmp_path, extra)
    with pytest.raises(ValueError, match=named):
        load(cfg_path)
    assert cli.main(["check", "-c", str(cfg_path)]) == 2  # said in one line, no traceback
    assert named in capsys.readouterr().err


def test_good_values_in_kia_toml_still_load(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    (tmp_path / "list.m3u").write_text("#EXTM3U\n")
    cfg = load(make_cfg(tmp_path, 'timeout = 7.5\nhistory_days = 10\n[filters]\nexclude_name_patterns = ["(?i)adult"]\n'
                                  "[alerts]\ndrop_percent = 12.5\n"))  # fmt: skip
    assert cfg.timeout == 7.5 and cfg.history_days == 10 and cfg.alerts.drop_percent == 12.5


@pytest.mark.parametrize("name, value", [("KIA_PORT", "eighty"), ("KIA_API_PORT", "-1"), ("KIA_GUIDE_REFRESH_HOURS", "4h"),
                                         ("KIA_RUN_ON_START", "maybe")])  # fmt: skip
def test_a_bad_number_in_env_is_named_without_a_traceback(tmp_path: Path, monkeypatch, capsys, name, value):
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    (tmp_path / "list.m3u").write_text("#EXTM3U\n")
    monkeypatch.setenv(name, value)
    assert cli.main(["daemon", "-c", str(make_cfg(tmp_path))]) == 2
    assert name in capsys.readouterr().err


def test_numbers_in_env_are_read_only_for_the_commands_that_use_them(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KIA_PORT", "eighty")
    args = argparse.Namespace(port=None, guide_every=None)
    monkeypatch.setenv("KIA_GUIDE_REFRESH_HOURS", "2.5")
    with pytest.raises(ValueError, match="KIA_PORT"):
        cli.env_default(args)
    monkeypatch.setenv("KIA_PORT", "9000")
    cli.env_default(args)
    assert (args.port, args.guide_every) == (9000, 2.5)
    given = argparse.Namespace(port=1234)
    cli.env_default(given)
    assert given.port == 1234  # the command line wins
    cli.env_default(argparse.Namespace())  # a command without those options reads none of them


def test_a_missing_kia_toml_is_one_line(tmp_path: Path, capsys):
    assert cli.main(["check", "-c", str(tmp_path / "kia.toml")]) == 2
    assert "kia.toml not found" in capsys.readouterr().err


def test_a_page_with_no_channels_keeps_the_last_good_copy(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    (tmp_path / "list.m3u").write_text("#EXTM3U\n")
    cfg = load(make_cfg(tmp_path))
    src = cfg.sources[0].__class__(name="us", url="https://example.org/us.m3u")
    good = '#EXTM3U\n#EXTINF:-1 tvg-id="A.us",A\nhttp://a/1\n'
    monkeypatch.setattr(cli, "fetch", lambda url: good)
    assert cli.fetch_source(cfg, src) == good
    monkeypatch.setattr(cli, "fetch", lambda url: "<html>Service unavailable</html>")
    assert cli.fetch_source(cfg, src) == good  # the error page wasn't saved over it
    assert (cfg.epg.work_dir / "sources" / "us.m3u").read_text() == good
    assert not list((cfg.epg.work_dir / "sources").glob("*.tmp"))


def test_a_broken_history_file_is_set_aside(tmp_path: Path):
    from kia.history import History

    path = tmp_path / "history.json"
    path.write_text('{"http://a/1": {"checks": 3')  # cut short
    history = History(path)
    assert history.records == {}
    assert (tmp_path / "history.json.bad").read_text().startswith('{"http://a/1"')
    history.record("http://a/1", ProbeResult(ok=True, height=720))
    history.save()
    assert "http://a/1" in History(path).records
    path.write_text("[]")  # valid, but not what kia writes
    assert History(path).records == {}


def test_the_retry_tests_no_more_at_once_than_concurrency(tmp_path: Path, monkeypatch):
    seen = {"now": 0, "max": 0}

    async def fake_probe(url, **kw):
        seen["now"] += 1
        seen["max"] = max(seen["max"], seen["now"])
        await asyncio.sleep(0.01)
        seen["now"] -= 1
        return ProbeResult(ok=False, error="timed out")

    monkeypatch.setattr(cli, "probe", fake_probe)
    cfg = load(make_cfg(tmp_path))
    cfg.concurrency, cfg.host_delay_ms = 1, 0
    asyncio.run(cli.probe_all([m3u.Entry(str(i), f"http://host{i}/x") for i in range(6)], cfg, verbose=False))
    assert seen["max"] == 1


def test_a_first_fetch_that_failed_is_tried_again(tmp_path: Path, monkeypatch):
    origin = make_origin(tmp_path, branch="main")
    monkeypatch.setattr(grab, "install_deps", lambda cfg, log: None)
    real_run = subprocess.run
    monkeypatch.setattr(grab.subprocess, "run", lambda cmd, *a, **k: real_run(cmd, *a, **k) if cmd[0] == "git"
                        else subprocess.CompletedProcess(cmd, 0))  # fmt: skip
    repo = tmp_path / "vendor" / "epg"
    cfg = EpgConfig(repo_dir=repo, work_dir=tmp_path / "work", git_url=str(tmp_path / "gone"), runner="local")
    with pytest.raises(subprocess.CalledProcessError):  # no internet at the first start
        grab.ensure_repo(cfg, update=False, log=lambda m: None)
    assert (repo / ".git").is_dir() and not (repo / "package-lock.json").exists()
    cfg.git_url = str(origin)
    grab.ensure_repo(cfg, update=False, log=lambda m: None)  # kia map, by hand, without --update
    assert (repo / "package-lock.json").read_text() == "v1"
    assert not grab._never_fetched(repo)


def test_a_grab_that_cant_start_fails_only_its_own_site(tmp_path: Path, monkeypatch):
    cfg = EpgConfig(repo_dir=tmp_path / "epg", work_dir=tmp_path / "work", native_grabbers=False, parallel_sites=2)
    (cfg.repo_dir / "node_modules").mkdir(parents=True)
    channels = tmp_path / "work" / "channels"
    channels.mkdir(parents=True)
    for site in ("a.com", "b.com"):
        (channels / f"{site}.channels.xml").write_text("<channels/>")

    def grab_site(cfg, site, path):
        if site == "a.com":
            raise FileNotFoundError("docker")
        return grab.GrabResult(site, 0, 1.0, tmp_path / "b.log", tmp_path / "b.xml")

    monkeypatch.setattr(grab, "grab_site", grab_site)
    monkeypatch.setattr(grab, "sandboxed", lambda *a: True)
    said = []
    results = {r.site: r.returncode for r in grab.grab_all(cfg, None, said.append)}
    assert results == {"a.com": 1, "b.com": 0}
    assert any("a.com: couldn't start" in m for m in said)


def test_a_playlist_from_the_internet_cant_send_tests_onto_your_network(tmp_path: Path, monkeypatch):
    tested = []

    async def fake_probe(url, **kw):
        tested.append(url)
        return ProbeResult(ok=True, height=720)

    monkeypatch.setattr(cli, "probe", fake_probe)
    cfg = load(make_cfg(tmp_path))
    cfg.host_delay_ms = 0
    theirs = [m3u.Entry(n, u) for n, u in [("Router", "http://192.0.2.1/admin"), ("Here", "http://localhost:9090/x"),
                                           ("Docker", "http://[::1]:3000/"), ("TV", "http://8.8.8.8/live.m3u8")]]  # fmt: skip
    mine = m3u.Entry("Tuner", "http://192.168.1.20:5004/auto/v1", yours=True)
    results = asyncio.run(cli.probe_all([*theirs, mine], cfg, verbose=False))
    assert sorted(tested) == ["http://192.168.1.20:5004/auto/v1", "http://8.8.8.8/live.m3u8"]
    assert results["http://192.0.2.1/admin"].error == cli.LAN_REFUSED
    assert "lan" in cli.LAN_REFUSED and not results["http://localhost:9090/x"].ok

    # The same stream in one of your playlists and one from the internet: yours decides.
    tested.clear()
    asyncio.run(cli.probe_all([m3u.Entry("A", "http://192.168.1.20/a"), m3u.Entry("A", "http://192.168.1.20/a", yours=True)],
                              cfg, verbose=False))  # fmt: skip
    assert tested == ["http://192.168.1.20/a"]


def test_which_playlists_are_yours(tmp_path: Path):
    from kia import lan
    from kia.config import parse_sources

    a, b, c, d, e = parse_sources(f"{tmp_path / 'mine.m3u'}\nhttp://192.0.2.5:34400/m3u/threadfin.m3u tf\n"
                                  "https://8.8.8.8/us.m3u us\nhttps://8.8.8.8/eu.m3u eu lan radio\n"
                                  "radio-browser: country=US\n")  # fmt: skip
    assert [s.is_yours for s in (a, b, c, d, e)] == [True, True, False, True, False]
    assert (d.name, d.radio, d.allow_lan) == ("eu", True, True)
    assert lan.is_local("localhost") and lan.is_local("[fd00::5]") and lan.is_local("100.101.102.103")  # Tailscale's
    assert not lan.is_local("8.8.8.8") and not lan.is_local("") and not lan.is_local("no-such-host.invalid")


def test_the_check_marks_entries_from_your_own_playlists(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    (tmp_path / "list.m3u").write_text('#EXTM3U\n#EXTINF:-1 tvg-id="A.us",A\nhttp://192.168.1.20/a\n')
    cfg = load(make_cfg(tmp_path))
    (e,) = cli.load_entries(cfg, None)
    assert e.yours
