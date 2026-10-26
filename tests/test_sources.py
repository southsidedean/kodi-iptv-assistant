import json
import os
import sys
import threading
from pathlib import Path

import pytest

from kia import api, auth, sources
from kia.config import load

CATALOG = """\
# --- Countries (iptv-org) ---------------------------------------------- streams ---
https://iptv-org.github.io/iptv/countries/us.m3u               # USA            1,483
# https://iptv-org.github.io/iptv/countries/ca.m3u             # Canada           264

# --- Radio (radio-browser.info) ---------------------------------------------------
# Internet radio stations from a free community directory.
# radio-browser: country=US tag=jazz top=25
# https://example.com/jazz.m3u   jazz   radio   # 403 stations

# --- Your own ---------------------------------------------------------------------
# playlists/my-extras.m3u8   extras
"""


def instance(tmp_path: Path, own: str | None = None) -> Path:
    (tmp_path / "sources.txt").write_text(CATALOG)
    if own is not None:
        (tmp_path / "sources.local.txt").write_text(own)
        (tmp_path / "kia.toml").write_text('sources_file = "sources.local.txt"\n')
    else:
        (tmp_path / "kia.toml").write_text("")
    return tmp_path / "kia.toml"


def test_offered_lists_playlists_on_and_off():
    found = sources.offered(CATALOG)
    assert [(o.name, o.on, o.section) for o in found] == [
        ("us", True, "Countries (iptv-org)"),
        ("ca", False, "Countries (iptv-org)"),
        ("radio-us-jazz", False, "Radio (radio-browser.info)"),
        ("jazz", False, "Radio (radio-browser.info)"),
    ]  # comments and turned-off file paths aren't offered
    assert found[1].label == "Canada  264"
    assert found[2].radio and found[3].radio


def test_choices_apply_on_top_of_the_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    cfg = instance(tmp_path)
    assert [s.name for s in load(cfg).sources] == ["us"]
    state = sources.load_state(tmp_path / "state")
    sources.set_on(state, "https://iptv-org.github.io/iptv/countries/ca.m3u", True, in_file=False, known=True)
    sources.set_on(state, "https://iptv-org.github.io/iptv/countries/us.m3u", False, in_file=True, known=True)
    sources.add(state, "https://example.org/extra.m3u", "extra", radio=False)
    sources.set_on(state, "radio-browser: country=US tag=jazz top=25", True, in_file=False, known=True)
    sources.save_state(tmp_path / "state", state)
    got = load(cfg).sources
    assert [s.name for s in got] == ["ca", "radio-us-jazz", "extra"]
    assert got[1].is_radio_browser and got[1].params["tag"] == "jazz"


def test_your_own_copy_still_offers_sources_txt(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    cfg = instance(tmp_path, own="https://example.org/mine.m3u  mine\n")
    state = {"on": ["https://iptv-org.github.io/iptv/countries/ca.m3u"], "off": [], "added": []}
    sources.save_state(tmp_path / "state", state)
    assert [s.name for s in load(cfg).sources] == ["mine", "ca"]
    rows = sources.listing((tmp_path / "sources.local.txt").read_text(), CATALOG, state, {})
    us = next(r for r in rows if r["name"] == "us")
    assert not us["on"]  # on in sources.txt, but your copy leaves it out


def test_names_and_addresses_are_checked():
    with pytest.raises(ValueError, match="http"):
        sources.check_new("file:///etc/passwd", "", set())
    with pytest.raises(ValueError, match="http"):
        sources.check_new("/data/kia.toml", "", set())
    with pytest.raises(ValueError, match="lowercase"):
        sources.check_new("https://example.org/a.m3u", "Bad Name", set())
    with pytest.raises(ValueError, match="already"):
        sources.check_new("https://example.org/us.m3u", "", {"us"})
    assert sources.check_new(" https://example.org/a.m3u ", "", set()) == ("https://example.org/a.m3u", "a")


def test_lan_and_radio_arent_names(monkeypatch):
    # On a sources line they're flags: named lan, a playlist from the internet could test your network.
    for word in ("lan", "radio"):
        with pytest.raises(ValueError, match="meaning of its own"):
            sources.check_new("https://example.org/a.m3u", word, set())
    # One saved before this check is read with a name of its own, not as the flag.

    state = {"on": [], "off": [], "added": [{"url": "https://example.org/a.m3u", "name": "lan", "radio": False}]}
    (added,) = sources.apply([], [], state)
    assert not added.allow_lan and not added.is_yours and added.name == "a"


def test_check_now_keeps_the_lan_word():
    rows = sources.listing("https://example.org/z.m3u mine lan\n", "", {"on": [], "off": [], "added": []}, {})
    assert rows[0]["lan"] is True


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch) -> api.ApiConfig:
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    instance(tmp_path)
    out, state = tmp_path / "output", tmp_path / "state"
    out.mkdir()
    (out / "report.json").write_text(json.dumps({"streams": [
        {"source": "us", "name": "A", "status": "ok"}, {"source": "us", "name": "B", "status": "dead"}]}))  # fmt: skip
    return api.ApiConfig(out, state, tmp_path / "profiles.toml", None, auth.Auth(state, "secret"), 8743,
                         sources_file=tmp_path / "sources.txt", catalog_file=tmp_path / "sources.txt",
                         config_file=tmp_path / "kia.toml", test_report=tmp_path / "work" / "check-test" / "report.json")  # fmt: skip


