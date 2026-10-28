import contextlib
import json
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

from kia import api, auth, profiles, serve, tls
from kia.m3u import Entry, write


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch) -> api.ApiConfig:
    monkeypatch.setattr(api, "_sleep", lambda s: None)
    out, state = tmp_path / "output", tmp_path / "state"
    out.mkdir()
    entries = [
        Entry("Toons", "http://a/1", {"tvg-id": "Toons.us", "group-title": "Kids;Animation", "tvg-chno": "1"}, source="us"),
        Entry("CNN", "http://a/2", {"tvg-id": "CNN.us", "group-title": "News", "tvg-chno": "2"}, source="us"),
        Entry("KINK", "http://a/3", {"tvg-id": "radio.kink", "group-title": "Alternative", "radio": "true", "tvg-chno": "3"}, source="kink"),
    ]
    (out / "curated.m3u").write_text(write(entries, None))
    profiles.save_sources(state, [(e, {e.source}) for e in entries])
    changes = []
    c = api.ApiConfig(out, state, tmp_path / "profiles" / "profiles.toml", None, auth.Auth(state, "secret"), 8743,
                      lambda: changes.append(1))  # fmt: skip
    c.changes = changes
    return c


def call(cfg, method, path, body=None, password=None, origin=None, host="tv.lan:8743", secure=True):
    headers = {"Host": host}
    if password:
        headers["X-Kia-Password"] = password
    if origin:
        headers["Origin"] = origin
    return api.handle(cfg, method, path, headers, json.dumps(body).encode() if body is not None else b"", secure)


def test_read_and_preview(cfg):
    status, data = call(cfg, "GET", "/api/profiles")
    assert status == 200 and data["editable"] and data["profiles"] == [] and data["total"] == 3
    assert {"name": "Kids", "count": 1} in data["groups"] and {"name": "kink", "count": 1} in data["sources"]
    assert call(cfg, "POST", "/api/preview", {"groups": ["kids"]})[1] == {"tv": 1, "radio": 0, "sample": ["Toons"]}
    assert call(cfg, "POST", "/api/preview", {"tv": False})[1]["radio"] == 1
    assert call(cfg, "POST", "/api/preview", {"names": ["("]})[0] == 401  # patterns need the password
    assert call(cfg, "POST", "/api/preview", {"names": ["("]}, password="secret")[0] == 400
    assert call(cfg, "POST", "/api/preview", {"names": ["cn"]}, password="secret")[1]["sample"] == ["CNN"]
    assert call(cfg, "GET", "/api/channels?q=cn")[1]["channels"] == [{"id": "CNN.us", "name": "CNN", "group": "News", "status": "ok"}]



def test_a_damaged_sources_file_still_lists_the_channels(cfg):
    for damaged in ("[1, 2]", '{"x": 5}', "not json"):
        (cfg.state_dir / "channel-sources.json").write_text(damaged)
        status, data = call(cfg, "GET", "/api/profiles")
        assert status == 200 and data["total"] == 3
    (cfg.state_dir / "channel-sources.json").write_text(json.dumps({api._items(cfg)[0][0].key: "us"}))  # text, not a list
    assert api._items(cfg)[0][1] == set()

def test_saving_needs_the_password_over_https(cfg):
    assert call(cfg, "PUT", "/api/profiles/kids", {"groups": ["Kids"]})[0] == 401
    assert call(cfg, "PUT", "/api/profiles/kids", {"groups": ["Kids"]}, password="nope")[0] == 401
    assert not cfg.profiles_file.exists()
    # Never over plain HTTP, even with the right password.
    status, data = call(cfg, "PUT", "/api/profiles/kids", {"groups": ["Kids"]}, password="secret", secure=False)
    assert status == 403 and "secure" in data["error"] and not cfg.profiles_file.exists()
    assert call(cfg, "POST", "/api/password", {"new": "something-new"}, password="secret", secure=False)[0] == 403
    data = call(cfg, "GET", "/api/profiles", secure=False)[1]
    assert data["editable"] and not data["secure"] and data["https_port"] == 8743 and data["password"] == "env"
    cfg.auth.enabled = False
    status, data = call(cfg, "PUT", "/api/profiles/kids", {"groups": ["Kids"]}, password="secret")
    assert status == 403 and "KIA_EDITING" in data["error"]
    assert call(cfg, "GET", "/api/profiles")[1]["editable"] is False


