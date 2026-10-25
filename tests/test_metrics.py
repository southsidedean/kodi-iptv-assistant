import json
import re
import urllib.request
from pathlib import Path

from kia import metrics, serve


class FakeRelay:
    def counters(self):
        return {"sessions": 3, "rewinds": 1, "errors": 2, "fallbacks": 0, "pieces": 40, "bytes": 123456,
                "playing": 1, "viewers": {"65c69bbfd77d450008c7ffee": 2}}  # fmt: skip


STATUS = {
    "result": "ok",
    "last_run": {"started": "2026-10-22T04:00:00-05:00", "finished": "2026-10-22T04:32:00-05:00", "minutes": 32, "exit_code": 0},
    "next_run": "2026-10-23T04:00:00-05:00",
    "guide_updated": "2026-10-22T08:33:00-05:00",
    "channels": {"tv": 1533, "radio": 101, "with_guide": 966},
    "coverage": {"full": 585, "partial": 380, "none": 568},
    "sources": [{"name": "us", "ok": 1178, "flaky": 24, "dead": 281}],
    "profiles": [{"name": "kids", "tv": 72, "radio": 0}],
    "alerts": [],
    "pluto_channels": {"65c69bbfd77d450008c7ffee": 'Battlestar "Galactica"'},
}


def test_the_numbers_in_prometheus_format(tmp_path: Path):
    (tmp_path / "status.json").write_text(json.dumps(STATUS))
    text = metrics.render(tmp_path, FakeRelay())
    assert 'kia_channels{kind="tv"} 1533' in text
    assert 'kia_source_streams{source="us",status="dead"} 281' in text
    assert 'kia_playlist_channels{playlist="kids",kind="tv"} 72' in text
    assert "kia_last_refresh_duration_seconds 1920" in text and "kia_last_refresh_success 1" in text
    assert "kia_refresh_running 0" in text and "kia_pluto_bytes_total 123456" in text
    assert 'kia_pluto_viewers{channel="65c69bbfd77d450008c7ffee",name="Battlestar \\"Galactica\\""} 2' in text
    # Every sample has a TYPE line before it, and every line is a comment or a sample.
    for line in text.splitlines():
        assert line.startswith("# ") or re.fullmatch(r'[a-z_]+(\{[^}]*\})? -?[0-9.e+]+', line), line
    typed = set(re.findall(r"^# TYPE (\S+)", text, re.M))
    assert {re.match(r"[a-z_]+", line).group() for line in text.splitlines() if not line.startswith("#")} <= typed


def test_no_status_yet(tmp_path: Path):
    text = metrics.render(tmp_path)  # a new install, before its first refresh
    assert "kia_info" in text and "kia_channels" not in text and "kia_pluto" not in text


def test_the_server_answers_metrics(tmp_path: Path):
    (tmp_path / "status.json").write_text(json.dumps(STATUS))
    srv = serve.serve_in_background(tmp_path, "127.0.0.1", 0, metrics=lambda: metrics.render(tmp_path))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/metrics", timeout=5) as resp:
            assert resp.headers["Content-Type"].startswith("text/plain; version=0.0.4")
            assert "kia_channels_with_guide 966" in resp.read().decode()
    finally:
        srv.shutdown()


class FakePlex:
    def __init__(self):
        self.playing = [
            {"player": "Kitchen", "player_id": "abc123", "product": "Plex for Roku", "kind": "live", "video": "copy", "location": "lan", "state": "playing"},
            {"player": "Grandma's iPad", "player_id": "def456", "product": "Plex for iOS", "kind": "movie", "video": "transcode", "hw": "gpu", "location": "wan", "state": "paused"},
        ]  # fmt: skip

    def sessions(self):
        return [dict(x) for x in self.playing]


