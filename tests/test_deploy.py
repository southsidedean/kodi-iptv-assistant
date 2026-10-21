import gzip
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kia import cli, grab, serve, upstream, xmltv
from kia.config import EpgConfig, load
from kia.m3u import Entry, write


@pytest.fixture
def server(tmp_path: Path):
    srv = serve.serve_in_background(tmp_path / "www", "127.0.0.1", 0)
    yield tmp_path / "www", f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url: str):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.headers.get_content_type(), resp.headers.get("Cache-Control"), resp.read()


def test_serve_types_and_hides_tmp(server):
    www, base = server
    (www / "curated.m3u").write_text("#EXTM3U\n")
    (www / "guide.xml.gz").write_bytes(gzip.compress(b"<tv/>"))
    (www / "guide.xml.tmp").write_text("partial")
    assert get(f"{base}/curated.m3u")[:2] == ("audio/x-mpegurl", "no-cache")
    ctype, _, body = get(f"{base}/guide.xml.gz")
    assert ctype == "application/gzip" and gzip.decompress(body) == b"<tv/>"
    with pytest.raises(urllib.error.HTTPError) as err:
        get(f"{base}/guide.xml.tmp")
    assert err.value.code == 404


def test_download_guide(server, tmp_path: Path):
    www, base = server
    dest = tmp_path / "cache" / "guide.xml.gz"
    (www / "guide.xml.gz").write_bytes(gzip.compress(b'<?xml version="1.0"?><tv></tv>'))
    upstream.download_guide(base, dest)
    assert gzip.decompress(dest.read_bytes()).startswith(b"<?xml")

    (www / "guide.xml.gz").write_bytes(gzip.compress(b"<html>nope</html>"))
    with pytest.raises(upstream.UpstreamError, match="not an XMLTV guide"):
        upstream.download_guide(base, dest)
    assert gzip.decompress(dest.read_bytes()).startswith(b"<?xml")  # previous copy kept

    with pytest.raises(upstream.UpstreamError):
        upstream.download_guide("http://127.0.0.1:9", dest, timeout=2)


def test_placeholders_are_tagged_and_skipped(tmp_path: Path):
    now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    entries = [Entry("Quiet TV", "u", {"tvg-id": "Quiet.us@SD"})]
    out = tmp_path / "guide.xml"
    choices = xmltv.choose_sources(["Quiet.us@SD"], {}, {}, now)
    xmltv.write_guide(out, entries, choices, {}, placeholders=True, days=1, now=now, placeholder_hours=6)
    progs = ET.parse(out).getroot().findall("programme")
    assert len(progs) == 4 and all(p.get(xmltv.PLACEHOLDER_ATTR) == "1" for p in progs)
    assert xmltv.scan(out) == {}  # another kia instance won't count them as guide data


def make_instance(tmp_path: Path, upstream_url: str | None = None, fallback: str = "grab") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    toml = "[upstream]\n" + (f'url = "{upstream_url}"\n' if upstream_url else "") + f'fallback = "{fallback}"\n'
    (tmp_path / "kia.toml").write_text(toml)
    out = tmp_path / "output"
    out.mkdir(exist_ok=True)
    (out / "curated.m3u").write_text(
        write(
            [
                Entry("Alpha", "u1", {"tvg-id": "Alpha.us@SD"}),
                Entry("Local Only", "u2", {"tvg-id": "Local.us@SD"}),
            ]
        )
    )
    return tmp_path / "kia.toml"


def central_guide(now: datetime) -> bytes:
    t = lambda h: xmltv.format_time(now + timedelta(hours=h))
    progs = "".join(
        f'<programme start="{t(h)}" stop="{t(h + 1)}" channel="Alpha.us@SD"><title>A{h}</title></programme>'
        for h in range(24)
    )
    progs += f'<programme start="{t(0)}" stop="{t(2)}" channel="Gone.us@SD" kia-placeholder="1"><title>Gone</title></programme>'
    return gzip.compress(f'<?xml version="1.0"?><tv>{progs}</tv>'.encode())


