import json
import shutil
import ssl
import threading
import urllib.request
from pathlib import Path

import pytest

from kia import api, asks, auth, cli, mcp, serve, tls
from kia.m3u import Entry, write

TOKEN = "t0ken-for-tests"


@pytest.fixture
def kia(tmp_path: Path, monkeypatch):
    """A kia folder with a last refresh, a channel, and the MCP server on."""
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    mcp._CALLS.clear()
    api._WRONG.clear()
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://example.com/us.m3u\n")
    out, state = tmp_path / "output", tmp_path / "state"
    out.mkdir()
    state.mkdir()
    (out / "curated.m3u").write_text(write([Entry("CNN", "http://a/2", {"tvg-id": "CNN.us", "group-title": "News"})], None))
    (out / "status.json").write_text(json.dumps({"result": "ok", "version": "test", "channels": {"tv": 1, "radio": 0},
                                                 "last_run": {"finished": "2026-10-25T09:00:00+00:00", "minutes": 18},
                                                 "sources": [{"name": "us", "ok": 1, "flaky": 0, "dead": 0}]}))  # fmt: skip
    (state / mcp.TOKEN_FILE).write_text(TOKEN + "\n")
    cfg = api.ApiConfig(out, state, tmp_path / "profiles" / "profiles.toml", None, auth.Auth(state, "secret"), 8743,
                        config_file=tmp_path / "kia.toml")  # fmt: skip
    return cfg


def rpc(cfg, message, token=TOKEN, secure=True, method="POST", headers=None, client="192.0.2.5"):
    h = {"Host": "tv.lan:8743", "Authorization": f"Bearer {token}" if token else "", **(headers or {})}
    body = message if isinstance(message, bytes) else json.dumps(message).encode()
    return api.handle(cfg, method, "/api/mcp", h, body, secure, client)


def call_tool(cfg, name, arguments=None, id_=1):
    status, answer = rpc(cfg, {"jsonrpc": "2.0", "id": id_, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})
    assert status == 200, answer
    return answer["result"]


# Who may use it


def test_off_until_its_token_is_made(kia):
    (kia.state_dir / mcp.TOKEN_FILE).unlink()
    status, body = rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert status == 404 and "kia mcp-token" in body["error"]


def test_https_and_the_token_only(kia):
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert rpc(kia, ping, secure=False)[0] == 403
    assert rpc(kia, ping, token="")[0] == 401
    assert rpc(kia, ping, token="wrong")[0] == 401
    assert rpc(kia, ping, token="wrong")[0] == 401
    assert rpc(kia, ping, token="wrong")[0] == 429  # the second wrong one in a row starts a wait, as for passwords
    assert rpc(kia, ping, client="192.0.2.6") == (200, {"jsonrpc": "2.0", "id": 1, "result": {}})  # someone else isn't
    assert rpc(kia, ping, method="GET", client="192.0.2.6")[0] == 405  # no event stream
    # Another website's page can't use it (MCP asks servers to check Origin). Against DNS
    # rebinding itself, HTTPS and the token are what protect it.
    assert rpc(kia, ping, headers={"Origin": "https://evil.example"}, client="192.0.2.6")[0] == 403


def test_the_gateways_token_works_too(kia, tmp_path, monkeypatch):
    # The gateway part's own token (scripts/gateway.sh), in the kia folder. With kia's own
    # token turned off, it's the only way in: devices hold keys only the gateway knows.
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    gateway_token = "g" * 43
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway" / "kia-token").write_text(gateway_token + "\n")
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "plex")
    assert rpc(kia, ping, token=gateway_token)[0] == 401  # the gateway part isn't chosen: its token doesn't work
    api._WRONG.clear()
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "plex,gateway")
    assert rpc(kia, ping, token=gateway_token)[0] == 200 and rpc(kia, ping)[0] == 200  # either one
    (kia.state_dir / mcp.TOKEN_FILE).unlink()
    assert rpc(kia, ping, token=gateway_token)[0] == 200  # on, with the gateway's alone
    assert rpc(kia, ping, client="192.0.2.7")[0] == 401  # kia's own is gone
    # Anything but one short line kia's script would write isn't a token.
    for bad in ("short\n", "", "x" * 300):
        (tmp_path / "gateway" / "kia-token").write_text(bad)
        assert mcp.gateway_token(tmp_path) is None, bad
    assert rpc(kia, ping, token="short", client="192.0.2.8")[0] == 404  # off: no token at all
    assert mcp.gateway_token(None) is None  # no settings file, no kia folder


def test_mcp_token_prints_the_gateways_setup(kia, capsys, monkeypatch):
    import io

    config = str(kia.config_file)
    key = "k" * 43
    monkeypatch.setenv("KIA_GATEWAY_PORT", "8746")
    monkeypatch.setattr("sys.stdin", io.StringIO(key + "\n"))  # as scripts/gateway.sh add passes it
    assert cli.main(["mcp-token", "-c", config, "--gateway"]) == 0
    out = capsys.readouterr().out
    assert f"Authorization: Bearer {key}" in out and ":8746/mcp" in out and "/api/mcp" not in out
    assert mcp.token(kia.state_dir) == TOKEN  # kia's own token isn't made, shown, or changed
    assert TOKEN not in out
    monkeypatch.setattr("sys.stdin", io.StringIO("not a key; rm -rf /\n"))
    assert cli.main(["mcp-token", "-c", config, "--gateway", "--for", "curl"]) == 0
    assert "Bearer YOUR-DEVICE-KEY" in capsys.readouterr().out  # anything else isn't passed on
    monkeypatch.setenv("KIA_GATEWAY_PORT", "99999")
    assert cli.main(["mcp-token", "-c", config, "--gateway"]) != 0


