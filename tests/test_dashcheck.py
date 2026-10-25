"""kia check-dashboards: which dashboard panels have data here, by section, and why not."""

import json
from pathlib import Path

from kia import dashcheck


def board(tmp_path: Path) -> Path:
    folder = tmp_path / "dashboards"
    folder.mkdir()
    panels = [
        {"type": "row", "title": "Graphics card"},
        {"type": "stat", "title": "Temperature", "targets": [{"expr": "max(DCGM_FI_DEV_GPU_TEMP)"}],
         "fieldConfig": {"defaults": {"noValue": "Needs monitoring-gpu"}}},
        {"type": "row", "title": "The computer"},
        {"type": "timeseries", "title": "Temperatures",
         "targets": [{"expr": "node_hwmon_temp_celsius"}, {"expr": "node_thermal_zone_temp"}]},
        {"type": "timeseries", "title": "Per container",
         "targets": [{"expr": 'rate(x{container_label_com_docker_compose_project="$project"}[$__rate_interval])'}]},
    ]  # fmt: skip
    (folder / "kia-hardware.json").write_text(json.dumps({"title": "Hardware", "panels": panels}))
    return folder


def test_a_panel_has_data_when_any_of_its_queries_does(tmp_path: Path):
    asked = []

    def ask(prometheus, expr, timeout):
        asked.append(dashcheck._query(expr))
        return 1 if expr == "node_thermal_zone_temp" or "rate(x" in expr else 0

    panels = dashcheck.check("http://prometheus:9090", board(tmp_path), ask=ask)
    assert [(p.section, p.title, p.has_data) for p in panels] == [
        ("Graphics card", "Temperature", False), ("The computer", "Temperatures", True), ("The computer", "Per container", True)]  # fmt: skip
    assert 'rate(x{container_label_com_docker_compose_project=~".+"}[2m])' in asked  # Grafana's variables filled in
    assert dashcheck.report(panels) == [
        "Hardware / Graphics card: 0 of 1 panels have data (Needs monitoring-gpu)",
        "Hardware / The computer: 2 of 2 panels have data",
    ]


def test_a_short_tile_note_gives_way_to_its_panel_note():
    panels = [dashcheck.Panel("Hardware", "Graphics card", "Power", False, "Needs monitoring-gpu"),
              dashcheck.Panel("Hardware", "Graphics card", "Load", False, "Needs the monitoring-gpu profile (an NVIDIA card, on Linux)"),
              dashcheck.Panel("Hardware", "Graphics card", "Plex", True, "")]  # fmt: skip
    assert dashcheck.report(panels) == [
        "Hardware / Graphics card: 1 of 3 panels have data (Needs the monitoring-gpu profile (an NVIDIA card, on Linux))"]


def test_against_a_prometheus_that_turns_one_query_down(tmp_path: Path):
    import threading
    import urllib.parse
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Prometheus(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)["query"][0]
            if "DCGM" in q:  # a query Prometheus can't parse: 400, as it answers bad syntax
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"status":"error","errorType":"bad_data"}')
                return
            body = {"status": "success", "data": {"result": [{"value": [0, "1"]}]}} if "thermal" in q else {"status": "success"}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Prometheus)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    panels = dashcheck.check(f"http://127.0.0.1:{server.server_port}", board(tmp_path), timeout=5)
    # The refused query is one empty panel; an answer without data.result is another; the
    # rest are still asked.
    assert [(p.title, p.has_data) for p in panels] == [("Temperature", False), ("Temperatures", True), ("Per container", False)]


def test_a_dashboard_variable_with_capitals_is_filled_in():
    assert dashcheck._query('x{job="$Job", host=~"$host_2"}') == 'x{job=~".+", host=~".+"}'
