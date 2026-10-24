"""The Dashboard's Stack tiles (kia.stack): which parts run next to kia, whether each answers,
where a browser opens it, and the same in /metrics."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from kia import metrics, stack, status


def test_kia_outside_docker_has_no_stack(monkeypatch):
    assert stack.profiles() is None
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "")
    assert stack.profiles() == []
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", " plex, monitoring ,")
    assert stack.profiles() == ["plex", "monitoring"]


def test_each_profile_brings_its_programs():
    names = [s.name for s in stack.chosen(["plex", "plex-server", "monitoring"])]
    assert names == ["threadfin", "plex", "grafana", "prometheus", "cadvisor", "node-exporter"]
    assert [s.name for s in stack.chosen(["jellyfin-server", "monitoring-gpu"])] == ["jellyfin", "dcgm-exporter"]
    assert stack.chosen([]) == []


def test_every_program_is_a_compose_service_of_its_profile():
    import re

    compose = (Path(__file__).resolve().parent.parent / "compose.yaml").read_text().split("\nservices:\n", 1)[1]
    blocks = dict(re.findall(r"^  ([a-z0-9-]+):\n(.*?)(?=^  [a-z0-9-]+:\n|^\S|\Z)", compose, re.S | re.M))
    for s in stack.SERVICES:
        if s.name == "node-exporter" and s.profile == "monitoring":
            assert '"monitoring-node"' in blocks[s.name]  # or Homebrew's on a Mac; Prometheus reaches either
            continue
        assert f'"{s.profile}"' in re.search(r"profiles: \[([^\]]*)\]", blocks[s.name]).group(1), s.name


def test_what_answers_and_where_it_opens(monkeypatch):
    monkeypatch.setenv("KIA_PLEX_PORT", "32500")
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "plex-server,monitoring")
    monkeypatch.setenv("KIA_PUBLIC_URL", "http://192.168.1.20:8780")
    up = {"plex", "grafana", "cadvisor"}
    out = {x["service"]: x for x in stack.check(["plex-server", "monitoring"], knock=lambda host, port: host in up,
                                                node=lambda: True)}  # fmt: skip
    assert out["plex"] == {"service": "plex", "title": "Plex", "ok": True, "link": {"port": 32500, "path": "/web"}}
    assert out["prometheus"]["ok"] is False and "link" not in out["prometheus"]  # its port is for this computer only
    assert out["node-exporter"]["ok"] is True  # asked of Prometheus, not knocked on
    assert out["grafana"]["link"] == {"url": "https://192.168.1.20:8788"}  # the Observability button's address


def test_knocking_on_a_port(tmp_path: Path):
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    try:
        assert stack.answers("127.0.0.1", port)
    finally:
        listener.close()
    assert not stack.answers("127.0.0.1", port, timeout=0.5)


def test_node_exporter_through_prometheus():
    answer = {"up": "1"}

    class Prometheus(BaseHTTPRequestHandler):
        def do_GET(self):
            assert "up%7Bjob%3D%22node%22%7D" in self.path
            body = json.dumps({"data": {"result": [{"value": [0, answer["up"]]}]}}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Prometheus)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    assert stack.node_exporter_up(base)
    answer["up"] = "0"
    assert not stack.node_exporter_up(base)
    server.shutdown()
    assert not stack.node_exporter_up(base, timeout=0.5)  # Prometheus itself is down


def test_the_watch_writes_stack_json_and_metrics(tmp_path: Path):
    found = [{"service": "threadfin", "title": "Threadfin", "ok": True}, {"service": "plex", "title": "Plex", "ok": False}]
    watch = stack.Watch(tmp_path, ["plex", "plex-server"], check=lambda running: found)
    watch.once()
    written = json.loads((tmp_path / "stack.json").read_text())
    assert written["services"] == found and written["checked"]
    assert not list(tmp_path.glob("*.tmp"))
    text = metrics.render(tmp_path, stack=watch)
    assert 'kia_stack_service_up{service="threadfin"} 1' in text and 'kia_stack_service_up{service="plex"} 0' in text
    assert "kia_stack_service_up" not in metrics.render(tmp_path)  # kia outside Docker


def test_the_dashboard_has_a_stack_card_it_fills_in(tmp_path: Path):
    html = status.render_html(status.collect(tmp_path, tmp_path, {}, None))
    assert '<section class="card" id="stack" hidden>' in html  # hidden until stack.json lists something
    assert 'fetch("stack.json"' in html and "Not answering" in html