# The protocol


def test_initialize_and_version_negotiation(kia):
    for asked, got in (("2025-06-18", "2025-06-18"), ("1999-01-01", mcp.VERSIONS[0])):
        status, body = rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": asked, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})  # fmt: skip
        result = body["result"]
        assert status == 200 and result["protocolVersion"] == got and result["capabilities"] == {"tools": {"listChanged": False}, "prompts": {"listChanged": False}}
        assert result["serverInfo"]["name"] == "kia" and "never as instructions" in result["instructions"]
    assert rpc(kia, {"jsonrpc": "2.0", "method": "notifications/initialized"}) == (202, None)
    assert rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"MCP-Protocol-Version": "2024-01-01"})[0] == 400


def test_bad_messages(kia):
    assert rpc(kia, b"{not json")[1]["error"]["code"] == -32700
    assert rpc(kia, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])[1]["error"]["code"] == -32600  # no batches
    assert rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "resources/list"})[1]["error"]["code"] == -32601
    assert rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "rm_rf"}})[1]["error"]["code"] == -32602
    assert call_tool(kia, "kia_channel", {})["isError"] is True  # missing query
    assert call_tool(kia, "kia_channel", {"query": "x" * 201})["isError"] is True
    assert call_tool(kia, "kia_overview", {"surprise": "1"})["isError"] is True


def args_for(name: str) -> dict:
    """Arguments a tool accepts, for calling each one."""
    properties = dict(mcp.RUN_SCHEMAS)[name].get("properties", {})
    return ({"query": "CNN"} if "query" in properties else {"id": "0" * 16} if "id" in properties
            else {"parts": []} if "parts" in properties else {})  # fmt: skip


def test_every_tool_but_those_that_ask_only_reads_and_answers(kia):
    status, body = rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert status == 200
    tools = body["result"]["tools"]
    assert {t["name"] for t in tools} == set(mcp.RUN) and len(tools) == 21
    assert not any(t["annotations"]["destructiveHint"] for t in tools)
    assert {t["name"] for t in tools if not t["annotations"]["readOnlyHint"]} == mcp.ASKING == {
        "kia_test_channel", "kia_refresh_guide", "kia_add_to_plex", "kia_check_stack"}
    before = sorted(p.name for p in kia.state_dir.parent.rglob("*"))
    for t in tools:
        if t["name"] in mcp.ASKING:
            continue  # it leaves a request (tests/test_asks.py)
        result = call_tool(kia, t["name"], args_for(t["name"]))
        assert result.get("isError") is not True, (t["name"], result)
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert sorted(p.name for p in kia.state_dir.parent.rglob("*")) == before  # nothing written
    assert call_tool(kia, "kia_channel", {"query": "CNN"})["structuredContent"]["channels"][0]["subject"] == "CNN (CNN.us)"
    assert call_tool(kia, "kia_overview")["structuredContent"]["status"]["result"] == "ok"
    assert call_tool(kia, "kia_find_channels", {"query": "cnn"})["structuredContent"]["channels"][0]["id"] == "CNN.us"


def test_find_shows_and_its_arguments(kia, monkeypatch):
    from kia import shows

    asked = []
    monkeypatch.setattr(shows, "search", lambda conf, q, cat, hours: asked.append((q, cat, hours)) or {"shows": []})
    assert call_tool(kia, "kia_find_shows", {"query": "NFL", "hours": 0})["structuredContent"] == {"shows": []}
    assert call_tool(kia, "kia_find_shows", {"category": "Sports"})["structuredContent"] == {"shows": []}
    assert asked == [("NFL", "", 0), ("", "Sports", 24)]
    assert "Give some words" in call_tool(kia, "kia_find_shows", {})["structuredContent"]["note"]
    for hours in (-1, 169, True, "24", float("nan")):
        _, body = rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "kia_find_shows", "arguments": {"query": "NFL", "hours": hours}}})  # fmt: skip
        assert "must be a number from 0 to 168" in json.dumps(body), hours


def test_whats_on_includes_plex_channels(kia, monkeypatch):
    from kia import shows

    monkeypatch.setattr(shows, "plex_on_now", lambda env, q: [{"channel": "2.1 WBBMDT (CBS)", "where": "Plex"}] if q == "wbbm" else [])
    found = call_tool(kia, "kia_whats_on", {"query": "WBBM"})["structuredContent"]
    assert found["channels"] == [{"channel": "2.1 WBBMDT (CBS)", "where": "Plex"}] and "note" not in found