def test_other_websites_are_refused(cfg):
    assert call(cfg, "GET", "/api/profiles", origin="http://evil.example")[0] == 403
    assert call(cfg, "PUT", "/api/profiles/kids", {}, password="secret", origin="http://evil.example")[0] == 403
    assert call(cfg, "GET", "/api/profiles", origin="https://tv.lan:8743")[0] == 200


def test_save_writes_the_file_and_playlists(cfg):
    status, data = call(cfg, "PUT", "/api/profiles/kids", {"title": "The kids", "groups": ["Kids"], "radio": False}, password="secret")
    assert status == 200 and data["profiles"][0] == {"name": "kids", "title": "The kids", "file": "kids.m3u", "tv": 1, "radio": 0, "guide": None, "builtin": False}
    assert (cfg.output_dir / "kids.m3u").exists() and cfg.changes == [1]
    assert profiles.load(cfg.profiles_file)[0].groups == ["Kids"]
    call(cfg, "PUT", "/api/profiles/radio", {"tv": False}, password="secret")
    call(cfg, "PUT", "/api/profiles/kids", {"groups": ["Kids", "News"]}, password="secret")  # edit keeps order
    assert [p.name for p in profiles.load(cfg.profiles_file)] == ["kids", "radio"]
    assert (cfg.profiles_file.with_name("profiles.toml.bak")).exists()
    for name, body in [("Bad Name", {}), ("guide", {}), ("x", {"group": ["Kids"]}), ("x", {"tv": "no"})]:
        assert call(cfg, "PUT", f"/api/profiles/{name}", body, password="secret")[0] == 400
    assert call(cfg, "DELETE", "/api/profiles/kids", password="secret")[0] == 200
    assert not (cfg.output_dir / "kids.m3u").exists() and [p.name for p in profiles.load(cfg.profiles_file)] == ["radio"]
    assert call(cfg, "DELETE", "/api/profiles/kids", password="secret")[0] == 404


def test_tv_only_playlist_comes_with_radio(cfg):
    data = call(cfg, "PUT", "/api/profiles/kids", {"groups": ["Kids"]}, password="secret")[1]
    tv = next(p for p in data["profiles"] if p["name"] == "tv")
    assert tv == {**tv, "title": "TV only", "builtin": True, "tv": 2, "radio": 0}
    assert "KINK" not in (cfg.output_dir / "tv.m3u").read_text()
    assert [p.name for p in profiles.load(cfg.profiles_file)] == ["kids"]  # never written to your file
    # A profile of your own named "tv" replaces it.
    data = call(cfg, "PUT", "/api/profiles/tv", {"groups": ["News"]}, password="secret")[1]
    mine = next(p for p in data["profiles"] if p["name"] == "tv")
    assert not mine["builtin"] and mine["tv"] == 1


def test_a_broken_file_is_not_overwritten(cfg):
    cfg.profiles_file.parent.mkdir()
    cfg.profiles_file.write_text("[kids\n")
    data = call(cfg, "GET", "/api/profiles")[1]
    assert data["error"] and not data["editable"]
    assert call(cfg, "PUT", "/api/profiles/news", {}, password="secret")[0] == 409
    assert cfg.profiles_file.read_text() == "[kids\n"


def test_toml_round_trip():
    ps = [profiles.Profile("kids", title='Say "hi" \\ ünï', groups=["Kids"], radio=False), profiles.Profile("all")]
    assert profiles.load_text(profiles.to_toml(ps)) == ps


