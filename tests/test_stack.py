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


# --- The Stack page ----------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent


def test_the_parts_are_the_ones_stack_sh_knows():
    import re

    script = (ROOT / "scripts" / "stack.sh").read_text()
    block = script.split('PARTS="', 1)[1].split('"\n', 1)[0]
    assert [line.split("|")[0] for line in block.splitlines()] == [name for name, _ in stack.PARTS]
    assert all(re.fullmatch(r"[a-z0-9-]+", name) for name, _ in stack.PARTS)


def test_a_choice_is_known_parts_in_kias_order():
    assert stack.parts_from(["observability", "plex"]) == ["plex", "observability"]
    assert stack.parts_from(["plex", "plex"]) == ["plex"]
    assert stack.parts_from([]) == []
    for bad in (["plex;reboot"], ["PLEX"], "plex", [1], None):
        try:
            stack.parts_from(bad)
        except stack.StackError:
            continue
        raise AssertionError(bad)


def test_the_presets_come_from_stacks():
    found = {p["name"]: p for p in stack.presets(ROOT)}
    assert found["plex-box"]["parts"] == ["plex"] and found["kodi"]["parts"] == []
    assert found["everything"]["what"].startswith("Plex and Jellyfin")


def project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A kia folder with the presets: (the folder, its state/, its stack-service/)."""
    folder = tmp_path / "kia"
    (folder / "state").mkdir(parents=True)
    (folder / "stack-service").mkdir()
    (folder / "stacks").symlink_to(ROOT / "stacks")
    return folder, folder / "state", folder / "stack-service"


def test_asking_writes_one_request_at_a_time(tmp_path: Path):
    _, state, service = project(tmp_path)
    rid = stack.ask(state, service, "check", ["plex", "observability"])
    assert (state / "stack-request").read_text() == f"id={rid}\naction=check\nstack=plex,observability\n"
    assert stack.waiting(state, service) == rid
    try:
        stack.ask(state, service, "apply", ["plex"])
        raise AssertionError("a second request while the first waits")
    except stack.StackError as exc:
        assert "still working" in str(exc)
    (state / "stack-request").rename(service / "request")  # the service took it
    assert stack.waiting(state, service) == rid
    (service / "request").unlink()  # and finished
    assert stack.waiting(state, service) is None and stack.ask(state, service, "apply", ["plex"])


def test_an_old_request_doesnt_block_new_ones(tmp_path: Path):
    import os
    import time

    _, state, service = project(tmp_path)
    stack.ask(state, service, "check", [])
    old = time.time() - stack.REQUEST_EXPIRES - 5
    os.utime(state / "stack-request", (old, old))
    assert stack.waiting(state, service) is None


def test_reading_the_services_answer_and_containers(tmp_path: Path):
    _, _, service = project(tmp_path)
    assert stack.result(service) is None and stack.containers(service) == []
    (service / "result").write_text("id=abc\naction=check\nstate=finished\nexit=0\nfinished=2026-10-24T22:00:00Z\n"
                                    "evil=1\n\n0 problem(s), 0 warning(s).\n")  # fmt: skip
    assert stack.result(service) == {"id": "abc", "action": "check", "state": "finished", "exit": "0",
                                     "finished": "2026-10-24T22:00:00Z", "output": "0 problem(s), 0 warning(s).\n"}  # fmt: skip
    (service / "containers.tsv").write_text("kia\trunning\thealthy\tUp 2 hours\nweb\trunning\n\n" + "x" * 500 + "\n")
    rows = stack.containers(service)
    assert rows[0] == {"service": "kia", "state": "running", "health": "healthy", "status": "Up 2 hours"}
    assert rows[1]["health"] == "" and len(rows[2]["service"]) == 80


def test_the_service_counts_as_running_while_its_list_is_fresh_or_it_works(tmp_path: Path, monkeypatch):
    import os
    import time

    folder, state, service = project(tmp_path)
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "plex")
    assert stack.page_state(state, folder)["service"] is False
    (service / "containers.tsv").write_text("kia\trunning\thealthy\tUp\n")
    page = stack.page_state(state, folder)
    assert page["service"] is True and page["docker"] is True and page["profiles"] == ["plex"]
    old = time.time() - stack.SERVICE_SILENT_AFTER - 5
    os.utime(service / "containers.tsv", (old, old))
    assert stack.page_state(state, folder)["service"] is False
    # An apply downloading images for minutes: the list is old, but the service is working.
    (service / "result").write_text("id=abc\naction=apply\nstate=running\nexit=\nfinished=x\n\n")
    assert stack.page_state(state, folder)["service"] is True


def api_cfg(tmp_path: Path):
    from kia import api, auth

    folder, state, _ = project(tmp_path)
    return api.ApiConfig(folder / "output", state, folder / "profiles.toml", None,
                         auth.Auth(state, "secret"), 8743, config_file=folder / "kia.toml")  # fmt: skip


def test_the_api_asks_the_service(tmp_path: Path, monkeypatch):
    from kia import api

    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "plex,plex-server")
    monkeypatch.setenv("KIA_STACK", "plex")
    cfg = api_cfg(tmp_path)
    status, data = api.handle(cfg, "GET", "/api/stack", {}, b"", False)
    assert status == 200 and data["chosen"] == ["plex"] and not data["secure"] and data["waiting"] is None
    assert data["parts"][0]["name"] == "plex" and any(p["name"] == "plex-box" for p in data["presets"])
    ask = lambda body: api.handle(cfg, "POST", "/api/stack/check", {"X-Kia-Password": "secret"}, json.dumps(body).encode(), True)
    status, data = ask({"parts": ["plex", "observability"]})
    assert status == 200 and data["waiting"] == data["id"]
    assert "stack=plex,observability" in (cfg.state_dir / "stack-request").read_text()
    assert ask({"parts": ["plex"]})[0] == 409  # one at a time
    (cfg.state_dir / "stack-request").unlink()
    status, data = ask({"parts": ["plex", "$(reboot)"]})
    assert status == 400 and not (cfg.state_dir / "stack-request").exists()


def test_the_api_without_docker_has_nothing_to_ask(tmp_path: Path, monkeypatch):
    from kia import api

    cfg = api_cfg(tmp_path)
    assert api.handle(cfg, "GET", "/api/stack", {}, b"", True)[1]["docker"] is False
    status, data = api.handle(cfg, "POST", "/api/stack/apply", {"X-Kia-Password": "secret"}, b'{"parts": []}', True)
    assert status == 409 and "Docker" in data["error"] and not (cfg.state_dir / "stack-request").exists()


def test_the_stack_page_is_in_the_menu(tmp_path: Path):
    s = status.collect(tmp_path, tmp_path, {}, None)
    html = status.render_stack(s)
    assert 'id="stack-page"' in html and 'fetch("api/stack"' in html
    # The password box and buttons use the shared form styles; the Sources page needs them too.
    assert ".editor label.field" in html and ".editor label.field" in status.render_sources(s)
    assert 'href="stack.html"' in status.render_html(s)


def test_the_api_refuses_a_body_that_isnt_an_object(tmp_path: Path, monkeypatch):
    from kia import api

    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "plex")
    cfg = api_cfg(tmp_path)
    for body in (b"[]", b"null", b'"plex"'):
        status, data = api.handle(cfg, "POST", "/api/stack/check", {"X-Kia-Password": "secret"}, body, True)
        assert status == 400 and "parts" in data["error"], body


def test_a_claim_code_is_checked_and_goes_with_apply_only(tmp_path: Path):
    assert stack.claim_from(None) == "" and stack.claim_from("") == ""
    assert stack.claim_from(" claim-AbC_12-x ") == "claim-AbC_12-x"
    for bad in ("claim-", "claim-a\nb", "claim-$(id)", "token-abc", "claim-a b", 7, ["claim-a"], "claim-" + "a" * 80):
        try:
            stack.claim_from(bad)
        except stack.StackError:
            continue
        raise AssertionError(bad)
    _, state, service = project(tmp_path)
    try:
        stack.ask(state, service, "check", ["plex"], "claim-abc")
        raise AssertionError("a claim code with a check")
    except stack.StackError:
        pass
    stack.ask(state, service, "apply", ["plex"], "claim-abc")
    assert (state / "stack-request").read_text().endswith("stack=plex\nclaim=claim-abc\n")


def test_presets_have_a_title_and_what_they_need():
    assert stack.presets(ROOT)[0]["name"] == "kodi"  # simplest first, by "# order:"
    assert len({p["order"] for p in stack.presets(ROOT)}) == len(stack.presets(ROOT))
    for p in stack.presets(ROOT):
        assert p["title"] and p["title"] != p["name"] and p["needs"] and p["what"], p["name"]
        stack.parts_from(p["parts"])  # every part is one kia knows


def test_adding_live_tv_to_plex_from_the_page(tmp_path: Path, monkeypatch):
    import subprocess

    from kia import api

    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    monkeypatch.setattr(api, "PLEX_TUNERS", {"running": False})
    cfg = api_cfg(tmp_path)
    post = lambda: api.handle(cfg, "POST", "/api/plex/tuners", {"X-Kia-Password": "secret"}, b"", True)
    status, data = post()
    assert status == 409 and "KIA_PLEX_URL" in data["error"]
    monkeypatch.setenv("KIA_PLEX_URL", "http://plex:32400")
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd[-3:])
        return subprocess.CompletedProcess(cmd, 0, stdout="Made a new DVR in Plex\n", stderr="")

    monkeypatch.setattr(api.subprocess, "run", fake_run)
    monkeypatch.setattr(api.threading, "Thread", lambda target, args, **kw: type("T", (), {"start": lambda self: target(*args)})())
    status, data = post()
    assert status == 200 and ran == [["plex-tuners", "-c", str(cfg.config_file)]]
    state = api.handle(cfg, "GET", "/api/plex/tuners", {}, b"", False)[1]["plex"]
    assert state["ok"] is True and state["running"] is False and "Made a new DVR" in state["output"]
    monkeypatch.setitem(api.PLEX_TUNERS, "running", True)
    assert post()[0] == 409  # one at a time