def test_plex_players_appear_by_pseudonym_only(tmp_path: Path):
    state = tmp_path / "state"
    out = metrics.render(tmp_path, plex=metrics.MediaSessions(FakePlex(), state_dir=state))
    assert "Kitchen" not in out and "Grandma" not in out
    names = re.findall(r'kia_plex_session_info\{player="(player-[0-9a-f]{6})"', out)
    assert len(set(names)) == 2
    assert 'kia_plex_sessions{kind="live",video="copy",hw="none",location="lan"} 1' in out
    assert 'kia_plex_sessions{kind="movie",video="transcode",hw="gpu",location="wan"} 1' in out
    # The same device keeps its pseudonym, after a restart too (the key is kept in state/).
    again = metrics.render(tmp_path, plex=metrics.MediaSessions(FakePlex(), state_dir=state))
    assert re.findall(r'kia_plex_session_info\{player="(player-[0-9a-f]{6})"', again) == names
    assert (state / "metrics-key").stat().st_mode & 0o077 == 0
    # Which is which, for the secure page only.
    known = {p["pseudonym"]: p["name"] for p in metrics.known_players(state)}
    assert sorted(known.values()) == ["Grandma's iPad", "Kitchen"] and set(known) == set(names)
    assert (state / metrics.PLAYERS_FILE).stat().st_mode & 0o077 == 0


def test_players_names_need_the_password(tmp_path: Path):
    from kia import api, auth

    state = tmp_path / "state"
    metrics.render(tmp_path, plex=metrics.MediaSessions(FakePlex(), state_dir=state))
    cfg = api.ApiConfig(tmp_path, state, tmp_path / "profiles.toml", None, auth.Auth(state, "secret"), 8743)

    def call(password, secure):
        headers = {"Host": "tv.lan:8743", **({"X-Kia-Password": password} if password else {})}
        return api.handle(cfg, "GET", "/api/players", headers, b"", secure)

    assert call(None, True)[0] == 401 and call("secret", False)[0] == 403
    status, body = call("secret", True)
    assert status == 200 and {p["name"] for p in body["players"]} == {"Kitchen", "Grandma's iPad"}


def test_plex_says_where_it_converts():
    from kia import plex

    def session(ts):
        return {"Player": {"title": "TV", "product": "Plex for Roku", "machineIdentifier": "x"}, "type": "movie", "TranscodeSession": ts}

    p = plex.Plex("http://plex:32400", "t")
    p.call = lambda method, path: {"MediaContainer": {"Metadata": [
        session({"videoDecision": "transcode", "transcodeHwEncoding": "nvenc"}),
        session({"videoDecision": "transcode"}),
        session({"videoDecision": "copy"}),
        {"Player": {"title": "Phone"}, "type": "episode"}]}}  # fmt: skip
    assert [s["hw"] for s in p.sessions()] == ["gpu", "cpu", "none", "none"]


def test_plex_is_up_even_when_kia_cant_keep_its_players_list(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "metrics-key").mkdir()  # can't be read as the key: kia's own files fail, not Plex
    out = metrics.render(tmp_path, plex=metrics.MediaSessions(FakePlex(), state_dir=state))
    assert "kia_plex_up 1" in out and 'player="player-unknown"' in out
    assert "Kitchen" not in out


