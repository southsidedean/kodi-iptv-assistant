import json
import shutil
import threading
from pathlib import Path

import pytest

from kia import api, auth, profiles, serve, tls
from kia.m3u import Entry, write


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch) -> api.ApiConfig:
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
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
    assert call(cfg, "GET", "/api/channels?q=cn")[1]["channels"] == [{"id": "CNN.us", "name": "CNN", "group": "News"}]


def test_save_and_delete_need_the_password(cfg):
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
    assert a.source == "generated" and shown in logged[0] and a.check(shown) and not a.check("nope")
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


def test_served_over_https(cfg, certs):
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
        return urllib.request.urlopen(req, **kw)

    try:
        with urllib.request.urlopen(f"{https}/api/profiles", context=context) as r:
            assert json.load(r)["secure"] is True
        with pytest.raises(urllib.error.HTTPError) as refused:
            put(http)
        assert refused.value.code == 403
        with put(https, context=context) as r:
            assert json.load(r)["profiles"][0]["tv"] == 1
        with urllib.request.urlopen(f"{http}/news.m3u") as r:  # playlists stay on plain HTTP
            assert b"CNN" in r.read()
        # Behind nginx: only its X-Kia-Secure header makes a request secure.
        proxied = serve.make_server(cfg.output_dir, "127.0.0.1", 0, cfg, trust_proxy=True)
        threading.Thread(target=proxied.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{proxied.server_address[1]}"
        with urllib.request.urlopen(urllib.request.Request(f"{base}/api/profiles", headers={"X-Kia-Secure": "1"})) as r:
            assert json.load(r)["secure"] is True
        with urllib.request.urlopen(f"{base}/api/profiles") as r:
            assert json.load(r)["secure"] is False
        proxied.shutdown()
    finally:
        secure.shutdown()
        plain.shutdown()