def test_generated_password_can_be_changed(cfg, tmp_path):
    a = auth.Auth(tmp_path / "state2")
    logged = []
    a.ensure(logged.append)
    shown = a.shown.read_text().strip()
    assert a.source == "generated" and a.check(shown) and not a.check("nope")
    assert shown not in logged[0] and str(a.shown) in logged[0]  # the log says where it is, never what
    assert len(shown) == 14 and all(c in auth.ALPHABET + "-" for c in shown)
    assert (a.saved.stat().st_mode & 0o777) == 0o600 and shown not in a.saved.read_text()
    a.ensure(logged.append)
    assert len(logged) == 1  # made once
    cfg.auth = a
    assert call(cfg, "POST", "/api/password", {"new": "short"}, password=shown)[0] == 400
    assert call(cfg, "POST", "/api/password", {"new": "new-password-123"}, password="wrong")[0] == 401
    assert call(cfg, "POST", "/api/password", {"new": "new-password-123"}, password=shown) == (200, {"password": "set"})
    assert a.check("new-password-123") and not a.check(shown) and not a.shown.exists()
    assert call(cfg, "PUT", "/api/profiles/kids", {}, password="new-password-123")[0] == 200
    a.reset(logged.append)
    assert a.source == "generated" and not a.check("new-password-123")
    # A password in .env wins and can't be changed here.
    env = auth.Auth(tmp_path / "state2", "from-env-file")
    assert env.check("from-env-file") and not env.check(a.shown.read_text().strip())
    cfg.auth = env
    status, data = call(cfg, "POST", "/api/password", {"new": "whatever-123"}, password="from-env-file")
    assert status == 400 and ".env" in data["error"]