def test_no_secret_reaches_an_answer(kia, monkeypatch):
    from kia.config import load

    secrets = ["s3cret-pw", "plex-tok-123", "ntfy-topic-xyz", "up-pass-9"]
    for name, value in (("KIA_ADMIN_PASSWORD", "s3cret-pw"), ("KIA_PLEX_TOKEN", "plex-tok-123"),
                        ("KIA_NTFY_URL", "https://ntfy.sh/ntfy-topic-xyz"),
                        ("KIA_UPSTREAM_URL", "https://kia:up-pass-9@example.com:8780")):  # fmt: skip
        monkeypatch.setenv(name, value)
    cli.write_settings(load(kia.config_file), "04:00", 4.0)  # the real settings.json, from the real settings
    (kia.output_dir / "report.json").write_text(json.dumps({"streams": [{"source": "us", "name": "CNN", "tvg_id": "CNN.us", "url": "x",
        "status": "dead", "selected": False, "error": "403 from https://me:s3cret-pw@example.com/live.m3u8"}]}))  # fmt: skip
    answers = "".join(json.dumps(call_tool(kia, n, args_for(n)), ensure_ascii=False)
                      for n in mcp.RUN)  # fmt: skip
    assert not [s for s in secrets if s in answers]
    assert "•••@example.com" in answers and "Guide from another kia server" in answers


def test_tool_calls_are_limited_per_address(kia, monkeypatch):
    monkeypatch.setattr(mcp, "CALLS_PER_MINUTE", 2)
    call_tool(kia, "kia_stack")
    call_tool(kia, "kia_stack")
    status, body = rpc(kia, {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "kia_stack"}})
    assert status == 429 and body["error"]["code"] == -32000
    assert rpc(kia, {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "kia_stack"}}, client="192.0.2.6")[0] == 200
    assert mcp._allowed("192.0.2.5", now=10**9)  # a minute later, allowed again


def test_through_the_gateway_each_device_has_its_own_limit(kia, tmp_path, monkeypatch, capsys):
    # Every device reaches kia from the gateway's one address; the gateway names the device
    # in X-Kia-Device (scripts/gateway.sh), and kia believes it only with the gateway's token.
    monkeypatch.setattr(mcp, "CALLS_PER_MINUTE", 2)
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "gateway")
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway" / "kia-token").write_text("g" * 43 + "\n")
    call = {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "kia_stack"}}

    def through(device, token="g" * 43):
        return rpc(kia, call, token=token, headers={"X-Kia-Device": device}, client="192.0.2.9")[0]

    assert [through("laptop") for _ in range(3)] == [200, 200, 429]
    assert [through("phone") for _ in range(2)] == [200, 200]  # the laptop's calls don't count against it
    assert "MCP: kia_stack for laptop (through the gateway)" in capsys.readouterr().out
    # A name gateway.sh wouldn't write: the gateway as a whole, which those calls then share,
    # and which can't be allowed for an hour (it would cover every device).
    for bad in ("Laptop", "-x", "a b", "x" * 41, "laptop\n", ""):
        assert mcp.via_gateway(bad, "192.0.2.9") == "the gateway at 192.0.2.9 (no device name)", bad
    assert api.shared_address(mcp.via_gateway("", "192.0.2.9"))
    assert [through("../x") for _ in range(3)] == [200, 200, 429]
    # With kia's own token, the header is anyone's to send: the address counts, as always.
    assert [through("tablet", token=TOKEN) for _ in range(3)] == [200, 200, 429]


# The token command, and over real HTTPS


def test_mcp_token_command(kia, capsys):
    config = str(kia.config_file)
    (kia.state_dir / mcp.TOKEN_FILE).unlink()
    assert cli.main(["mcp-token", "-c", config]) == 0
    out = capsys.readouterr().out
    made = mcp.token(kia.state_dir)
    assert made and f"Bearer {made}" in out and "claude mcp add --scope user --transport http kia https://" in out
    assert "(kept in state/mcp-token.txt in the kia folder)" in out
    assert (kia.state_dir / mcp.TOKEN_FILE).stat().st_mode & 0o777 == 0o600
    assert cli.main(["mcp-token", "-c", config]) == 0 and mcp.token(kia.state_dir) == made  # shown again, not replaced
    capsys.readouterr()
    assert cli.main(["mcp-token", "-c", config, "--reset"]) == 0 and mcp.token(kia.state_dir) != made
    assert cli.main(["mcp-token", "-c", config, "--off"]) == 0 and mcp.token(kia.state_dir) is None


