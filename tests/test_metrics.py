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
            {"player": "Grandma's iPad", "player_id": "def456", "product": "Plex for iOS", "kind": "movie", "video": "transcode", "location": "wan", "state": "paused"},
        ]  # fmt: skip

    def sessions(self):
        return [dict(x) for x in self.playing]


def test_plex_players_appear_by_pseudonym_only(tmp_path: Path):
    state = tmp_path / "state"
    out = metrics.render(tmp_path, plex=metrics.PlexSessions(FakePlex(), state_dir=state))
    assert "Kitchen" not in out and "Grandma" not in out
    names = re.findall(r'kia_plex_session_info\{player="(player-[0-9a-f]{6})"', out)
    assert len(set(names)) == 2
    assert 'kia_plex_sessions{kind="live",video="copy",location="lan"} 1' in out
    # The same device keeps its pseudonym, after a restart too (the key is kept in state/).
    again = metrics.render(tmp_path, plex=metrics.PlexSessions(FakePlex(), state_dir=state))
    assert re.findall(r'kia_plex_session_info\{player="(player-[0-9a-f]{6})"', again) == names
    assert (state / "metrics-key").stat().st_mode & 0o077 == 0
    # Which is which, for the secure page only.
    known = {p["pseudonym"]: p["name"] for p in metrics.known_players(state)}
    assert sorted(known.values()) == ["Grandma's iPad", "Kitchen"] and set(known) == set(names)
    assert (state / metrics.PLAYERS_FILE).stat().st_mode & 0o077 == 0


def test_players_names_need_the_password(tmp_path: Path):
    from kia import api, auth

    state = tmp_path / "state"
    metrics.render(tmp_path, plex=metrics.PlexSessions(FakePlex(), state_dir=state))
    cfg = api.ApiConfig(tmp_path, state, tmp_path / "profiles.toml", None, auth.Auth(state, "secret"), 8743)

    def call(password, secure):
        headers = {"Host": "tv.lan:8743", **({"X-Kia-Password": password} if password else {})}
        return api.handle(cfg, "GET", "/api/players", headers, b"", secure)

    assert call(None, True)[0] == 401 and call("secret", False)[0] == 403
    status, body = call("secret", True)
    assert status == 200 and {p["name"] for p in body["players"]} == {"Kitchen", "Grandma's iPad"}