@pytest.fixture
def certs(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("needs openssl")
    d = tmp_path / "tls"
    tls.make_self_signed(d, tls.names("http://tv.lan:8780", []))
    return d


def test_certificate(certs, tmp_path):
    info = tls.info(certs)
    assert info["generated"] and info["key_matches"] and info["names"] == ["tv.lan", "localhost", "127.0.0.1"]
    assert tls.names(None, ["kia.home.lan", "192.168.1.20"]) == ["kia.home.lan", "192.168.1.20", "localhost", "127.0.0.1"]
    assert tls.names("http://[fd00::5]:8780", []) [0] == "fd00::5"
    assert tls.names(None, ["FD00:0:0:0:0:0:0:5", "TV.lan"])[:2] == ["fd00::5", "tv.lan"]
    # The front door's names (extra) are added for the certificate but never change the first,
    # the one people open; a name already chosen isn't repeated.
    assert tls.names(None, ["kia.my-server.lan"], extra=["kia.my-server.lan", "auth.my-server.lan"]) == \
        ["kia.my-server.lan", "auth.my-server.lan", "localhost", "127.0.0.1"]
    assert tls.names("http://tv.lan:8780", [], extra=["kia.my-server.lan"])[0] == "tv.lan"
    assert tls.in_url("fd00::5") == "[fd00::5]" and tls.in_url("tv.lan") == "tv.lan"
    logged = []
    before = (certs / tls.CERT).read_text()
    assert tls.ensure(certs, tls.names("http://tv.lan:8780", []), logged.append)["generated"] and not logged
    tls.ensure(certs, tls.names(None, ["kia.home.lan"]), logged.append)  # a new name: a new certificate
    assert "kia.home.lan" in tls.info(certs)["names"] and len(logged) == 1
    from datetime import datetime, timedelta, timezone
    tls.ensure(certs, tls.names(None, ["kia.home.lan"]), logged.append, now=datetime.now(timezone.utc) + timedelta(days=800))
    assert "expire" in logged[-1]
    # Someone else's certificate is left alone, even an expired one.
    (certs / tls.MARK).write_text("someone else\n")
    current = (certs / tls.CERT).read_text()
    assert not tls.ensure(certs, ["other.name"], logged.append, now=datetime.now(timezone.utc) + timedelta(days=900))["generated"]
    assert (certs / tls.CERT).read_text() == current != before


def test_served_over_https(cfg, certs, monkeypatch):
    import ssl
    import urllib.error

    secure = serve.make_server(cfg.output_dir, "127.0.0.1", 0, cfg, tls_dir=certs)
    plain = serve.make_server(cfg.output_dir, "127.0.0.1", 0, cfg)
    for s in (secure, plain):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    context = ssl.create_default_context(cafile=str(certs / tls.CERT))
    context.check_hostname = False
    https, http = f"https://127.0.0.1:{secure.server_address[1]}", f"http://127.0.0.1:{plain.server_address[1]}"

    def put(base, **kw):
        req = urllib.request.Request(f"{base}/api/profiles/news", data=b'{"groups":["News"]}', method="PUT",
                                     headers={"X-Kia-Password": "secret", "Content-Type": "application/json"})  # fmt: skip
        return urllib.request.urlopen(req, timeout=10, **kw)

    try:
        with urllib.request.urlopen(f"{https}/api/profiles", context=context, timeout=10) as r:
            assert json.load(r)["secure"] is True
        with pytest.raises(urllib.error.HTTPError) as refused:
            put(http)
        refused.value.close()
        assert refused.value.code == 403
        with put(https, context=context) as r:
            assert json.load(r)["profiles"][0]["tv"] == 1
        with urllib.request.urlopen(f"{http}/news.m3u", timeout=10) as r:  # playlists stay on plain HTTP
            assert b"CNN" in r.read()
        # Behind nginx: only its X-Kia-Secure header makes a request secure, and only from nginx.
        proxied = serve.make_server(cfg.output_dir, "127.0.0.1", 0, cfg, trust_proxy=True)
        threading.Thread(target=proxied.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{proxied.server_address[1]}"
        claim = urllib.request.Request(f"{base}/api/profiles", headers={"X-Kia-Secure": "1"})
        for proxy, believed in (("127.0.0.1", True), ("192.0.2.9", False),  # nginx here, or somewhere else
                                ("192.0.2.9, 127.0.0.1", True), ("no-such-name.invalid,192.0.2.9", False)):  # any of its names; one that doesn't look up
            monkeypatch.setenv("KIA_PROXY_HOST", proxy)
            serve._proxy["at"] = -1e9  # look it up again
            with urllib.request.urlopen(claim, timeout=10) as r:
                assert json.load(r)["secure"] is believed, proxy
        with urllib.request.urlopen(f"{base}/api/profiles", timeout=10) as r:
            assert json.load(r)["secure"] is False
        proxied.shutdown()
        serve._proxy["at"] = -1e9
    finally:
        secure.shutdown()
        plain.shutdown()


def test_wrong_passwords_slow_down_only_who_sends_them(cfg):
    def try_(password, client):
        headers = {"Host": "tv.lan:8743", "X-Kia-Password": password}
        return api.handle(cfg, "PUT", "/api/profiles/x", headers, b'{"name": "x"}', True, client)[0]

    assert try_("nope", "192.0.2.9") == 401
    assert try_("nope", "192.0.2.9") == 401  # the second wrong one in a row: a wait from now on
    assert try_("nope", "192.0.2.9") == 429 and try_("secret", "192.0.2.9") == 429
    assert try_("secret", "192.0.2.10") == 200  # someone else's right password still works
    assert api.handle(cfg, "PUT", "/api/profiles/x", {"Host": "tv.lan:8743"}, b"{}", True)[1] == {
        "error": "Enter kia's password first."}  # fmt: skip


def test_someone_guessing_is_an_alert_once(cfg, monkeypatch):
    # The waits between tries would take minutes: a clock that moves on 2 minutes a try.
    clock = [1000.0]

    def later():
        clock[0] += 120
        return clock[0]

    monkeypatch.setattr(api, "_clock", later)
    said = []
    cfg.alert = lambda event, title, message: said.append((event, title, message))

    def arrived(n):  # alerts go out on a thread of their own, so the answer doesn't wait
        deadline = time.time() + 5  # (the cfg fixture makes time.sleep return at once)
        while len(said) < n and time.time() < deadline:
            pass

    def try_(password, client="192.0.2.9"):
        headers = {"Host": "tv.lan:8743", "X-Kia-Password": password}
        return api.handle(cfg, "PUT", "/api/profiles/x", headers, b'{"name": "x"}', True, client)[0]

    for _ in range(api.GUESSES * 2):
        assert try_("nope") == 401
    arrived(1)
    assert said == [("guessing", "kia: 10 wrong tries at the Dashboard's password from 192.0.2.9",
                     said[0][2])] and "192.0.2.9 got the Dashboard's password wrong 10 times in a row" in said[0][2]  # fmt: skip
    assert try_("secret") == 200  # the right one ends the streak
    for _ in range(api.GUESSES):
        try_("nope")
    arrived(2)
    assert len(said) == 2  # arrived(2) above waited for it; a third would need 10 more


def test_a_password_the_browser_can_send(tmp_path: Path):
    a = auth.Auth(tmp_path)
    with pytest.raises(ValueError, match="no accents or emoji"):
        a.change("pässwörd-123")
    said = []
    auth.Auth(tmp_path, "pässwörd-123").ensure(said.append)
    assert "characters a browser can't send" in said[0]


def test_a_failed_https_connection_is_one_line_in_the_log(cfg, certs, capfd):
    secure = serve.make_server(cfg.output_dir, "127.0.0.1", 0, cfg, tls_dir=certs)
    threading.Thread(target=secure.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(secure.server_address, timeout=5) as s, contextlib.suppress(OSError):
            s.sendall(b"GET / HTTP/1.0\r\n\r\n")  # http:// to the https port
            s.recv(100)  # kia answers with a TLS alert, or just closes it
    finally:  # server_close waits for the request's thread, so its log line is written
        secure.shutdown()
        secure.server_close()
    err = capfd.readouterr().err
    assert "didn't finish connecting (SSLError)" in err and "Traceback" not in err


# Every API request that changes something, or shows players' names, with a body it would
# accept. Each one asks for the password on its own (api.handle), so each is tried here.
PROTECTED = [
    ("PUT", "/api/profiles/kids", {"groups": ["Kids"]}),
    ("DELETE", "/api/profiles/kids", None),
    ("POST", "/api/channel/test", {"key": "CNN.us"}),
    ("POST", "/api/channel/remove", {"key": "CNN.us"}),
    ("POST", "/api/channel/restore", {"key": "CNN.us"}),
    ("POST", "/api/renumber", None),
    ("PUT", "/api/channel", {"key": "CNN.us", "favorite": True}),
    ("GET", "/api/players", None),
    ("PUT", "/api/sources", {"url": "https://example.com/us.m3u", "on": False}),
    ("POST", "/api/sources", {"url": "https://example.com/more.m3u", "name": "more"}),
    ("DELETE", "/api/sources?url=https://example.com/us.m3u", None),
    ("POST", "/api/sources/check", {"url": "https://example.com/us.m3u"}),
    ("DELETE", "/api/sources/check", None),
    ("POST", "/api/password", {"new": "something-new"}),
    ("POST", "/api/stack/check", {"parts": ["plex"]}),
    ("POST", "/api/stack/apply", {"parts": ["plex"]}),
    ("POST", "/api/plex/tuners", None),
    ("PUT", "/api/name", {"name": "den"}),
    ("POST", "/api/asks/approve", {"id": "0123456789abcdef"}),
    ("POST", "/api/asks/decline", {"id": "0123456789abcdef"}),
    ("POST", "/api/asks/stop-allowing", {}),
]
PUBLIC = [("GET", "/api/profiles"), ("POST", "/api/preview"), ("GET", "/api/channels"), ("GET", "/api/channel-choices"),
          ("GET", "/api/sources"), ("GET", "/api/sources/check"), ("GET", "/api/stack"),
          ("GET", "/api/plex/tuners"), ("GET", "/api/doctor"), ("GET", "/api/doctor/channel"), ("GET", "/api/doctor/plex"),
          ("GET", "/api/doctor/hardware"),
          ("GET", "/api/doctor/jellyfin"),
          ("GET", "/api/doctor/guide"),
          ("GET", "/api/doctor/pluto"), ("GET", "/api/doctor/setup"), ("GET", "/api/name"), ("GET", "/api/asks")]  # fmt: skip
# Routes with a credential of their own: the MCP server's token (tests/test_mcp.py checks it).
TOKEN_ROUTES = [("POST", "/api/mcp")]


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED, ids=lambda x: x if isinstance(x, str) else "")
def test_every_change_needs_the_password_over_https(cfg, method, path, body):
    def files() -> dict[str, bytes]:  # everything kia could have written: state/, output/, profiles, a stack request
        root = cfg.output_dir.parent
        return {str(f.relative_to(root)): f.read_bytes() for f in sorted(root.rglob("*")) if f.is_file()}

    before = files()
    assert call(cfg, method, path, body)[0] == 401
    assert call(cfg, method, path, body, password="nope")[0] == 401
    assert call(cfg, method, path, body, password="secret", secure=False)[0] == 403
    assert cfg.changes == [] and files() == before


def test_every_route_is_in_one_of_the_lists():
    # A new route has to be added above, as one that needs the password or one that doesn't.
    import inspect
    import re

    source = inspect.getsource(api.handle)
    routes = {(m, "/api/" + "/".join(re.findall(r'"([a-z-]+)"', p)))
              for p, m in re.findall(r'parts == \[([^\]]*)\] and method == "([A-Z]+)"', source)}  # fmt: skip
    listed = {(m, p.split("?")[0]) for m, p, _ in PROTECTED if not p.startswith("/api/profiles/")} | set(PUBLIC)
    assert routes == listed
    # And every route is written that way, so none slips past the pattern above: the only
    # others are these three, each covered by its own test.
    others = {'if parts == ["mcp"]:', 'if parts and parts[0] == "sources" and method in ("PUT", "POST", "DELETE"):',
              'if len(parts) == 2 and parts[0] == "profiles" and method in ("PUT", "DELETE"):'}  # fmt: skip
    for line in source.splitlines():
        line = line.split("  #")[0].strip()
        if re.match(r"(el)?if .*\bparts\b", line):
            assert re.fullmatch(r'if parts == \[[^\]]*\] and method == "[A-Z]+":', line) or line in others, line
    # Routes written another way (parts[0] == "sources" and method in (...), say): each part of
    # the API the code names is in one of the lists.
    named = set(re.findall(r'parts(?:\[0\]|\[:1\])? == \[?"([a-z-]+)"', source))
    covered = ({p.split("/")[2].split("?")[0] for _, p, _ in PROTECTED} | {p.split("/")[2] for _, p in PUBLIC}
               | {p.split("/")[2] for _, p in TOKEN_ROUTES})  # fmt: skip
    assert named and named <= covered, named - covered


def test_nginx_sets_who_asked_and_clears_the_secure_flag_itself():
    # kia believes X-Real-IP (whose wrong passwords to slow down) and X-Kia-Secure (whether a
    # request came over HTTPS) only from nginx. So nginx has to set both on every location
    # that reaches kia; a header the browser sent would otherwise pass straight through.
    deploy = Path(__file__).resolve().parent.parent / "deploy"
    api_inc = (deploy / "kia-api.inc").read_text()
    assert "proxy_set_header X-Real-IP $remote_addr;" in api_inc
    common = (deploy / "kia-common.inc").read_text()
    for location in ("location ^~ /pluto/ {", "location = /metrics {"):
        block = common.split(location, 1)[1].split("}", 1)[0]
        assert "proxy_set_header X-Real-IP $remote_addr;" in block and 'proxy_set_header X-Kia-Secure "";' in block, location
    nginx = (deploy / "nginx.conf").read_text()
    assert nginx.count('proxy_set_header X-Kia-Secure "0";') == 1 and nginx.count('proxy_set_header X-Kia-Secure "1";') == 1


def test_crafted_api_addresses_are_refused(cfg):
    import urllib.error

    server = serve.make_server(cfg.output_dir, "127.0.0.1", 0, cfg)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        for path in ("/api/players;%2F..%2F..%2Fmetrics", "/api/profiles%2Fx", "/api/./profiles", "/api/%2e%2e/x"):
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(base + path, timeout=10)
            assert refused.value.code == 400, path
            refused.value.close()
        with urllib.request.urlopen(f"{base}/api/profiles?x=1", timeout=10) as r:  # a query is fine
            assert r.status == 200
    finally:
        server.shutdown()


def test_a_burst_of_wrong_passwords_is_slowed_down_like_one_at_a_time(cfg, monkeypatch):
    # Sent all at once, each guess used to read the count before the others added to it,
    # so a burst counted as one. Now they take turns, and only the first gets checked.
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(api, "_sleep", lambda s: None)
    checked = []
    real = cfg.auth.check
    monkeypatch.setattr(cfg.auth, "check", lambda given: checked.append(given) or real(given))

    def guess(n):
        api._CLIENT.set("192.0.2.66")
        try:
            api.check_password(cfg, f"wrong-{n}", True)
        except api.ApiError as exc:
            return exc.status
        return 200

    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(guess, range(20)))
    assert statuses.count(401) == 2 and statuses.count(429) == 18  # the first two, then the wait
    assert len(checked) == 2


def test_an_ipv6_address_gets_one_certificate_and_a_working_link(tmp_path, monkeypatch):
    if not shutil.which("openssl"):
        pytest.skip("needs openssl")
    from kia import cli

    d, logged = tmp_path / "tls", []
    hosts = tls.names("http://[fd00::5]:8780", [])
    tls.ensure(d, hosts, logged.append)
    assert "fd00::5" in tls.info(d)["names"]  # openssl wrote it as FD00:0:0:0:0:0:0:5
    tls.ensure(d, hosts, logged.append)  # the next start: no new certificate
    assert len(logged) == 1

    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    (tmp_path / "kia.toml").write_text('[output]\npublic_url = "http://[fd00::5]:8780"\n')
    monkeypatch.delenv("KIA_PUBLIC_URL", raising=False)
    monkeypatch.delenv("KIA_TLS_NAMES", raising=False)
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    cli.secure_setup(cli.load(tmp_path / "kia.toml"))
    assert json.loads((tmp_path / "state" / "secure.json").read_text())["url"] == "https://[fd00::5]:8743/"


def test_grafanas_link_brackets_an_ipv6_address(monkeypatch):
    from kia import htmlpage

    monkeypatch.delenv("KIA_GRAFANA_URL", raising=False)
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "monitoring")
    monkeypatch.setenv("KIA_PUBLIC_URL", "http://[fd00::5]:8780")
    assert htmlpage.grafana_url() == "https://[fd00::5]:8788"


