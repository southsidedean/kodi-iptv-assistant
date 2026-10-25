import json
import shutil
import ssl
import threading
import urllib.request
from pathlib import Path

import pytest

from kia import api, auth, cli, mcp, serve, tls
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


# The protocol


def test_initialize_and_version_negotiation(kia):
    for asked, got in (("2025-06-18", "2025-06-18"), ("1999-01-01", mcp.VERSIONS[0])):
        status, body = rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": asked, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})  # fmt: skip
        result = body["result"]
        assert status == 200 and result["protocolVersion"] == got and result["capabilities"] == {"tools": {"listChanged": False}}
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


def test_every_tool_only_reads_and_answers(kia):
    status, body = rpc(kia, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert status == 200
    tools = body["result"]["tools"]
    assert {t["name"] for t in tools} == set(mcp.RUN)
    assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in tools)
    before = sorted(p.name for p in kia.state_dir.parent.rglob("*"))
    for t in tools:
        args = {"query": "CNN"} if "query" in t["inputSchema"].get("properties", {}) else {}
        result = call_tool(kia, t["name"], args)
        assert result.get("isError") is not True, (t["name"], result)
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert sorted(p.name for p in kia.state_dir.parent.rglob("*")) == before  # nothing written
    assert call_tool(kia, "kia_channel", {"query": "CNN"})["structuredContent"]["channels"][0]["subject"] == "CNN (CNN.us)"
    assert call_tool(kia, "kia_overview")["structuredContent"]["status"]["result"] == "ok"
    assert call_tool(kia, "kia_find_channels", {"query": "cnn"})["structuredContent"]["channels"][0]["id"] == "CNN.us"


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
    answers = "".join(json.dumps(call_tool(kia, n, {"query": "CNN"} if n in ("kia_channel", "kia_find_channels") else {}), ensure_ascii=False)
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
        result = call_tool(kia, name, {"query": "CNN"} if name in ("kia_channel", "kia_find_channels") else {})
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

    names = backup.make(kia.state_dir.parent, tmp_path / "b.tar.gz")
    assert "state/mcp-token.txt" not in names and any(n.startswith("state/") for n in [*names, "state/x"])


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