def call(cfg, method, path, body=None, password="secret", secure=True):
    headers = {"Host": "tv.lan:8743", "X-Kia-Password": password} if password else {"Host": "tv.lan:8743"}
    return api.handle(cfg, method, path, headers, json.dumps(body).encode() if body is not None else b"", secure)


def test_api_lists_and_changes_playlists(cfg):
    status, data = call(cfg, "GET", "/api/sources", password=None, secure=False)
    assert status == 200 and data["file"] == "sources.txt" and not data["secure"]
    us = data["playlists"][0]
    assert us["on"] and us["streams"] == {"ok": 1, "flaky": 0, "dead": 1}
    ca = "https://iptv-org.github.io/iptv/countries/ca.m3u"
    assert call(cfg, "PUT", "/api/sources", {"url": ca, "on": True}, secure=False)[0] == 403
    assert call(cfg, "PUT", "/api/sources", {"url": ca, "on": True}, password="nope")[0] == 401
    rows = call(cfg, "PUT", "/api/sources", {"url": ca, "on": True})[1]["playlists"]
    assert next(r for r in rows if r["name"] == "ca")["on"]
    status, data = call(cfg, "POST", "/api/sources", {"url": "https://example.org/x.m3u", "name": "x", "radio": True})
    assert status == 200 and data["playlists"][-1] == {**data["playlists"][-1], "name": "x", "origin": "added", "radio": True}
    assert call(cfg, "POST", "/api/sources", {"url": "https://example.org/x.m3u"})[0] == 409  # already there
    assert call(cfg, "DELETE", "/api/sources?url=" + ca)[0] == 404  # only added ones can go
    assert call(cfg, "DELETE", "/api/sources?url=https%3A%2F%2Fexample.org%2Fx.m3u")[0] == 200
    assert call(cfg, "PUT", "/api/sources", {"url": "https://nowhere.example/z.m3u", "on": True})[0] == 404


def test_the_last_playlist_stays_on(cfg):
    us = "https://iptv-org.github.io/iptv/countries/us.m3u"
    status, data = call(cfg, "PUT", "/api/sources", {"url": us, "on": False})
    assert status == 409 and "last playlist" in data["error"]


def test_check_now_runs_one_playlist(cfg, monkeypatch):
    ran = []

    class Done:
        def __init__(self, cmd, **kw):
            ran.append(cmd)
            cfg.test_report.parent.mkdir(parents=True)
            cfg.test_report.write_text(json.dumps({"streams": [{"name": "Rock", "status": "ok"}, {"name": "Gone", "status": "dead"}]}))

        def communicate(self, timeout):
            return "", ""

    monkeypatch.setattr(api.subprocess, "Popen", Done)
    monkeypatch.setattr(api.threading, "Thread", lambda target, args, **kw: type("T", (), {"start": lambda self: target(*args)})())
    status, _data = call(cfg, "POST", "/api/sources/check", {"url": "https://example.com/jazz.m3u"})
    assert status == 200
    assert ran[0][-2:] == ["-u", "https://example.com/jazz.m3u jazz radio"]
    check = call(cfg, "GET", "/api/sources/check", password=None)[1]["check"]
    assert not check["running"] and check["result"] == {"total": 2, "ok": 1, "flaky": 0, "dead": 1, "sample": ["Rock"]}
    assert call(cfg, "POST", "/api/sources/check", {"url": "https://example.com/jazz.m3u"}, secure=False)[0] == 403
    # A line with the word lan is checked with it, as the nightly check reads it.
    (cfg.sources_file).write_text(cfg.sources_file.read_text() + "https://example.com/home.m3u home lan\n")
    ran.clear()
    cfg.test_report.unlink()
    assert call(cfg, "POST", "/api/sources/check", {"url": "https://example.com/home.m3u"})[0] == 200
    assert ran[0][-1] == "https://example.com/home.m3u home lan"