def test_a_body_that_isnt_json_and_an_address_kia_doesnt_have(cfg):
    status, data = api.handle(cfg, "POST", "/api/preview", {"Host": "tv.lan:8743"}, b"{not json", True)
    assert status == 400 and data["error"] == "The request wasn't valid JSON."
    status, _ = call(cfg, "GET", "/api/nothing-here")
    assert status == 404


def test_kia_doctor_through_the_api(cfg, tmp_path):
    # Without kia's settings file there's nothing to read the answers from.
    assert call(cfg, "GET", "/api/doctor")[0] == 409
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://example.com/us.m3u\n")
    cfg.config_file = tmp_path / "kia.toml"
    status, body = call(cfg, "GET", "/api/doctor", secure=False)  # a read: open on the plain port too
    assert status == 200 and body["reports"][0]["subject"] == "kia"
    (cfg.output_dir / "curated.m3u").write_text('#EXTM3U\n#EXTINF:-1 tvg-id="CNN.us",CNN\nhttps://a/cnn\n')
    status, body = call(cfg, "GET", "/api/doctor/channel?q=CNN.us")
    assert status == 200 and body["reports"][0]["subject"] == "CNN (CNN.us)"
    assert call(cfg, "GET", "/api/doctor/channel?q=" + "x" * 201)[0] == 400
    assert cfg.changes == []