def test_two_threads_saving_the_players_list_at_once(tmp_path: Path):
    # Plex's and Jellyfin's numbers are gathered in their own threads and share one list.
    import threading

    failed = []

    def save(n):
        for i in range(50):
            try:
                metrics._write_private(tmp_path / "players.json", f'{{"n": {n}, "i": {i}}}\n')
            except OSError as exc:
                failed.append(exc)

    threads = [threading.Thread(target=save, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not failed and json.loads((tmp_path / "players.json").read_text())["i"] == 49


def test_every_kia_number_a_dashboard_uses_is_one_kia_sends(tmp_path: Path):
    # The Plex and Jellyfin dashboards are made from one template (scripts/dashboards.py): a
    # misspelled name there would leave a panel saying kia isn't set up, forever.
    (tmp_path / "status.json").write_text(json.dumps(STATUS))
    text = metrics.render(tmp_path, FakeRelay(), plex=metrics.MediaSessions(FakePlex(), state_dir=tmp_path),
                          jellyfin=metrics.MediaSessions(FakePlex(), state_dir=tmp_path))  # fmt: skip
    sent = set(re.findall(r"^# TYPE (\S+)", text, re.M))
    folder = Path(__file__).resolve().parent.parent / "deploy" / "monitoring" / "dashboards"
    used = set()
    for path in folder.glob("*.json"):
        for panel in json.loads(path.read_text())["panels"]:
            for target in panel.get("targets", []):
                used |= set(re.findall(r"\bkia_[a-z_]+", target["expr"]))
                # A name pattern ({__name__=~"kia_(plex|jellyfin)_sessions"}) means each of its names.
                for a, names, b in re.findall(r'__name__=~"(kia_)\(([a-z_|]+)\)(_[a-z_]+)"', target["expr"]):
                    used |= {a + n + b for n in names.split("|")}
    used.discard("kia_")
    assert {"kia_jellyfin_up", "kia_jellyfin_sessions", "kia_jellyfin_session_info"} <= used
    assert used <= sent, sorted(used - sent)


def test_players_that_look_the_same_share_a_line(tmp_path: Path):
    plex = FakePlex()
    plex.playing = [{"player": "", "player_id": "", "product": "Plex Web", "kind": "live", "video": "copy",
                     "location": "lan", "state": "playing"}] * 2  # fmt: skip
    out = metrics.render(tmp_path, plex=metrics.MediaSessions(plex, state_dir=tmp_path / "state"))
    lines = [line for line in out.splitlines() if line.startswith("kia_plex_session_info{")]
    assert len(lines) == 1 and lines[0].endswith(" 2")


def test_plex_and_jellyfin_are_asked_at_the_same_time(tmp_path: Path):
    import time

    class Slow(FakePlex):
        def sessions(self):
            time.sleep(0.5)
            return super().sessions()

    state = tmp_path / "state"
    began = time.monotonic()
    out = metrics.render(tmp_path, plex=metrics.MediaSessions(Slow(), state_dir=state),
                         jellyfin=metrics.MediaSessions(Slow(), state_dir=state))  # fmt: skip
    assert time.monotonic() - began < 0.9 and "kia_plex_up 1" in out and "kia_jellyfin_up 1" in out


def test_a_plex_that_turned_kia_away_is_asked_with_its_new_token(tmp_path: Path, capsys):
    class Unclaimed(FakePlex):
        token = ""

        def sessions(self):
            if not self.token:
                raise OSError("401")
            return super().sessions()

    server = Unclaimed()
    renewed = []
    s = metrics.MediaSessions(server, every=0, state_dir=tmp_path / "state",
                             renew=lambda: (renewed.append(1), setattr(server, "token", "claimed")))  # fmt: skip
    assert s.get() is None and renewed == [1]
    assert "didn't answer" in capsys.readouterr().err  # said once, when it stops answering
    assert s.get() is not None


def test_in_stack_plex_reads_its_token_again(tmp_path: Path, monkeypatch):
    from kia import cli, plex

    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    (tmp_path / "kia.toml").write_text("")
    monkeypatch.setenv("KIA_PLEX_URL", "http://plex:32400")
    monkeypatch.delenv("KIA_PLEX_TOKEN", raising=False)
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    tokens = [None, "claimed-token"]
    monkeypatch.setattr(plex, "own_token", lambda base: tokens.pop(0) if tokens else "claimed-token")
    s = cli.plex_sessions(str(tmp_path / "kia.toml"))
    assert s.server.headers["X-Plex-Token"] == "" and s.renew is not None
    s.renew()
    assert s.server.headers["X-Plex-Token"] == "claimed-token"
    monkeypatch.setenv("KIA_PLEX_TOKEN", "from-env")
    assert cli.plex_sessions(str(tmp_path / "kia.toml")).renew is None  # your own token: nothing to read again