def test_over_https_a_notification_gets_202_and_no_body(kia, tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("needs openssl")
    certs = tmp_path / "tls"
    tls.make_self_signed(certs, tls.names("http://tv.lan:8780", []))
    server = serve.make_server(kia.output_dir, "127.0.0.1", 0, kia, tls_dir=certs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    context = ssl.create_default_context(cafile=str(certs / tls.CERT))
    context.check_hostname = False
    url = f"https://127.0.0.1:{server.server_address[1]}/api/mcp"

    def post(message):
        req = urllib.request.Request(url, data=json.dumps(message).encode(), method="POST", headers={
            "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})  # fmt: skip
        return urllib.request.urlopen(req, context=context, timeout=10)

    try:
        with post({"jsonrpc": "2.0", "method": "notifications/initialized"}) as r:
            assert r.status == 202 and r.read() == b"" and r.headers.get("Content-Type") is None
        with post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) as r:
            assert r.headers["Content-Type"] == "application/json" and len(json.load(r)["result"]["tools"]) == len(mcp.TOOLS)
    finally:
        server.shutdown()


def test_the_log_holds_only_kias_own_tool_names(kia, capsys):
    # The tool name a client sends is matched against kia's list; only kia's own copy is logged.
    call_tool(kia, "kia_stack")
    rpc(kia, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "https://me:s3cret@example.com"}})
    logged = [line for line in capsys.readouterr().out.splitlines() if line.startswith("MCP:")]
    assert logged == ["MCP: kia_stack for 192.0.2.5"]


def test_the_password_and_the_token_are_slowed_down_apart(kia):
    # A right token mustn't reset the count of wrong passwords, and old tokens mustn't hold up
    # the right password (both shared one counter in the review's version).
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    statuses = []
    for _ in range(3):
        try:
            api._CLIENT.set("192.0.2.5")
            api.check_password(kia, "wrong", True)
        except api.ApiError as exc:
            statuses.append(exc.status)
        assert rpc(kia, ping)[0] == 200
    assert statuses == [401, 401, 429]
    for _ in range(3):
        rpc(kia, ping, token="stale", client="192.0.2.8")
    api._CLIENT.set("192.0.2.8")
    api.check_password(kia, "secret", True)  # no error: wrong tokens don't count against it


def test_a_damaged_file_or_a_failing_tool_still_answers(kia, monkeypatch):
    (kia.output_dir / "report.json").write_text("[1, 2, 3]")  # damaged into a list
    (kia.output_dir / "stack.json").write_text('{"services": "nope"}')
    for name in mcp.RUN:
        result = call_tool(kia, name, args_for(name))
        assert not result.get("isError"), (name, result)
    monkeypatch.setitem(mcp.RUN, "kia_stack", lambda *a: 1 / 0)
    assert call_tool(kia, "kia_stack") == {"content": [{"type": "text", "text": "kia_stack failed; kia's log has the details."}],
                                           "isError": True}  # fmt: skip


def test_broken_settings_dont_stop_a_client_connecting(kia):
    kia.config_file.write_text("[check\n")  # not valid TOML
    status, body = rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
    assert status == 200 and body["result"]["serverInfo"]["name"] == "kia"
    assert "kia's settings couldn't be read" in call_tool(kia, "kia_overview")["content"][0]["text"]


def test_ids_messages_and_the_bearer_word(kia, capsys):
    assert rpc(kia, {"jsonrpc": "2.0", "id": None, "method": "ping"})[0] == 400
    assert rpc(kia, {"jsonrpc": "2.0", "id": {"a": 1}, "method": "ping"})[0] == 400
    assert rpc(kia, {"jsonrpc": "2.0", "id": True, "method": "ping"})[0] == 400
    assert rpc(kia, {"jsonrpc": "2.0"})[0] == 400  # neither a request, a notification, nor a response
    assert rpc(kia, {"jsonrpc": "2.0", "id": 5, "result": {}}) == (202, None)  # a response
    assert rpc(kia, {"jsonrpc": "2.0", "id": "a", "method": "ping"})[1]["id"] == "a"
    h = {"Host": "tv.lan:8743", "Authorization": f"bearer {TOKEN}"}
    assert api.handle(kia, "POST", "/api/mcp", h, b'{"jsonrpc":"2.0","id":1,"method":"ping"}', True, "192.0.2.9")[0] == 200
    rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="nope", client="192.0.2.10")
    assert "MCP: refused a token from 192.0.2.10 (401)" in capsys.readouterr().out


def test_the_address_it_prints_is_one_the_certificate_covers(kia, capsys, monkeypatch):
    config = str(kia.config_file)
    monkeypatch.setenv("KIA_PUBLIC_URL", "http://192.168.1.20:8780")
    monkeypatch.setenv("KIA_TLS_NAMES", "my-server.lan")
    cli.main(["mcp-token", "-c", config])
    out = capsys.readouterr().out
    assert "https://my-server.lan:8743/api/mcp" in out and "192.168.1.20" not in out
    monkeypatch.delenv("KIA_PUBLIC_URL")
    monkeypatch.delenv("KIA_TLS_NAMES")
    cli.main(["mcp-token", "-c", config])
    out = capsys.readouterr().out
    assert "https://localhost:8743/api/mcp" in out and "works only on this computer" in out and "<" not in out


def test_backups_leave_the_token_out(kia, tmp_path):
    from kia import backup

    gateway = kia.state_dir.parent / "gateway"
    gateway.mkdir()
    (gateway / "kia-token").write_text("g" * 43 + "\n")
    (gateway / "devices").write_text("laptop " + "0" * 64 + "\n")  # hashes only: no use as keys
    names = backup.make(kia.state_dir.parent, tmp_path / "b.tar.gz")
    assert "state/mcp-token.txt" not in names and any(n.startswith("state/") for n in [*names, "state/x"])
    assert "gateway/kia-token" not in names and "gateway/devices" in names


# kia mcp: the same server over standard input and output, for an assistant on this computer


def test_stdio_answers_on_stdout_and_logs_on_stderr(kia):
    import subprocess
    import sys

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},  # no answer
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "kia_channel", "arguments": {"query": "CNN"}}},
    ]
    src = Path(api.__file__).resolve().parent.parent
    run = subprocess.run([sys.executable, "-m", "kia", "mcp", "-c", str(kia.config_file)], input="".join(json.dumps(m) + "\n" for m in messages),
                         capture_output=True, text=True, timeout=30, env={**__import__("os").environ, "PYTHONPATH": str(src)},
                         check=True)  # fmt: skip
    lines = run.stdout.splitlines()
    assert [json.loads(line)["id"] for line in lines] == [1, 2]  # one line each, only MCP messages
    assert json.loads(lines[1])["result"]["structuredContent"]["channels"][0]["subject"] == "CNN (CNN.us)"
    assert "MCP: kia_channel for stdio" in run.stderr


