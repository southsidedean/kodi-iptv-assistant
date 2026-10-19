import json
import threading
import urllib.request
from pathlib import Path

import pytest

from kia import api, profiles, serve
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
    c = api.ApiConfig(out, state, tmp_path / "profiles" / "profiles.toml", None, "secret", lambda: changes.append(1))
    c.changes = changes
    return c


def call(cfg, method, path, body=None, password=None, origin=None, host="tv.lan:8780"):
    headers = {"Host": host}
    if password:
        headers["X-Kia-Password"] = password
    if origin:
        headers["Origin"] = origin
    return api.handle(cfg, method, path, headers, json.dumps(body).encode() if body is not None else b"")


def test_read_and_preview(cfg):
    status, data = call(cfg, "GET", "/api/profiles")
    assert status == 200 and data["editable"] and data["profiles"] == [] and data["total"] == 3
    assert {"name": "Kids", "count": 1} in data["groups"] and {"name": "kink", "count": 1} in data["sources"]
    assert call(cfg, "POST", "/api/preview", {"groups": ["kids"]})[1] == {"tv": 1, "radio": 0, "sample": ["Toons"]}
    assert call(cfg, "POST", "/api/preview", {"tv": False})[1]["radio"] == 1
    assert call(cfg, "POST", "/api/preview", {"names": ["("]})[0] == 400
    assert call(cfg, "GET", "/api/channels?q=cn")[1]["channels"] == [{"id": "CNN.us", "name": "CNN", "group": "News"}]


def test_save_and_delete_need_the_password(cfg):
    assert call(cfg, "PUT", "/api/profiles/kids", {"groups": ["Kids"]})[0] == 401
    assert call(cfg, "PUT", "/api/profiles/kids", {"groups": ["Kids"]}, password="nope")[0] == 401
    assert not cfg.profiles_file.exists()
    cfg.password = None
    status, data = call(cfg, "PUT", "/api/profiles/kids", {"groups": ["Kids"]}, password="secret")
    assert status == 403 and "KIA_ADMIN_PASSWORD" in data["error"]
    assert call(cfg, "GET", "/api/profiles")[1]["editable"] is False


def test_other_websites_are_refused(cfg):
    assert call(cfg, "GET", "/api/profiles", origin="http://evil.example")[0] == 403
    assert call(cfg, "PUT", "/api/profiles/kids", {}, password="secret", origin="http://evil.example")[0] == 403
    assert call(cfg, "GET", "/api/profiles", origin="http://tv.lan:8780")[0] == 200


def test_save_writes_the_file_and_playlists(cfg):
    status, data = call(cfg, "PUT", "/api/profiles/kids", {"title": "The kids", "groups": ["Kids"], "radio": False}, password="secret")
    assert status == 200 and data["profiles"][0] == {"name": "kids", "title": "The kids", "file": "kids.m3u", "tv": 1, "radio": 0, "guide": None}
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


def test_served_over_http(cfg):
    server = serve.make_server(cfg.output_dir, "127.0.0.1", 0, cfg)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/api/profiles") as r:
            assert json.load(r)["total"] == 3
        req = urllib.request.Request(f"{base}/api/profiles/news", data=b'{"groups":["News"]}', method="PUT",
                                     headers={"X-Kia-Password": "secret", "Content-Type": "application/json"})  # fmt: skip
        with urllib.request.urlopen(req) as r:
            assert json.load(r)["profiles"][0]["tv"] == 1
        with urllib.request.urlopen(f"{base}/news.m3u") as r:
            assert b"CNN" in r.read()
    finally:
        server.shutdown()
