"""kia.name: this kia's name, for telling several installs apart."""

import json
from pathlib import Path

import pytest

from kia import api, auth, mcp, metrics
from kia import name as kia_name


def config(tmp_path: Path) -> api.ApiConfig:
    return api.ApiConfig(tmp_path / "output", tmp_path / "state", tmp_path / "profiles.toml", None, auth.Auth(tmp_path / "state", "secret"))


def test_where_the_name_comes_from(tmp_path: Path, monkeypatch):
    assert kia_name.current(tmp_path) == ("", "")  # Docker without KIA_NAME (conftest: no computer name)
    monkeypatch.setattr(kia_name, "computer_name", lambda: "trans-box")
    assert kia_name.current(tmp_path) == ("trans-box", "computer")
    monkeypatch.setenv("KIA_NAME", "  Den Box!  ")  # .env is yours: made into a name, not refused
    assert kia_name.current(tmp_path) == ("den-box", "env")
    assert kia_name.save(tmp_path, "cabin-pi") == "cabin-pi"
    assert kia_name.current(tmp_path) == ("cabin-pi", "dashboard")  # the Dashboard's comes first
    assert kia_name.save(tmp_path, "") == "den-box"  # cleared: back to KIA_NAME
    assert not (tmp_path / kia_name.FILE).exists()


@pytest.mark.parametrize("bad", ["Den", "-den", "den-", "den box", "x" * 41, "den\nother", "../den", "dén"])
def test_only_a_name_is_saved(tmp_path: Path, bad):
    with pytest.raises(ValueError, match="lowercase letters"):
        kia_name.save(tmp_path, bad)
    assert not (tmp_path / kia_name.FILE).exists()


def test_a_damaged_file_isnt_a_name(tmp_path: Path, monkeypatch):
    # state/ is kia's to write, and a taken-over kia container's too: only a name counts.
    monkeypatch.setenv("KIA_NAME", "den")
    for text in ("<script>", "x" * 500, "den\nother", ""):
        (tmp_path / kia_name.FILE).write_text(text)
        assert kia_name.current(tmp_path) == ("den", "env"), text


@pytest.mark.parametrize("raw, name", [("my-server.lan", "my-server-lan"), ("ÀB C", "b-c"), ("---", ""), ("a" * 50, "a" * 40)])
def test_clean(raw, name):
    assert kia_name.clean(raw) == name


def test_titles():
    assert kia_name.titled("kia: nightly run failed", "den") == "kia (den): nightly run failed"
    assert kia_name.titled("kia check: local DNS problem", "den") == "kia (den) check: local DNS problem"
    assert kia_name.titled("kia: nightly run failed", "") == "kia: nightly run failed"
    assert kia_name.titled("kiaddress", "den") == "kiaddress"


def test_the_api_reads_it_for_anyone_and_changes_it_with_the_password(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(api, "_sleep", lambda s: None)
    monkeypatch.setenv("KIA_NAME", "den")
    (tmp_path / "output").mkdir()
    cfg = config(tmp_path)

    def call(method, body=None, password=None, secure=True):
        headers = {"Host": "tv.lan:8743", **({"X-Kia-Password": password} if password else {})}
        return api.handle(cfg, method, "/api/name", headers, json.dumps(body).encode() if body is not None else b"", secure)

    assert call("GET") == (200, {"name": "den", "from": "env", "env": "den", "computer": ""})
    assert call("PUT", {"name": "cabin"})[0] == 401 and call("PUT", {"name": "cabin"}, "secret", secure=False)[0] == 403
    status, body = call("PUT", {"name": "cabin"}, "secret")
    assert status == 200 and body["name"] == "cabin" and body["from"] == "dashboard"
    status, body = call("PUT", {"name": "Not A Name"}, "secret")
    assert status == 400 and "lowercase letters" in body["error"] and call("GET")[1]["name"] == "cabin"
    assert call("PUT", {"name": ""}, "secret")[1]["name"] == "den"


def test_assistants_setups_are_named_after_it():
    plain = mcp.setup_for("claude-code", "https://tv.lan:8743/api/mcp", "TOKEN")
    assert "--transport http kia https://" in plain  # one kia, no name: as before
    named = {a: mcp.setup_for(a, "https://tv.lan:8743/api/mcp", "TOKEN", "cabin-pi") for a in mcp.ASSISTANTS}
    assert "--transport http kia-cabin-pi https://" in named["claude-code"]
    assert '"kia-cabin-pi": {' in named["claude-desktop"] and " kia-cabin-pi https://" in named["gemini"]
    assert "[mcp_servers.kia-cabin-pi]" in named["codex"] and 'bearer_token_env_var = "KIA_MCP_TOKEN_CABIN_PI"' in named["codex"]
    assert "KIA_MCP_TOKEN_CABIN_PI=TOKEN" in named["codex"] and "KIA_MCP_TOKEN_CABIN_PI=TOKEN" in named["cursor"]
    assert "${env:KIA_MCP_TOKEN_CABIN_PI}" in named["cursor"]
    vscode = json.loads(named["vscode"].split("\n\n")[1])  # two installs keep two tokens
    assert vscode["inputs"][0]["id"] == "kia-cabin-pi-token"
    assert vscode["servers"]["kia-cabin-pi"]["headers"]["Authorization"] == "Bearer ${input:kia-cabin-pi-token}"


def test_the_mcp_server_says_its_name(tmp_path: Path, monkeypatch):
    cfg = config(tmp_path)
    hello = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}).encode()
    info = mcp.handle(lambda: None, cfg, {}, hello)[1]["result"]
    assert info["serverInfo"]["name"] == "kia" and "called" not in info["instructions"]
    monkeypatch.setenv("KIA_NAME", "den")
    info = mcp.handle(lambda: None, cfg, {}, hello)[1]["result"]
    assert info["serverInfo"]["name"] == "kia-den" and info["serverInfo"]["title"] == "kia (kodi-iptv-assistant) on den"
    assert "This kia is called den" in info["instructions"]


def test_kia_info_carries_it(tmp_path: Path, monkeypatch):
    (tmp_path / "output").mkdir()
    assert 'kia_info{version="' in metrics.render(tmp_path / "output", state_dir=tmp_path / "state")
    monkeypatch.setenv("KIA_NAME", "den")
    assert ',name="den"} 1' in metrics.render(tmp_path / "output", state_dir=tmp_path / "state")