@pytest.mark.parametrize("assistant", mcp.ASSISTANTS)
def test_setup_for_each_assistant(assistant):
    text = mcp.setup_for(assistant, "https://my-server.lan:8743/api/mcp", "tok-123")
    assert "https://my-server.lan:8743/api/mcp" in text
    if assistant in ("codex", "cursor", "vscode"):
        assert "tok-123" in text  # set where the snippet says (an environment variable or VS Code's prompt)
    for block in [b for b in text.split("\n\n") if b.lstrip().startswith("{")]:
        json.loads(block)  # the JSON ones paste as they are


def test_mcp_token_for_an_assistant(kia, capsys):
    assert cli.main(["mcp-token", "-c", str(kia.config_file), "--for", "gemini"]) == 0
    out = capsys.readouterr().out
    assert "gemini mcp add --scope user --transport http" in out and "folders you trust" in out


# What's on, kia's docs, and the starter prompts


def test_whats_on_now_and_next(kia):
    from kia.config import load

    now = 1_800_000_000
    (kia.output_dir / "guide-preview.json").write_text(json.dumps({"generated": "2027-01-15T08:00:00+00:00", "channels": {
        "CNN.us": [[now - 600, now + 1200, "News Now"], [now + 1200, now + 3000, "The Lead"], [now + 3000, now + 4800, "Late"]]}}))  # fmt: skip
    answer = mcp._whats_on(load(kia.config_file), {"query": "cnn"}, None, now=now)
    [c] = answer["channels"]
    assert c["channel"] == "CNN" and c["on_now"]["title"] == "News Now" and [s["title"] for s in c["next"]] == ["The Lead", "Late"]
    later = mcp._whats_on(load(kia.config_file), {"query": "CNN.us"}, None, now=now + 5000)["channels"][0]
    assert later["on_now"] is None and later["next"] == [] and "No listings" in later["note"]
    assert "No channel" in mcp._whats_on(load(kia.config_file), {"query": "zzz"}, None, now=now)["note"]
    (kia.output_dir / "guide-preview.json").write_text('{"channels": {"CNN.us": [[1, 2], "x"]}}')  # damaged
    assert mcp._whats_on(load(kia.config_file), {"query": "cnn"}, None, now=now)["channels"][0]["on_now"] is None


def test_docs_search_finds_sections_with_links(kia):
    base = kia.config_file.parent
    (base / "docs").mkdir()
    (base / "docs" / "secure-page.md").write_text("# The secure Dashboard\n\nIntro.\n\n## The browser warning\n\nThe certificate is kia's own.\n"
                                                  "<picture>certificate certificate</picture>\n\n## The password\n\nWhere it is.\n")  # fmt: skip
    (base / "README.md").write_text("# kia\n\nA certificate is mentioned here once.\n")
    found = call_tool(kia, "kia_docs", {"query": "browser certificate warning"})["structuredContent"]["results"]
    assert found[0]["file"] == "docs/secure-page.md" and found[0]["link"] == "docs/secure-page.md#the-browser-warning"
    assert "picture" not in found[0]["text"]  # images and HTML left out
    assert call_tool(kia, "kia_docs", {"query": "zz"})["structuredContent"]["results"] == []


def test_starter_prompts(kia):
    status, body = rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
    assert status == 200 and body["result"]["capabilities"]["prompts"] == {"listChanged": False}
    prompts = rpc(kia, {"jsonrpc": "2.0", "id": 2, "method": "prompts/list"})[1]["result"]["prompts"]
    assert [p["name"] for p in prompts] == ["kia-checkup", "kia-channel"]
    got = rpc(kia, {"jsonrpc": "2.0", "id": 3, "method": "prompts/get", "params": {"name": "kia-channel", "arguments": {"channel": "CNN"}}})
    text = got[1]["result"]["messages"][0]["content"]["text"]
    assert "'CNN'" in text and "kia_whats_on" in text
    assert rpc(kia, {"jsonrpc": "2.0", "id": 4, "method": "prompts/get", "params": {"name": "kia-channel"}})[1]["error"]["code"] == -32602
    assert rpc(kia, {"jsonrpc": "2.0", "id": 5, "method": "prompts/get", "params": {"name": "nope"}})[1]["error"]["code"] == -32602


# Tools that ask (kia.asks)


