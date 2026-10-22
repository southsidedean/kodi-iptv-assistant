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