def test_sync_from_upstream(server, monkeypatch):
    www, base = server
    (www / "guide.xml.gz").write_bytes(central_guide(datetime.now(timezone.utc)))
    monkeypatch.delenv("KIA_UPSTREAM_URL", raising=False)
    cfg_path = make_instance(www.parent / "laptop", base)
    assert cli.main(["sync", "-c", str(cfg_path)]) == 0

    root = ET.parse(www.parent / "laptop" / "output" / "guide.xml").getroot()
    by_channel = {}
    for p in root.findall("programme"):
        by_channel.setdefault(p.get("channel"), []).append(p)
    real = [p for p in by_channel["Alpha.us@SD"] if not p.get("kia-placeholder")]
    filled = [p for p in by_channel["Alpha.us@SD"] if p.get("kia-placeholder")]
    assert len(real) == 24 and filled and min(p.get("start") for p in filled) >= max(p.get("stop") for p in real)
    assert all(p.get("kia-placeholder") for p in by_channel["Local.us@SD"])  # not on the central server
    assert "Gone.us@SD" not in by_channel


def test_sync_unreachable_and_cached_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_UPSTREAM_URL", raising=False)
    cfg_path = make_instance(tmp_path / "a", "http://127.0.0.1:9")
    assert cli.main(["sync", "-c", str(cfg_path)]) == cli.EXIT_UPSTREAM_UNAVAILABLE

    cfg_path = make_instance(tmp_path / "b", "http://127.0.0.1:9", fallback="cached")
    cache = tmp_path / "b" / "work" / "upstream" / "guide.xml.gz"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(central_guide(datetime.now(timezone.utc)))
    assert cli.main(["sync", "-c", str(cfg_path)]) == 0
    assert (tmp_path / "b" / "output" / "guide.xml.gz").exists()


@pytest.mark.parametrize(
    "upstream_url, sync_rc, expected_steps",
    [
        (None, None, ["check", "map", "grab", "guide"]),
        ("http://central:8780", 0, ["check", "sync"]),
        ("http://central:8780", cli.EXIT_UPSTREAM_UNAVAILABLE, ["check", "sync", "map", "grab", "guide"]),
    ],
)
def test_pipeline_uses_upstream_when_available(tmp_path, monkeypatch, upstream_url, sync_rc, expected_steps):
    monkeypatch.delenv("KIA_UPSTREAM_URL", raising=False)
    cfg_path = make_instance(tmp_path, upstream_url)
    ran = []

    def fake_main(argv):
        ran.append(argv[0])
        return sync_rc if argv[0] == "sync" else 0

    monkeypatch.setattr(cli, "main", fake_main)
    assert cli.run_pipeline(str(cfg_path), update_epg=False) == 0
    assert ran == expected_steps


def test_wait_until_returns_for_past_times():
    cli.wait_until(datetime.now().astimezone() - timedelta(minutes=5))