def test_an_assistant_asks_and_only_the_password_makes_kia_act(kia, monkeypatch):
    tested, said = [], []
    monkeypatch.setattr(api, "test_channel", lambda cfg, body: tested.append(body) or {"ok": True, "height": 1080, "error": None})
    kia.alert = lambda event, title, message: said.append((event, title))
    (kia.output_dir / "status.json").write_text(json.dumps({"secure": {"url": "https://tv.lan:8743/"}}))
    asked = call_tool(kia, "kia_test_channel", {"query": "cnn"})["structuredContent"]
    assert asked["asked"] and asked["approve_at"] == "https://tv.lan:8743/#asks" and not tested
    assert asked["what"] == "Test the stream of CNN (CNN.us) now" and said == [("assistant_asks", "kia: an assistant asks: Test the stream of CNN (CNN.us) now")]
    again = call_tool(kia, "kia_test_channel", {"query": "CNN.us"})["structuredContent"]
    assert again["id"] == asked["id"] and "already" in again["next"] and len(said) == 1
    status = call_tool(kia, "kia_request_status", {"id": asked["id"]})["structuredContent"]
    assert status["state"] == "waiting" and status["who"] == "192.0.2.5"
    # The Dashboard: anyone can see it; approving takes the password, on the secure page.
    headers = {"Host": "tv.lan:8743"}
    listed = api.handle(kia, "GET", "/api/asks", headers, b"", False)[1]["asks"]
    assert [a["what"] for a in listed] == ["Test the stream of CNN (CNN.us) now"]
    body = json.dumps({"id": asked["id"]}).encode()
    assert api.handle(kia, "POST", "/api/asks/approve", headers, body, True)[0] == 401
    assert api.handle(kia, "POST", "/api/asks/approve", {**headers, "X-Kia-Password": "secret"}, body, False)[0] == 403
    assert not tested
    status, answer = api.handle(kia, "POST", "/api/asks/approve", {**headers, "X-Kia-Password": "secret"}, body, True)
    assert status == 200 and answer["ask"]["state"] == "done" and len(tested) == 1 and tested[0]["key"]
    assert api.handle(kia, "POST", "/api/asks/approve", {**headers, "X-Kia-Password": "secret"}, body, True)[0] == 409
    done = call_tool(kia, "kia_request_status", {"id": asked["id"]})["structuredContent"]
    assert done["state"] == "done" and done["result"]["height"] == 1080
    assert call_tool(kia, "kia_request_status", {"id": "0" * 16})["structuredContent"] == {"found": False}


def test_a_request_names_just_one_channel(kia):
    none = call_tool(kia, "kia_test_channel", {"query": "nothing like it"})["structuredContent"]
    assert not none["asked"] and none["why"] == "No channel matches that."
    assert not (kia.state_dir / "asks").exists()


def approve(kia, ask_id):
    headers = {"Host": "tv.lan:8743", "X-Kia-Password": "secret"}
    return api.handle(kia, "POST", "/api/asks/approve", headers, json.dumps({"id": ask_id}).encode(), True)


def finished(kia, ask_id):
    """The request once its thread is done: slow kinds run after Approve has answered."""
    for _ in range(200):
        r = api._shown(asks.get(kia.state_dir, ask_id))  # not the tool: 60 calls a minute
        if r["state"] != "running":
            return r
        threading.Event().wait(0.01)  # not time.sleep: the tests stand in for it
    pytest.fail("still running")


def test_the_slow_kinds_start_on_approve_and_answer_later(kia, monkeypatch):
    ran = []
    monkeypatch.setattr(api, "_refresh_guide", lambda cfg: ran.append("guide") or {"ok": True, "summary": "Refreshed 3 of 3 quick guide sources"})
    asked = call_tool(kia, "kia_refresh_guide")["structuredContent"]
    assert asked["asked"] and asked["what"] == "Refresh the guide now from the quick sources, and rebuild it" and not ran
    status, answer = approve(kia, asked["id"])
    assert status == 200 and answer["ask"]["state"] in ("running", "done")
    done = finished(kia, asked["id"])
    assert done["state"] == "done" and done["result"]["summary"].startswith("Refreshed") and ran == ["guide"]
    # With an upstream kia, a guide refresh copies that one's again, and says so.
    kia.config_file.write_text('[upstream]\nurl = "http://192.0.2.7:8742/guide.xml"\n')
    assert call_tool(kia, "kia_refresh_guide")["structuredContent"]["what"].startswith("Copy the guide from the upstream")


def test_add_to_plex_needs_a_plex(kia, monkeypatch):
    monkeypatch.delenv("KIA_PLEX_URL", raising=False)
    nope = call_tool(kia, "kia_add_to_plex")["structuredContent"]
    assert not nope["asked"] and "KIA_PLEX_URL" in nope["why"] and not (kia.state_dir / "asks").exists()
    monkeypatch.setenv("KIA_PLEX_URL", "http://plex:32400")
    monkeypatch.setattr(api, "_run_plex_tuners", lambda cfg: api.PLEX_TUNERS.update({"ok": True, "output": "Added 2 tuners"}))
    asked = call_tool(kia, "kia_add_to_plex")["structuredContent"]
    approve(kia, asked["id"])
    done = finished(kia, asked["id"])
    assert done["result"] == {"ok": True, "summary": "kia's channels are in Plex.", "output": "Added 2 tuners"}
    assert not api.PLEX_TUNERS["running"] or api.PLEX_TUNERS.get("ok")  # the Stack page's button sees it too


