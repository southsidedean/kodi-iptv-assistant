"""Settings for the whole test suite."""

import ipaddress
import os
import socket
import socketserver
from pathlib import Path

import pytest
from hypothesis import settings

from kia import api, epgshare, guard, pluto, serve
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
    # kia map reads epgshare01's channel lists from the internet: never in a test, unless it
    # gives its own (kia.epgshare.load_lists then keeps last time's, or none).
    def offline(url):
        raise OSError("no network in tests")

    monkeypatch.setattr(epgshare, "_get", offline)


# Names the tests' stand-in DNS answers (the network fixture); a test adds its own with
# hosts[name] = [address, ...].
HOSTS = {"localhost": ["127.0.0.1", "::1"]}


@pytest.fixture
def hosts():
    """The stand-in DNS's table, for a test to add names to (cleared afterwards)."""
    return dict(HOSTS)


@pytest.fixture(autouse=True)
def network(monkeypatch, request):
    """No test reaches the internet or the real DNS: a name lookup answers from a table
    (hosts), and a connection anywhere but this computer fails. A test that read a real site
    would pass or fail with what that site says that day, and a DNS that answers every name
    with a local address (some routers do) would fail the tests that count on a name not
    being local."""
    real_lookup, real_connect, real_connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex
    table = request.getfixturevalue("hosts") if "hosts" in request.fixturenames else HOSTS

    def lookup(host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        try:
            if name is None or ipaddress.ip_address(name.partition("%")[0]):
                return real_lookup(host, port, *args, **kwargs)
        except ValueError:
            pass
        if name.lower().rstrip(".") not in table:
            raise socket.gaierror(socket.EAI_NONAME, f"{name}: no DNS in tests")
        found = []
        for address in table[name.lower().rstrip(".")]:
            found += real_lookup(address, port, *args, **kwargs)
        return found

    def here(sock, address) -> bool:
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return True  # a Unix socket
        try:
            return ipaddress.ip_address(str(address[0]).partition("%")[0]).is_loopback
        except ValueError:
            return False

    def connect(sock, address):
        if not here(sock, address):
            raise ConnectionRefusedError(f"{address[0]}: no network in tests")
        return real_connect(sock, address)

    def connect_ex(sock, address):
        return real_connect_ex(sock, address) if here(sock, address) else 111

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """Test servers stop within 50 ms instead of half a second, and are stopped and closed
    after each test that left one running; the Pluto relay retries without waiting a second;
    and each test starts with no wrong passwords counted, no stream check under way, the
    web server's addresses looked up again, and no guard (the one a test started is closed
    with its server, so the next test starts its own)."""
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
    monkeypatch.setattr(guard, "_RUNNING", [])
    yield
    for server in made:
        if server in serving and not server._BaseServer__is_shut_down.is_set():
            server.shutdown()
        server.server_close()