def test_a_crowd_of_addresses_doesnt_reset_one_still_waiting(monkeypatch):
    # Past 1000 addresses, kia forgets those whose wait is over, to keep the table small;
    # one that must still wait keeps its streak.
    now = [1000.0]
    monkeypatch.setattr(api, "_clock", lambda: now[0])
    monkeypatch.setattr(api, "_sleep", lambda s: None)
    monkeypatch.setattr(api, "_WRONG", {("password", f"192.0.2.{i % 250}-{i}"): (1, 10.0) for i in range(1001)})
    api._WRONG[("password", "198.51.100.7")] = (5, 2000.0)  # still waiting
    token = api._CLIENT.set("192.0.2.200")
    try:
        with pytest.raises(api.ApiError):
            api._slowed("password", lambda: False, "Wrong.", "Too many.")
    finally:
        api._CLIENT.reset(token)
    assert api._WRONG[("password", "198.51.100.7")] == (5, 2000.0) and len(api._WRONG) == 2


def test_the_tool_call_table_forgets_only_idle_callers(monkeypatch):
    from collections import deque

    from kia import mcp

    monkeypatch.setattr(mcp, "_CALLS", {f"idle-{i}": deque([0.0]) for i in range(1001)})
    mcp._CALLS["busy"] = deque([95.0] * mcp.CALLS_PER_MINUTE)
    assert mcp._allowed("new", now=100.0)
    assert set(mcp._CALLS) == {"busy", "new"} and not mcp._allowed("busy", now=100.0)  # still at its limit