def test_check_stack_only_checks_and_only_known_parts(kia, monkeypatch):
    from kia import stack

    monkeypatch.delenv("KIA_COMPOSE_PROFILES", raising=False)
    assert "without Docker" in call_tool(kia, "kia_check_stack", {"parts": []})["structuredContent"]["why"]
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "")
    service = kia.config_file.parent / stack.SERVICE_DIR
    assert "isn't running" in call_tool(kia, "kia_check_stack", {"parts": []})["structuredContent"]["why"]
    service.mkdir()
    (service / stack.CONTAINERS).write_text("kia\trunning\thealthy\tUp\n")  # the service is there
    for bad in (["plex", "nope"], "plex", ["plex"] * 11):
        _, body = rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "kia_check_stack", "arguments": {"parts": bad}}})
        assert body["result"]["isError"], bad
    asked = call_tool(kia, "kia_check_stack", {"parts": ["jellyfin", "plex", "plex"]})["structuredContent"]
    assert asked["what"] == "Check the stack with plex, jellyfin (it only checks; nothing changes)"
    assert not (kia.state_dir / stack.REQUEST).exists()  # nothing for the service until it's approved

    def service_answers(seconds):  # the kia-stack service takes the request and answers
        rid = stack._request_id(kia.state_dir / stack.REQUEST)
        (service / stack.RESULT).write_text(f"id={rid}\naction=check\nstate=finished\nexit=0\n\nAll good\n")

    monkeypatch.setattr(api.time, "sleep", service_answers)
    approve(kia, asked["id"])
    done = finished(kia, asked["id"])
    assert done["result"]["ok"] and done["result"]["output"] == "All good"
    assert "action=check\nstack=plex,jellyfin\n" in (kia.state_dir / stack.REQUEST).read_text()  # never apply


def test_the_settings_limits_are_used(kia):
    kia.config_file.write_text("[asks]\nexpires_minutes = 90\nwaiting = 1\n")
    asked = call_tool(kia, "kia_test_channel", {"query": "CNN"})["structuredContent"]
    assert call_tool(kia, "kia_request_status", {"id": asked["id"]})["structuredContent"]["minutes_left"] in (89, 90)
    full = call_tool(kia, "kia_refresh_guide")["structuredContent"]
    assert not full["asked"] and "1 request is already waiting" in full["why"]


def test_what_a_guide_refresh_says(kia, monkeypatch):
    import subprocess

    def run(cmd, **kw):
        assert cmd[-3:] == ["refresh-guide", "-c", str(kia.config_file)]
        return subprocess.CompletedProcess(cmd, code, "", said)

    monkeypatch.setattr(api.subprocess, "run", run)
    code, said = 0, "Downloading i.mjh.nz\nRefreshed 3 of 4 quick guide sources\nWrote output/guide.xml\n"
    assert api._refresh_guide(kia)["summary"] == "Refreshed 3 of 4 quick guide sources, and the guide was rebuilt."
    code, said = 75, "A refresh is already running (the nightly refresh). Try again when it's done.\n"
    done = api._refresh_guide(kia)
    assert not done["ok"] and done["summary"] == "The guide refresh didn't finish: " + said.strip()


def test_a_stack_service_that_never_answers(kia, monkeypatch):
    from kia import stack

    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "")
    monkeypatch.setattr(api, "STACK_CHECK_UP_TO", 0)
    assert "didn't answer" in api._check_stack(kia, [])["summary"]
    service = kia.config_file.parent / stack.SERVICE_DIR
    service.mkdir()

    def refused(seconds):  # the service takes the request, and refuses it
        rid = stack._request_id(kia.state_dir / stack.REQUEST)
        (kia.state_dir / stack.REQUEST).unlink()
        (service / stack.RESULT).write_text(f"id={rid}\naction=check\nstate=refused\nexit=2\n\nAn old request\n")

    (kia.state_dir / stack.REQUEST).unlink()  # the first one, which nobody took
    monkeypatch.setattr(api, "STACK_CHECK_UP_TO", 60)
    monkeypatch.setattr(api.time, "sleep", refused)
    done = api._check_stack(kia, [])
    assert done["ok"] is False and done["output"] == "An old request"


def test_a_request_you_let_run_by_itself(kia, monkeypatch):
    tested, said = [], []
    monkeypatch.setattr(api, "test_channel", lambda cfg, body: tested.append(body) or {"ok": True, "height": 720, "error": None})
    kia.alert = lambda event, title, message: said.append(title)
    kia.config_file.write_text('[asks]\nauto_approve = ["test_channel"]\n')
    ran = call_tool(kia, "kia_test_channel", {"query": "CNN"})["structuredContent"]
    assert ran["state"] == "done" and ran["result"]["height"] == 720 and ran["approved"] == "automatically: kia.toml's auto_approve"
    assert len(tested) == 1 and said == ["kia: an assistant's request ran by itself: Test the stream of CNN (CNN.us) now"]
    listed = api.handle(kia, "GET", "/api/asks", {"Host": "tv.lan:8743"}, b"", False)[1]["asks"]
    assert listed[0]["auto"] == "kia.toml's auto_approve"  # on the Dashboard too
    # Only for the devices named: the same request from kia's own token waits.
    kia.config_file.write_text('[asks]\nauto_approve = ["test_channel"]\nauto_devices = ["laptop"]\n')
    waits = call_tool(kia, "kia_test_channel", {"query": "CNN"})["structuredContent"]
    assert waits["state"] == "waiting" and "approved" not in waits and len(tested) == 1


def test_the_device_comes_only_with_the_gateways_token(kia, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "test_channel", lambda cfg, body: {"ok": True})
    kia.config_file.write_text('[asks]\nauto_approve = ["test_channel"]\nauto_devices = ["laptop"]\n')
    gateway_token = "g" * 43
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway" / "kia-token").write_text(gateway_token + "\n")
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "gateway")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "kia_test_channel", "arguments": {"query": "CNN"}}}
    _, answer = rpc(kia, body, headers={"X-Kia-Device": "laptop"})  # kia's own token: anyone could send that name
    first = answer["result"]["structuredContent"]
    assert first["state"] == "waiting"
    asks.take(kia.state_dir, first["id"], False)  # declined, so the next one is a new request
    _, answer = rpc(kia, body, token=gateway_token, headers={"X-Kia-Device": "laptop"})
    assert answer["result"]["structuredContent"]["state"] == "done"