def test_a_check_can_be_stopped(cfg, monkeypatch):
    real = api.subprocess.Popen
    # A check that would run for a minute, and a child of it, like the ffprobe runs a check starts.
    slow = [sys.executable, "-c", "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(60)']); time.sleep(60)"]  # fmt: skip
    monkeypatch.setattr(api.subprocess, "Popen", lambda cmd, **kw: real(slow, **kw))
    assert call(cfg, "DELETE", "/api/sources/check")[0] == 409  # nothing to stop
    assert call(cfg, "POST", "/api/sources/check", {"url": "https://example.com/jazz.m3u"})[0] == 200
    pause = threading.Event().wait  # the fixture turns time.sleep off
    for _ in range(100):
        if "proc" in api.CHECK_PROC:
            break
        pause(0.05)
    proc = api.CHECK_PROC["proc"]
    assert call(cfg, "DELETE", "/api/sources/check", secure=False)[0] == 403
    assert call(cfg, "DELETE", "/api/sources/check")[0] == 200
    for _ in range(100):
        if not api.CHECK["running"]:
            break
        pause(0.05)
    check = call(cfg, "GET", "/api/sources/check", password=None)[1]["check"]
    assert not check["running"] and check["error"] == "Stopped. Nothing changed." and "result" not in check
    assert proc.returncode is not None
    for _ in range(100):  # the whole group goes, the child too
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            break
        pause(0.05)
    else:
        pytest.fail("the check's child is still running")


def test_no_check_during_a_refresh(cfg):
    (cfg.output_dir / "status.json").write_text(json.dumps({"result": "running"}))
    status, data = call(cfg, "POST", "/api/sources/check", {"url": "https://example.com/jazz.m3u"})
    assert status == 409 and "refreshing" in data["error"]


def test_the_last_playlist_cant_be_removed_either(cfg):
    us = "https://iptv-org.github.io/iptv/countries/us.m3u"
    call(cfg, "POST", "/api/sources", {"url": "https://example.org/x.m3u", "name": "x"})
    call(cfg, "PUT", "/api/sources", {"url": us, "on": False})
    status, data = call(cfg, "DELETE", "/api/sources?url=https%3A%2F%2Fexample.org%2Fx.m3u")
    assert status == 409 and "last playlist" in data["error"]


def test_logins_in_addresses_show_only_with_the_password(cfg, monkeypatch):
    secret = "https://user:hunter2@example.com/get.php?username=u&password=p"
    assert call(cfg, "POST", "/api/sources", {"url": secret, "name": "paid", "radio": False})[0] == 200

    def addresses(**kw):
        return [r["url"] for r in call(cfg, "GET", "/api/sources", **kw)[1]["playlists"] if r["name"] == "paid"]

    assert addresses(password=None, secure=False) == ["https://•••@example.com/get.php?•••"]
    assert addresses(password="secret", secure=False) == ["https://•••@example.com/get.php?•••"]  # http: never
    assert addresses(password=None) == ["https://•••@example.com/get.php?•••"]  # https, no password
    assert addresses(password="nope") == ["https://•••@example.com/get.php?•••"]
    assert addresses() == [secret]
    assert call(cfg, "GET", "/api/sources")[1]["unlocked"] and not call(cfg, "GET", "/api/sources", password=None)[1]["unlocked"]

    # Check now's progress is public: it never shows the login, even while it runs.
    monkeypatch.setitem(api.CHECK, "url", secret)
    for kw in ({"password": None, "secure": False}, {"password": "secret"}):
        assert "hunter2" not in json.dumps(call(cfg, "GET", "/api/sources/check", **kw)[1])
    assert "hunter2" not in json.dumps(call(cfg, "GET", "/api/sources", password=None, secure=False)[1])


def test_a_bad_port_doesnt_break_the_list():
    assert api.mask_url("http://user:pw@host:99999/x.m3u") == "•••"


def test_stop_right_after_start_still_stops(cfg, monkeypatch):
    real, started, go = api.subprocess.Popen, threading.Event(), threading.Event()

    def slow_start(cmd, **kw):  # the process takes a moment to start, and Stop comes first
        started.set()
        go.wait(5)
        return real([sys.executable, "-c", "import time; time.sleep(60)"], **kw)

    monkeypatch.setattr(api.subprocess, "Popen", slow_start)
    assert call(cfg, "POST", "/api/sources/check", {"url": "https://example.com/jazz.m3u"})[0] == 200
    started.wait(5)
    assert call(cfg, "DELETE", "/api/sources/check")[0] == 200  # not "No check is running"
    go.set()
    pause = threading.Event().wait
    for _ in range(200):
        if not api.CHECK["running"]:
            break
        pause(0.05)
    assert not api.CHECK["running"] and api.CHECK["error"] == "Stopped. Nothing changed."


def test_the_page_hears_why_the_password_didnt_unlock_it(cfg, monkeypatch):
    monkeypatch.setattr(api, "_WRONG", {})

    def get(password):
        headers = {"Host": "tv.lan:8743", "X-Kia-Password": password}
        return api.handle(cfg, "GET", "/api/sources", headers, b"", True, "192.0.2.7")[1]

    assert get("nope")["locked"] == "Wrong password." and not get("nope")["unlocked"]
    waiting = get("secret")  # right, but after two wrong ones in a row
    assert not waiting["unlocked"] and waiting["locked"].startswith("Too many wrong passwords")
    monkeypatch.setattr(api, "_WRONG", {})
    right = get("secret")
    assert right["unlocked"] and "locked" not in right
    assert "locked" not in call(cfg, "GET", "/api/sources", password=None)[1]  # nothing given, nothing to say
