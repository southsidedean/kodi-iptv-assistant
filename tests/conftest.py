"""Settings for the whole test suite."""

import os
import socketserver
from pathlib import Path

import pytest
from hypothesis import settings

from kia import api, pluto, serve
from kia import name as kia_name

# On CI, the fuzz tests try the same examples every run, so a rare one can't fail a pull
# request that didn't cause it. Locally they keep finding new ones.
settings.register_profile("ci", derandomize=True)
if os.environ.get("CI"):
    settings.load_profile("ci")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """kia's settings from the shell (a .env exported, or a run inside the container) would
    change what the tests read, so each test starts without them, in the repository's folder
    wherever pytest was started."""
    # A proxy would take the tests' requests to 127.0.0.1 servers somewhere else.
    proxies = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
    for name in list(os.environ):
        if name.startswith(("KIA_", "COMPOSE_")) or name.lower() in proxies:
            monkeypatch.delenv(name)
    # git as the tests run it, without your own settings (signed commits, hooks, a template).
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.chdir(Path(__file__).resolve().parent.parent)
    # The computer's own name would go into alert titles and setups (kia.name): none, unless
    # a test gives one.
    monkeypatch.setattr(kia_name, "computer_name", lambda: "")
    # Run under Docker Desktop, every caller would share one address (api.shared_address).
    monkeypatch.setattr(api, "_docker_desktop", lambda: False)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """Test servers stop within 50 ms instead of half a second, and are stopped and closed
    after each test that left one running; the Pluto relay retries without waiting a second;
    and each test starts with no wrong passwords counted, no stream check under way, and the
    web server's addresses looked up again."""
    made, serving = [], []
    original_init, original_serve = socketserver.BaseServer.__init__, socketserver.BaseServer.serve_forever

    def init(self, *args, **kwargs):
        made.append(self)
        original_init(self, *args, **kwargs)

    def serve_forever(self, poll_interval=0.05):
        serving.append(self)
        original_serve(self, poll_interval=poll_interval)

    monkeypatch.setattr(socketserver.BaseServer, "__init__", init)
    monkeypatch.setattr(socketserver.BaseServer, "serve_forever", serve_forever)
    monkeypatch.setattr(pluto, "RETRY_DELAY", 0)
    monkeypatch.setattr(api, "_WRONG", {})
    monkeypatch.setattr(api, "CHECK", {"running": False})
    monkeypatch.setattr(api, "CHECK_REPORT", {})
    monkeypatch.setattr(api, "CHECK_PROC", {})
    monkeypatch.setattr(serve, "_proxy", {"at": -1e9, "addresses": frozenset()})
    yield
    for server in made:
        if server in serving and not server._BaseServer__is_shut_down.is_set():
            server.shutdown()
        server.server_close()
