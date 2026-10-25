import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from kia import plex


class FakePlex:
    """Enough of Plex Media Server's API to set up DVRs, recording every change."""

    def __init__(self, dvrs=None):
        self.dvrs = dvrs or []
        self.devices = {}  # address -> device
        self.calls = []
        self.titles = {}
        self.maps = {}
        self.starting = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def answer(self, method):
                url = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                if self.headers.get("X-Plex-Token") != "secret":
                    self.send_response(401)
                    self.end_headers()
                    return
                if fake.starting > 0:  # a new Plex, for its first minutes
                    fake.starting -= 1
                    said = b"the server is still starting up. Please retry later"
                    self.send_response(400)
                    self.send_header("Content-Length", str(len(said)))
                    self.end_headers()
                    self.wfile.write(said)
                    return
                fake.calls.append((method, url.path))
                body = fake.route(method, url.path, q)
                data = json.dumps({"MediaContainer": body}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.answer("GET")

            def do_POST(self):
                self.answer("POST")

            def do_PUT(self):
                self.answer("PUT")

            def do_DELETE(self):
                self.answer("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def route(self, method, path, q):
        if path == "/livetv/dvrs" and method == "GET":
            return {"Dvr": self.dvrs}
        if path == "/media/grabbers/tv.plex.grabbers.hdhomerun/devices":
            dev = self.devices.setdefault(q["uri"], {"key": str(100 + len(self.devices)), "uuid": f"device://x/{q['uri']}",
                                                     "uri": f"http://{q['uri']}", "model": "Threadfin"})  # fmt: skip
            return {"Device": [dev]}
        if path == "/livetv/dvrs" and method == "POST":
            dev = next(d for d in self.devices.values() if d["uuid"] == q["device"])
            dvr = {"key": str(10 + len(self.dvrs)), "Device": [dict(dev)], "Lineup": [{"id": q["lineup"]}]}
            self.dvrs.append(dvr)
            return {"Dvr": [dvr]}
        parts = path.strip("/").split("/")
        if method == "DELETE" and parts[:2] == ["livetv", "dvrs"]:
            self.dvrs = [d for d in self.dvrs if d["key"] != parts[2]]
            return {}
        if parts[:2] == ["livetv", "dvrs"] and parts[3:4] == ["lineups"]:
            self._dvr(parts[2])["Lineup"].append({"id": q["lineup"]})
        elif parts[:2] == ["livetv", "dvrs"] and parts[3:4] == ["devices"]:
            dev = next(d for d in self.devices.values() if d["key"] == parts[4])
            self._dvr(parts[2])["Device"].append(dict(dev))
        elif parts[:3] == ["media", "grabbers", "devices"] and parts[4:5] == ["channelmap"]:
            self.maps[parts[3]] = q
        elif parts[:3] == ["media", "grabbers", "devices"] and len(parts) == 4:
            self.titles[parts[3]] = q["title"]
        return {}

    def _dvr(self, key):
        return next(d for d in self.dvrs if d["key"] == key)

    def changes(self):
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def antenna():
    return {"key": "5", "Device": [{"key": "1", "uri": "http://192.0.2.50:5004", "model": "WinTV"}],
            "Lineup": [{"id": "lineup://tv.plex.providers.epg.cloud/abc#Local"}]}  # fmt: skip


def tuners():
    return [plex.Tuner("192.168.1.20:34400", "Threadfin 1: Plex 1", ["1", "20", "21"]),
            plex.Tuner("192.168.1.20:34401", "Threadfin 2: Plex 2", ["2", "28"])]  # fmt: skip


def test_a_new_plex_still_starting_up_is_waited_for():
    fake = FakePlex()
    fake.starting = 3
    plex.set_up(plex.Plex(fake.url, "secret", retry_every=0.01), tuners(), lambda m: None)
    assert len(fake.dvrs) == 1 and fake.starting == 0
    fake.starting = 1000  # one that never finishes: kia says so
    with pytest.raises(plex.PlexError, match="still starting up"):
        plex.Plex(fake.url, "secret", startup_wait=0.1, retry_every=0.01).dvrs()


def test_first_run_makes_one_dvr_for_both_threadfins():
    fake = FakePlex()
    dvr = plex.set_up(plex.Plex(fake.url, "secret"), tuners(), lambda m: None)
    assert len(fake.dvrs) == 1 and dvr["key"] == fake.dvrs[0]["key"]
    assert [d["uri"] for d in fake.dvrs[0]["Device"]] == ["http://192.168.1.20:34400", "http://192.168.1.20:34401"]
    # Each Threadfin has its own guide, named after its playlist.
    assert [lu["id"] for lu in fake.dvrs[0]["Lineup"]] == [t.lineup for t in tuners()]
    assert "http%3A%2F%2F192.168.1.20%3A34400%2Fxmltv%2Fthreadfin.xml#Threadfin%201%3A%20Plex%201" in tuners()[0].lineup
    # Every channel on, matched to the same number in Threadfin's guide.
    first = fake.maps["100"]
    assert first["channelsEnabled"] == "1,20,21" and first["channelMappingByKey[20]"] == "20" and first["channelMapping[21]"] == "21"
    assert fake.titles == {"100": "Threadfin 1: Plex 1", "101": "Threadfin 2: Plex 2"}
    assert ("POST", f"/livetv/dvrs/{dvr['key']}/reloadGuide") in fake.calls


def test_an_antenna_dvr_is_left_alone(antenna):
    before = json.dumps(antenna)
    fake = FakePlex([antenna])
    dvr = plex.set_up(plex.Plex(fake.url, "secret"), tuners(), lambda m: None)
    assert len(fake.dvrs) == 2 and dvr["key"] != "5"
    assert json.dumps(fake.dvrs[0]) == before
    assert not [c for c in fake.changes() if "/livetv/dvrs/5" in c[1]]


def test_running_again_reuses_the_dvr_and_updates_the_channels(antenna):
    fake = FakePlex([antenna])
    p = plex.Plex(fake.url, "secret")
    plex.set_up(p, tuners(), lambda m: None)
    grown = tuners()
    grown[1].channels.append("433")
    plex.set_up(p, grown, lambda m: None)
    assert len(fake.dvrs) == 2
    threadfin = fake.dvrs[1]
    assert len(threadfin["Device"]) == 2 and len(threadfin["Lineup"]) == 2  # nothing added twice
    assert fake.maps["101"]["channelsEnabled"] == "2,28,433"


def test_two_threadfins_in_the_stack_each_get_their_guide():
    # In kia's stack both listen on 34400 and differ by name: the second one's guide is added too.
    fake = FakePlex()
    stacked = [plex.Tuner("threadfin:34400", "Threadfin 1: Plex 1", ["1"]),
               plex.Tuner("threadfin-2:34400", "Threadfin 2: Plex 2", ["2"])]  # fmt: skip
    p = plex.Plex(fake.url, "secret")
    plex.set_up(p, stacked, lambda m: None)
    assert [lu["id"] for lu in fake.dvrs[0]["Lineup"]] == [t.lineup for t in stacked]
    plex.set_up(p, stacked, lambda m: None)
    assert len(fake.dvrs[0]["Lineup"]) == 2  # and not again on the next run


def test_a_threadfin_added_under_another_address_is_recognised():
    # Set up by hand with the IP address; kia's own address is a name. Same DVR, no new guide.
    fake = FakePlex()
    p = plex.Plex(fake.url, "secret")
    plex.set_up(p, tuners(), lambda m: None)
    for t in tuners():
        fake.devices[t.address.replace("192.168.1.20", "my-server.lan")] = fake.devices[t.address]
    renamed = [plex.Tuner(t.address.replace("192.168.1.20", "my-server.lan"), t.title, t.channels) for t in tuners()]
    plex.set_up(p, renamed, lambda m: None)
    assert len(fake.dvrs) == 1 and len(fake.dvrs[0]["Lineup"]) == 2 and len(fake.dvrs[0]["Device"]) == 2


def test_remove_deletes_only_a_threadfin_dvr(antenna):
    fake = FakePlex([antenna])
    p = plex.Plex(fake.url, "secret")
    plex.set_up(p, tuners(), lambda m: None)
    assert plex.remove(p, tuners(), lambda m: None) is True
    assert [d["key"] for d in fake.dvrs] == ["5"]
    assert plex.remove(p, tuners(), lambda m: None) is False  # nothing left to remove
    # A DVR that mixes in other tuners is never deleted.
    plex.set_up(p, tuners(), lambda m: None)
    fake.dvrs[1]["Device"].append(antenna["Device"][0])
    with pytest.raises(plex.PlexError, match="other tuners"):
        plex.remove(p, tuners(), lambda m: None)
    assert len(fake.dvrs) == 2


def test_a_wrong_token_or_no_threadfin_says_so():
    fake = FakePlex()
    with pytest.raises(plex.PlexError, match="check the token"):
        plex.set_up(plex.Plex(fake.url, "wrong"), tuners(), lambda m: None)
    with pytest.raises(plex.PlexError, match="No Threadfin"):
        plex.set_up(plex.Plex(fake.url, "secret"), [], lambda m: None)
    with pytest.raises(plex.PlexError, match="Can't reach Plex"):
        plex.set_up(plex.Plex("http://127.0.0.1:9", "secret", timeout=2), tuners(), lambda m: None)


def test_find_tuners_reads_threadfin_and_kia(tmp_path: Path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    (out / "profiles.json").write_text(json.dumps({"profiles": [{"file": "plex-1.m3u", "title": "Plex 1: Pluto and series"}]}))
    for folder, m3u in (("threadfin", "http://web/plex-1.m3u"), ("threadfin-2", "http://web/plex-2.m3u")):
        (tmp_path / folder / "conf").mkdir(parents=True)
        (tmp_path / folder / "conf" / "settings.json").write_text(json.dumps({"files": {"m3u": {"X": {"file.source": m3u}}}}))
    monkeypatch.setenv("KIA_THREADFIN_2_PORT", "35001")
    lineups = {"http://my-server.lan:34400/lineup.json": [{"GuideNumber": "1"}, {"GuideNumber": "20"}],
               "http://my-server.lan:35001/lineup.json": [{"GuideNumber": "5"}]}  # fmt: skip
    found = plex.find_tuners(tmp_path, out, "my-server.lan", lambda m: None, getter=lineups.__getitem__)
    assert [(t.address, t.title, t.channels) for t in found] == [
        ("my-server.lan:34400", "Plex 1: Pluto and series", ["1", "20"]),
        ("my-server.lan:35001", "plex-2", ["5"]),  # no profile title: the file's name
    ]
    # A Threadfin that isn't running is left out, and said so.
    said = []
    plex.find_tuners(tmp_path, out, "my-server.lan", said.append, getter=lambda url: (_ for _ in ()).throw(OSError("refused")))
    assert len(said) == 2 and "isn't answering" in said[0]


def test_plex_in_the_stack_reaches_threadfin_by_name_and_has_its_own_token(tmp_path: Path):
    assert plex.in_stack("http://plex:32400") and not plex.in_stack("http://192.168.1.20:32400")
    assert plex.own_token(tmp_path) is None  # not claimed yet
    prefs = tmp_path / plex.PREFERENCES
    prefs.parent.mkdir(parents=True)
    prefs.write_text('<?xml version="1.0" encoding="utf-8"?>\n<Preferences FriendlyName="box" PlexOnlineToken="abc123"/>\n')
    assert plex.own_token(tmp_path) == "abc123"
    out = tmp_path / "output"
    out.mkdir()
    (tmp_path / "threadfin" / "conf").mkdir(parents=True)
    (tmp_path / "threadfin" / "conf" / "settings.json").write_text("{}")
    lineups = {"http://threadfin:34400/lineup.json": [{"GuideNumber": "7"}]}
    found = plex.find_tuners(tmp_path, out, None, lambda m: None, getter=lineups.__getitem__)
    assert [(t.address, t.channels) for t in found] == [("threadfin:34400", ["7"])]


def test_the_daemon_keeps_plex_in_step_only_when_asked(monkeypatch):
    from kia import cli

    calls = []
    monkeypatch.setattr(cli, "main", lambda argv: calls.append(argv) or 0)
    monkeypatch.delenv("KIA_PLEX_URL", raising=False)
    cli.keep_plex_in_step("kia.toml")
    assert calls == []
    monkeypatch.setenv("KIA_PLEX_URL", "http://plex:32400")
    cli.keep_plex_in_step("kia.toml")
    assert calls == [["plex-tuners", "-c", "kia.toml", "--after-refresh"]]

    def broken(argv):
        raise SystemExit(1)

    monkeypatch.setattr(cli, "main", broken)
    cli.keep_plex_in_step("kia.toml")  # a failure never stops the daemon


class StubPlex:
    """Just enough Plex for warm_up: a library that finds the clip, and a transcoder that,
    once asked for a piece, 'downloads' the AAC codec into the codecs folder."""

    def __init__(self, base: Path):
        self.base, self.calls, self.sections = base, [], []

    def call(self, method, path, params=None):
        self.calls.append((method, path))
        if method == "POST" and path == "/library/sections":
            self.sections.append({"key": "9", "title": params["name"], "location": params["location"]})
        if path == "/library/sections" and method == "GET":
            return {"MediaContainer": {"Directory": self.sections}}
        if path == "/library/sections/9/all":
            return {"MediaContainer": {"Metadata": [{"ratingKey": "42"}]}}
        if method == "DELETE":
            self.sections = []
        return {}

    def text(self, path, params=None):
        self.calls.append(("GET", path))
        if path.endswith("start.m3u8"):
            assert params["path"] == "/library/metadata/42" and params["directStream"] == "0"
            return "#EXTM3U\nsession/x/base/index.m3u8\n"
        if path.endswith("index.m3u8"):
            folder = self.base / plex.CODECS / "abc-linux-x86_64"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "libaac_decoder.so").write_bytes(b"")
        return ""


class BusyPlex(StubPlex):
    """A Plex still scanning another library: the warm-up library's scan waits its turn."""

    def __init__(self, base: Path, busy_for: int):
        super().__init__(base)
        self.busy_for = busy_for

    def call(self, method, path, params=None):
        if path == "/library/sections" and method == "GET":
            self.busy_for -= 1
            music = {"key": "5", "title": "Music", "refreshing": self.busy_for > 0}
            super().call(method, path, params)
            return {"MediaContainer": {"Directory": [music, *self.sections]}}
        if path == "/library/sections/9/all" and self.busy_for > 0:
            return {"MediaContainer": {}}
        return super().call(method, path, params)


class PlexWithMovies(StubPlex):
    """A Plex that already has a movie library, busy scanning music."""

    def call(self, method, path, params=None):
        self.calls.append((method, path))
        if path == "/library/sections" and method == "GET":
            return {"MediaContainer": {"Directory": [
                {"key": "3", "title": "Music", "type": "artist", "refreshing": True},
                {"key": "4", "title": "Movies", "type": "movie"}]}}  # fmt: skip
        if path == "/library/sections/4/all":
            assert params["type"] == "1" and params["audioCodec"] == "aac" and params["X-Plex-Container-Size"] == "1"
            return {"MediaContainer": {"Metadata": [{"ratingKey": "42"}]}}
        return {}


def test_the_aac_codec_counts_under_either_name(tmp_path: Path):
    assert not plex.has_codecs(tmp_path)
    folder = tmp_path / plex.CODECS / "abc-linux-aarch64"
    folder.mkdir(parents=True)
    (folder / "libaac_lc_decoder.so").write_bytes(b"")  # Plex for Arm
    assert plex.has_codecs(tmp_path)


def test_the_warm_up_plays_a_video_plex_already_has(tmp_path: Path):
    stub, said = PlexWithMovies(tmp_path), []
    assert plex.warm_up(stub, tmp_path, said.append, ffmpeg="false", wait=5) is True  # no clip to make
    assert not any(m == "POST" for m, _ in stub.calls)  # no library added, so no scan to wait for
    assert ("GET", "/video/:/transcode/universal/stop") in stub.calls
    assert said[-1] == "Plex has its codecs now."


def test_the_warm_up_waits_for_another_library_scan(tmp_path: Path):
    said = []
    stub = BusyPlex(tmp_path, busy_for=5)
    assert plex.warm_up(stub, tmp_path, said.append, ffmpeg="true", wait=2, step=0.01) is True
    assert "Plex is scanning Music first" in said[1] and said[-1] == "Plex has its codecs now."
    # One that never finishes: kia gives up after busy_wait, says why, and tidies up.
    stub = BusyPlex(tmp_path, busy_for=10_000)
    with pytest.raises(plex.PlexError, match="still scanning Music"):
        plex.warm_up(stub, tmp_path, said.append, ffmpeg="true", wait=0.05, busy_wait=0.3, step=0.01)
    assert stub.sections == [] and not list((tmp_path / plex.WARMUP).glob("*.mp4"))


def test_a_new_plex_is_warmed_up_once_and_left_tidy(tmp_path: Path):
    stub = StubPlex(tmp_path)
    assert not plex.has_codecs(tmp_path)
    said = []
    assert plex.warm_up(stub, tmp_path, said.append, ffmpeg="true", wait=5) is True
    assert plex.has_codecs(tmp_path)
    assert ("DELETE", "/library/sections/9") in stub.calls and stub.sections == []  # the library is gone again
    assert ("GET", "/video/:/transcode/universal/stop") in stub.calls
    assert not list((tmp_path / plex.WARMUP).glob("*.mp4"))  # and so is the clip
    assert "codecs" in said[0] and said[-1] == "Plex has its codecs now."


class PlexThatKeepsItsLibrary(StubPlex):
    """Removing the warm-up library fails, after the warm-up itself failed."""

    def call(self, method, path, params=None):
        if path == "/library/sections/9/all":
            return {"MediaContainer": {}}  # never finds the clip
        if method == "DELETE":
            raise plex.PlexError("Plex answered 500")
        return super().call(method, path, params)


def test_a_failed_clean_up_doesnt_hide_why_the_warm_up_failed(tmp_path: Path):
    said = []
    with pytest.raises(plex.PlexError, match="didn't find the warm-up clip"):
        plex.warm_up(PlexThatKeepsItsLibrary(tmp_path), tmp_path, said.append, ffmpeg="true", wait=0.05, step=0.01)
    assert not list((tmp_path / plex.WARMUP).glob("*.mp4"))  # the clip is gone anyway
    assert any("Couldn't remove the kia codec warm-up library" in m for m in said)


def test_after_a_refresh_the_warm_up_is_tried_once_a_day(tmp_path: Path, monkeypatch):
    from kia import cli

    (tmp_path / "sources.txt").write_text("https://example.org/a.m3u\n")
    (tmp_path / "kia.toml").write_text("")
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    monkeypatch.setenv("KIA_PLEX_URL", "http://plex:32400")
    monkeypatch.setattr(plex, "own_token", lambda base: "token")
    monkeypatch.setattr(plex, "find_tuners", lambda *a, **kw: [])
    monkeypatch.setattr(plex, "has_codecs", lambda base: False)
    monkeypatch.setattr(plex, "set_up", lambda *a: None)
    warmed = []
    monkeypatch.setattr(plex, "warm_up", lambda *a: warmed.append(1))
    run = ["plex-tuners", "-c", str(tmp_path / "kia.toml")]
    assert cli.main([*run, "--after-refresh"]) == 0
    assert cli.main([*run, "--after-refresh"]) == 0  # the next refresh, hours later
    assert len(warmed) == 1
    assert cli.main(run) == 0  # by hand, it's tried every time
    assert len(warmed) == 2
    tried = tmp_path / "state" / "plex-warm-up-tried"
    os.utime(tried, (time.time() - 90000, time.time() - 90000))  # a day on
    assert cli.main([*run, "--after-refresh"]) == 0
    assert len(warmed) == 3


def test_remove_never_adds_a_tuner_to_find_one(antenna):
    fake = FakePlex([antenna])
    p = plex.Plex(fake.url, "secret")
    plex.set_up(p, tuners(), lambda m: None)
    fake.calls.clear()
    assert plex.remove(p, tuners(), lambda m: None) is True
    assert not any(c[0] == "POST" for c in fake.calls)  # only a look at the DVRs, then the delete


def test_the_warm_up_clip_is_made_with_a_bare_environment(tmp_path: Path, monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        raise plex.PlexError("stop here")

    monkeypatch.setenv("KIA_PLEX_TOKEN", "secret-token")
    monkeypatch.setattr(plex.subprocess, "run", run)
    with pytest.raises(plex.PlexError):
        plex._warm_up_with_clip(None, tmp_path, lambda m: None, "ffmpeg", 1, 1, 0.01)
    assert "KIA_PLEX_TOKEN" not in seen["env"]