def test_new_settings(tmp_path: Path, monkeypatch):
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    cfg = tmp_path / "kia.toml"
    for name in ("KIA_EPG_GIT_URL", "KIA_EPG_GIT_REF", "KIA_UPSTREAM_URL", "KIA_UPSTREAM_FALLBACK"):
        monkeypatch.delenv(name, raising=False)
    cfg.write_text("[check]\nhistory_days = 7\n[epg]\nplaceholder_hours = 3\ngit_ref = \"v1\"\n")
    c = load(cfg)
    assert (c.history_days, c.epg.placeholder_hours, c.epg.git_ref, c.upstream_url) == (7, 3, "v1", None)
    monkeypatch.setenv("KIA_EPG_GIT_REF", "abc123")
    monkeypatch.setenv("KIA_UPSTREAM_URL", "http://central:8780/")
    c = load(cfg)
    assert (c.epg.git_ref, c.upstream_url, c.upstream_fallback) == ("abc123", "http://central:8780", "grab")
    monkeypatch.setenv("KIA_UPSTREAM_FALLBACK", "sometimes")
    with pytest.raises(ValueError, match="fallback"):
        load(cfg)
    monkeypatch.delenv("KIA_UPSTREAM_FALLBACK")
    cfg.write_text("[epg]\nplaceholder_hours = 5\n")
    with pytest.raises(ValueError, match="placeholder_hours"):
        load(cfg)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_ensure_repo_branch_and_commit(tmp_path: Path, monkeypatch):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "master")
    git(origin, "config", "user.email", "t@example.com")
    git(origin, "config", "user.name", "t")
    (origin / "package-lock.json").write_text("v1")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "one")
    first = git(origin, "rev-parse", "HEAD")
    (origin / "package-lock.json").write_text("v2")
    git(origin, "commit", "-q", "-am", "two")
    git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")

    installs, refreshes = [], []
    monkeypatch.setattr(grab, "install_deps", lambda cfg, log: installs.append(1))
    monkeypatch.setattr(grab.subprocess, "run", _passthrough_git(refreshes))
    repo = tmp_path / "vendor" / "epg"
    cfg = EpgConfig(repo_dir=repo, work_dir=tmp_path / "work", git_url=str(origin), git_ref="master", runner="local")

    grab.ensure_repo(cfg, update=False, log=lambda m: None)
    assert (repo / "package-lock.json").read_text() == "v2" and len(installs) == 1
    grab.ensure_repo(cfg, update=False, log=lambda m: None)  # no update requested: nothing happens
    assert len(installs) == 1

    cfg.git_ref = first  # pin to an older commit: lockfile changes, deps reinstall
    (repo / "node_modules").mkdir()
    grab.ensure_repo(cfg, update=True, log=lambda m: None)
    assert (repo / "package-lock.json").read_text() == "v1" and len(installs) == 2
    grab.ensure_repo(cfg, update=True, log=lambda m: None)  # same lockfile: only refresh API data
    assert len(installs) == 2 and refreshes == [["npm", "run", "api:load"]]


def _passthrough_git(refreshes):
    real_run = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, *args, **kwargs)
        refreshes.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    return run


def test_serve_shows_the_status_page(server):
    www, base = server
    (www / "index.html").write_text("<!doctype html><title>kia status</title>")
    ctype, _, body = get(f"{base}/")
    assert ctype == "text/html" and b"kia status" in body


def test_pin_versions_script(tmp_path: Path):
    import shutil

    root = Path(__file__).resolve().parent.parent
    proj = tmp_path / "kia"
    (proj / "scripts").mkdir(parents=True)
    shutil.copy(root / "scripts" / "pin-versions.sh", proj / "scripts")
    (proj / ".env").write_text("KIA_PUBLIC_URL=http://tv.lan:8780\nKIA_NODE_IMAGE=\nKIA_NGINX_IMAGE=\nKIA_EPG_GIT_REF=\n")
    (proj / "vendor" / "epg").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text('#!/bin/sh\necho "${5%%:*}@sha256:abc123"\n')  # docker image inspect --format X IMAGE
    (bin_dir / "git").write_text("#!/bin/sh\necho 0123456789abcdef\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    run = lambda *a: subprocess.run(["sh", "scripts/pin-versions.sh", *a], cwd=proj, env=env, capture_output=True, text=True)
    assert run().returncode == 0
    text = (proj / ".env").read_text()
    assert "KIA_NODE_IMAGE=node@sha256:abc123" in text and "KIA_NGINX_IMAGE=nginx@sha256:abc123" in text
    assert "KIA_EPG_GIT_REF=0123456789abcdef" in text and "KIA_PUBLIC_URL=http://tv.lan:8780" in text
    assert list(proj.glob(".env.bak-*"))
    assert run("--unpin").returncode == 0
    text = (proj / ".env").read_text()
    assert "KIA_NODE_IMAGE=\n" in text and "KIA_EPG_GIT_REF=\n" in text and "KIA_PUBLIC_URL=http://tv.lan:8780" in text
