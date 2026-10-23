"""Jellyfin's sessions in kia's /metrics, the way Plex's are."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from kia import jellyfin, metrics

SESSIONS = [
    {"DeviceName": "Living room TV", "DeviceId": "abc", "Client": "Jellyfin Android TV", "RemoteEndPoint": "192.168.1.20",
     "NowPlayingItem": {"Type": "TvChannel", "Name": "Pluto TV Movies"},
     "PlayState": {"PlayMethod": "Transcode", "IsPaused": False},
     "TranscodingInfo": {"IsVideoDirect": True, "HardwareAccelerationType": "none"}},
    {"DeviceName": "Phone", "DeviceId": "def", "Client": "Jellyfin Mobile (iOS)", "RemoteEndPoint": "8.8.8.8:51234",
     "NowPlayingItem": {"Type": "Movie"}, "PlayState": {"PlayMethod": "Transcode", "IsPaused": True},
     "TranscodingInfo": {"IsVideoDirect": False, "HardwareAccelerationType": "vaapi"}},
    {"DeviceName": "Laptop", "DeviceId": "ghi", "Client": "Jellyfin Web", "RemoteEndPoint": "::ffff:192.168.1.20",
     "NowPlayingItem": {"Type": "Episode"}, "PlayState": {"PlayMethod": "DirectPlay"}},
    {"DeviceName": "Idle", "DeviceId": "jkl", "Client": "Jellyfin Web", "RemoteEndPoint": "192.168.1.20"},  # signed in only
]  # fmt: skip


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if 'Token="secret"' not in self.headers.get("Authorization", ""):
                self.send_response(401)
                self.end_headers()
                return
            data = json.dumps(SESSIONS).encode() if self.path.startswith("/Sessions") else b"[]"
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_sessions_in_the_shape_plex_uses(server):
    got = jellyfin.Jellyfin(server, "secret").sessions()
    assert [(x["kind"], x["video"], x["hw"], x["location"], x["state"]) for x in got] == [
        ("live", "copy", "none", "lan", "playing"),  # live TV, video copied, sound converted
        ("movie", "transcode", "gpu", "wan", "paused"),
        ("episode", "directplay", "none", "lan", "playing"),
    ]  # the session that isn't playing anything is left out
    assert got[0]["player_id"] == "jellyfin:abc" and got[0]["product"] == "Jellyfin Android TV"
    with pytest.raises(jellyfin.JellyfinError, match="KIA_JELLYFIN_TOKEN"):
        jellyfin.Jellyfin(server, "wrong").sessions()


def test_locations():
    # Outside addresses are public DNS servers; Python counts the documentation ranges as private.
    found = [jellyfin.location(a) for a in ("192.168.1.20", "8.8.8.8:443", "[2001:4860:4860::8888]:80", "::1", "fd00::5",
                                             "::ffff:192.168.1.20", "::ffff:8.8.4.4", "")]  # fmt: skip
    assert found == ["lan", "wan", "wan", "lan", "lan", "lan", "wan", "?"]


def test_jellyfin_in_metrics_next_to_plex(server, tmp_path: Path):
    state = tmp_path / "state"
    players = metrics.Players(state)

    class FakePlex:
        def sessions(self):
            return [{"player": "Den Roku", "player_id": "roku-1", "product": "Plex for Roku", "kind": "live",
                     "video": "copy", "hw": "none", "location": "lan", "state": "playing"}]  # fmt: skip

    text = metrics.render(tmp_path, plex=metrics.PlexSessions(FakePlex(), state_dir=state, players=players),
                          jellyfin=metrics.JellyfinSessions(jellyfin.Jellyfin(server, "secret"), state_dir=state, players=players))  # fmt: skip
    assert "kia_jellyfin_up 1" in text and "kia_plex_up 1" in text
    assert 'kia_jellyfin_sessions{kind="live",video="copy",hw="none",location="lan"} 1' in text
    assert 'kia_jellyfin_sessions{kind="movie",video="transcode",hw="gpu",location="wan"} 1' in text
    assert "Living room TV" not in text and "Phone" not in text  # pseudonyms only
    known = json.loads((state / metrics.PLAYERS_FILE).read_text())
    assert {v["name"] for v in known.values()} == {"Den Roku", "Living room TV", "Phone", "Laptop"}  # one list for both
    down = metrics.render(tmp_path, jellyfin=metrics.JellyfinSessions(jellyfin.Jellyfin("http://127.0.0.1:9", "x"), state_dir=state))
    assert "kia_jellyfin_up 0" in down
