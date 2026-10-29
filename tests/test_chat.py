import io
import json
import urllib.error

import pytest

from kia import chat


def text_answer(text, usage=None):
    return {"role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
            "usage": usage or {"input_tokens": 10, "output_tokens": 5}}


def tool_call(tool_id, name, args, usage=None):
    return {"role": "assistant", "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}],
            "usage": usage or {"input_tokens": 20, "output_tokens": 8}}


# The loop


def test_a_plain_answer_uses_no_tools():
    out = chat.reply([{"role": "user", "content": "is channel 400 ok?"}],
                     post=lambda body: text_answer("Channel 400 is working fine."),
                     run_tool=lambda n, a: pytest.fail("should not run a tool"))
    assert out["reply"] == "Channel 400 is working fine."
    assert out["tools_used"] == []
    assert out["rounds"] == 1
    assert out["usage"]["output_tokens"] == 5


def test_a_tool_round_then_an_answer():
    seen, sent = [], []
    posts = [tool_call("tu_1", "kia_whats_on", {"query": "ESPN"}),
             text_answer("The game is on ESPN now.")]

    def run_tool(name, args):
        seen.append((name, args))
        return {"on_now": "the game"}

    def post(body):
        sent.append(body)
        return posts.pop(0)

    out = chat.reply([{"role": "user", "content": "what's on ESPN?"}], post=post, run_tool=run_tool)
    assert out["reply"] == "The game is on ESPN now."
    assert out["tools_used"] == ["kia_whats_on"]
    assert seen == [("kia_whats_on", {"query": "ESPN"})]
    assert out["rounds"] == 2
    # Tokens add up across both rounds.
    assert out["usage"] == {"input_tokens": 30, "output_tokens": 13,
                            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    # The second request carried the model's tool-use turn (preserved) and the tool result.
    second = sent[1]["messages"]
    assert second[-2]["role"] == "assistant"
    assert second[-1]["role"] == "user"
    result = second[-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1"
    assert json.loads(result["content"]) == {"on_now": "the game"}


def test_the_body_has_caching_and_adaptive_thinking():
    captured = {}

    def post(body):
        captured.update(body)
        return text_answer("ok")

    chat.reply([{"role": "user", "content": "hi"}], post=post, run_tool=lambda n, a: {})
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert captured["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert captured["thinking"] == {"type": "adaptive"}
    assert captured["model"] == chat.DEFAULT_MODEL
    names = {t["name"] for t in captured["tools"]}
    assert "kia_whats_on" in names  # a read-only tool is offered
    assert "kia_add_to_plex" not in names  # a tool that acts is not


def test_thinking_and_caching_can_be_turned_off():
    captured = {}

    def post(body):
        captured.update(body)
        return text_answer("ok")

    chat.reply([{"role": "user", "content": "hi"}], post=post, run_tool=lambda n, a: {},
               thinking=False, cache=False)
    assert "thinking" not in captured
    assert "cache_control" not in captured["system"][0]
    assert all("cache_control" not in t for t in captured["tools"])


def test_the_loop_stops_after_max_rounds():
    calls = {"n": 0}

    def post(body):
        calls["n"] += 1
        return tool_call(f"tu_{calls['n']}", "kia_overview", {})

    out = chat.reply([{"role": "user", "content": "hi"}], post=post,
                     run_tool=lambda n, a: {"ok": True}, max_rounds=3)
    assert calls["n"] == 3
    assert out["stopped"] == "max_rounds"
    assert out["rounds"] == 3
    assert out["tools_used"] == ["kia_overview", "kia_overview", "kia_overview"]


def test_a_reply_in_the_wrong_shape_is_an_error():
    with pytest.raises(chat.ChatError):
        chat.reply([{"role": "user", "content": "hi"}], post=lambda b: {"content": "not a list"},
                   run_tool=lambda n, a: {})


def test_a_read_only_answer_has_no_requests():
    out = chat.reply([{"role": "user", "content": "hi"}],
                     post=lambda b: text_answer("hello"), run_tool=lambda n, a: {})
    assert out["requests"] == []


# Actions (KIA_CHAT_ACTIONS)


def test_actions_offer_the_acting_tools_and_their_prompt():
    captured = {}

    def post(body):
        captured.update(body)
        return text_answer("ok")

    chat.reply([{"role": "user", "content": "refresh the guide"}], post=post,
               run_tool=lambda n, a: {}, actions=True)
    names = {t["name"] for t in captured["tools"]}
    assert {"kia_refresh_guide", "kia_test_channel", "kia_add_to_plex", "kia_check_stack"} <= names
    assert "kia_whats_on" in names  # the read-only tools are still offered
    assert captured["system"][0]["text"] == chat.SYSTEM_WITH_ACTIONS


def test_without_actions_the_acting_tools_and_prompt_stay_out():
    captured = {}

    def post(body):
        captured.update(body)
        return text_answer("ok")

    chat.reply([{"role": "user", "content": "hi"}], post=post, run_tool=lambda n, a: {})
    names = {t["name"] for t in captured["tools"]}
    assert not ({"kia_refresh_guide", "kia_add_to_plex"} & names)
    assert captured["system"][0]["text"] == chat.SYSTEM


def test_requests_from_acting_tools_are_collected():
    # When an acting tool leaves a request, the loop pulls its id, kia's words, state, and the
    # Dashboard link out of the tool result so the page can show the member where to approve it.
    ask_result = {"asked": True, "id": "ab12cd", "what": "Refresh the guide", "state": "waiting",
                  "approve_at": "https://my-server.lan:8743/#asks", "next": "Ask them to approve it."}
    posts = [tool_call("tu_1", "kia_refresh_guide", {}),
             text_answer("I asked kia to refresh the guide. Approve it on the Dashboard.")]
    out = chat.reply([{"role": "user", "content": "refresh the guide"}],
                     post=lambda b: posts.pop(0), run_tool=lambda n, a: ask_result, actions=True)
    assert out["tools_used"] == ["kia_refresh_guide"]
    assert out["requests"] == [{"id": "ab12cd", "what": "Refresh the guide", "state": "waiting",
                                "approve_at": "https://my-server.lan:8743/#asks"}]


# Telling a budget error apart


def test_is_budget_error():
    over = json.dumps({"error": {"message": "budget exceeded", "type": "rate_limit_error",
                                 "code": "budget_exceeded"}}).encode()
    assert chat.is_budget_error(429, over)
    assert not chat.is_budget_error(429, b'{"error":{"code":"rate_limited"}}')
    assert not chat.is_budget_error(200, over)
    assert not chat.is_budget_error(429, b"not json")


# The gateway transport


class FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_gateway_posts_the_body_and_parses_the_reply(monkeypatch):
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent["headers"] = dict(req.headers)
        sent["body"] = json.loads(req.data)
        sent["url"] = req.full_url
        return FakeResp(json.dumps(text_answer("hi")).encode())

    monkeypatch.setattr(chat.urllib.request, "urlopen", fake_urlopen)
    gw = chat.Gateway("http://gateway:4000/v1/messages", "sk-abc")
    answer = gw.post({"model": "claude-sonnet-5-5", "messages": []})
    assert answer["content"][0]["text"] == "hi"
    assert sent["url"] == "http://gateway:4000/v1/messages"
    # urllib capitalizes header names.
    assert sent["headers"]["Authorization"] == "Bearer sk-abc"
    assert sent["headers"]["Anthropic-version"] == chat.ANTHROPIC_VERSION


def _raise_http(status, payload):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(payload))

    return fake_urlopen


def test_gateway_over_budget_raises_budget_exceeded(monkeypatch):
    over = json.dumps({"error": {"code": "budget_exceeded"}}).encode()
    monkeypatch.setattr(chat.urllib.request, "urlopen", _raise_http(429, over))
    gw = chat.Gateway("http://gateway/v1/messages", "sk-abc")
    with pytest.raises(chat.BudgetExceeded):
        gw.post({})


def test_gateway_other_errors_are_plain_chat_errors(monkeypatch):
    monkeypatch.setattr(chat.urllib.request, "urlopen", _raise_http(401, b"{}"))
    gw = chat.Gateway("http://gateway/v1/messages", "sk-abc")
    with pytest.raises(chat.ChatError) as exc:
        gw.post({})
    assert "sign in" in str(exc.value)  # a plain message, no key or model named
    assert not isinstance(exc.value, chat.BudgetExceeded)