def test_approve_and_allow_for_an_hour(kia, monkeypatch):
    tested = []
    monkeypatch.setattr(api, "test_channel", lambda cfg, body: tested.append(body) or {"ok": True})
    headers = {"Host": "tv.lan:8743", "X-Kia-Password": "secret"}
    first = call_tool(kia, "kia_test_channel", {"query": "CNN"})["structuredContent"]
    status, answer = api.handle(kia, "POST", "/api/asks/approve", headers, json.dumps({"id": first["id"], "allow_hour": True}).encode(), True)
    assert status == 200 and answer["ask"]["state"] == "done"
    allowed = api.handle(kia, "GET", "/api/asks", headers, b"", False)[1]["allowed"]
    assert allowed == [{"kind": "test_channel", "who": "192.0.2.5", "minutes_left": 60}]
    again = call_tool(kia, "kia_test_channel", {"query": "CNN"})["structuredContent"]
    assert again["approved"] == "automatically: allowed for an hour on the Dashboard" and len(tested) == 2
    other = rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "kia_test_channel", "arguments": {"query": "CNN"}}}, client="192.0.2.99")[1]  # fmt: skip
    assert other["result"]["structuredContent"]["state"] == "waiting"  # another caller still asks
    assert api.handle(kia, "POST", "/api/asks/stop-allowing", {"Host": "tv.lan:8743"}, b"{}", True)[0] == 401
    assert api.handle(kia, "POST", "/api/asks/stop-allowing", headers, b"{}", True)[1]["allowed"] == []
    # Plex's tuners always ask: no hour for them.
    monkeypatch.setenv("KIA_PLEX_URL", "http://plex:32400")
    plex = call_tool(kia, "kia_add_to_plex")["structuredContent"]
    status, answer = api.handle(kia, "POST", "/api/asks/approve", headers, json.dumps({"id": plex["id"], "allow_hour": True}).encode(), True)
    assert status == 409 and "always asks you" in answer["error"]
    assert asks.get(kia.state_dir, plex["id"])["state"] == "waiting"  # not run either


@pytest.mark.parametrize("who", ["127.0.0.1", "::1", "192.0.2.1", "stdio", ""])
def test_no_hour_for_an_address_many_share(kia, monkeypatch, who):
    monkeypatch.setattr(api, "test_channel", lambda cfg, body: {"ok": True})
    monkeypatch.setattr(api, "_network_gateways", lambda: {"192.0.2.1"})
    r = asks.ask(kia.state_dir, "test_channel", {"key": "CNN.us"}, "Test CNN", who)
    shown = api.get_asks(kia)["asks"][0]
    assert not shown["can_allow"] and shown["shared_caller"]
    headers = {"Host": "tv.lan:8743", "X-Kia-Password": "secret"}
    status, answer = api.handle(kia, "POST", "/api/asks/approve", headers, json.dumps({"id": r["id"], "allow_hour": True}).encode(), True)
    assert status == 409 and "can't tell assistants apart" in answer["error"]
    assert asks.get(kia.state_dir, r["id"])["state"] == "waiting" and api.get_asks(kia)["allowed"] == []
    assert api.handle(kia, "POST", "/api/asks/approve", headers, json.dumps({"id": r["id"]}).encode(), True)[0] == 200  # alone, fine


def test_under_docker_desktop_every_address_is_shared_but_a_gateway_device_isnt(kia, monkeypatch):
    monkeypatch.setattr(api, "test_channel", lambda cfg, body: {"ok": True})
    assert not api.shared_address("192.0.2.5")
    monkeypatch.setattr(api, "_docker_desktop", lambda: True)  # conftest: False
    assert api.shared_address("192.0.2.5")
    r = asks.ask(kia.state_dir, "test_channel", {"key": "CNN.us"}, "Test CNN", "laptop (through the gateway)", device="laptop")
    assert api.get_asks(kia)["asks"][0]["can_allow"]
    headers = {"Host": "tv.lan:8743", "X-Kia-Password": "secret"}
    assert api.handle(kia, "POST", "/api/asks/approve", headers, json.dumps({"id": r["id"], "allow_hour": True}).encode(), True)[0] == 200
    assert api.get_asks(kia)["allowed"][0]["who"] == "laptop (through the gateway)"


def test_network_gateways_from_the_route_table(tmp_path: Path, monkeypatch):
    route = tmp_path / "route"
    route.write_text("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
                     "eth0\t00000000\t010200C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
                     "eth0\t000200C0\t00000000\t0001\t0\t0\t0\t80FFFFFF\t0\t0\t0\n"
                     "eth1\t800200C0\t00000000\t0001\t0\t0\t0\t80FFFFFF\t0\t0\t0\n")  # fmt: skip
    real = Path

    monkeypatch.setattr(api, "Path", lambda p: route if p == "/proc/net/route" else real(p))
    assert api._network_gateways() == {"192.0.2.1", "192.0.2.129"}  # two /25 networks
