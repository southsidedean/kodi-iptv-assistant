"""kia.asks: requests from AI assistants, which run only once the person approves them."""

import json
from pathlib import Path

import pytest

from kia import asks

T = 1_000_000.0  # a clock for the tests


def test_a_request_waits_and_runs_once_approved(tmp_path: Path):
    ran = []
    r = asks.ask(tmp_path, "test_channel", {"key": "k1"}, "Test the stream of CNN (CNN.us) now", "laptop (through the gateway)", now=T)
    assert r["state"] == "waiting" and asks.ID.fullmatch(r["id"]) and not ran
    assert asks.get(tmp_path, r["id"], now=T)["who"] == "laptop (through the gateway)"
    done = asks.answer(tmp_path, r["id"], True, lambda kind, args: ran.append((kind, args)) or {"ok": True}, now=T + 60)
    assert done["state"] == "done" and done["result"] == {"ok": True} and ran == [("test_channel", {"key": "k1"})]
    with pytest.raises(asks.AskError, match="done already"):  # a second click doesn't run it again
        asks.answer(tmp_path, r["id"], True, lambda *a: ran.append(a) or {}, now=T + 61)
    assert len(ran) == 1


def test_declined_failed_and_expired(tmp_path: Path):
    a = asks.ask(tmp_path, "test_channel", {"key": "k1"}, "one", now=T)
    assert asks.answer(tmp_path, a["id"], False, lambda *x: pytest.fail("ran"), now=T)["state"] == "declined"
    b = asks.ask(tmp_path, "test_channel", {"key": "k2"}, "two", now=T)

    def broken(kind, args):
        raise ValueError("That channel isn't in the playlist.")

    failed = asks.answer(tmp_path, b["id"], True, broken, now=T)
    assert failed["state"] == "failed" and failed["error"] == "That channel isn't in the playlist."
    c = asks.ask(tmp_path, "test_channel", {"key": "k3"}, "three", now=T)
    assert asks.get(tmp_path, c["id"], now=T + asks.EXPIRES + 1)["state"] == "expired"  # nobody answered
    with pytest.raises(asks.AskError, match="expired already"):
        asks.answer(tmp_path, c["id"], True, lambda *x: pytest.fail("ran"), now=T + asks.EXPIRES + 2)
    # A day after its answer, a request is gone.
    assert asks.listing(tmp_path, now=T + asks.EXPIRES + asks.KEEP + 3) == []


def test_an_assistant_cant_pile_them_up(tmp_path: Path, monkeypatch):
    first = asks.ask(tmp_path, "test_channel", {"key": "k1"}, "one", now=T)
    again = asks.ask(tmp_path, "test_channel", {"key": "k2"}, "two", now=T)
    assert again["id"] == first["id"] and again["already"]  # one of each kind waiting
    monkeypatch.setitem(asks.KINDS, "other", "Something else")
    monkeypatch.setattr(asks, "WAITING", 2)
    asks.ask(tmp_path, "other", {}, "other", now=T)
    monkeypatch.setitem(asks.KINDS, "third", "A third")
    with pytest.raises(asks.AskError, match="2 requests are already waiting"):
        asks.ask(tmp_path, "third", {}, "third", now=T)


@pytest.mark.parametrize("ask_id", ["", "../x", "0123456789abcdeG", "0" * 17, "0" * 15])
def test_only_kias_own_ids(tmp_path: Path, ask_id):
    assert asks.get(tmp_path, ask_id) is None
    with pytest.raises(asks.AskError, match="no such request"):
        asks.answer(tmp_path, ask_id, True, lambda *x: pytest.fail("ran"))


def test_a_damaged_or_planted_file_is_never_a_request(tmp_path: Path):
    folder = tmp_path / asks.FOLDER
    folder.mkdir()
    good = {"id": "0123456789abcdef", "kind": "test_channel", "args": {"key": "k"}, "what": "w", "state": "waiting",
            "expires": T + 600}  # fmt: skip
    (folder / "0123456789abcdef.json").write_text(json.dumps({**good, "state": "approved"}))  # not a state kia uses
    (folder / "fedcba9876543210.json").write_text(json.dumps({**good, "kind": "apply_stack"}))  # not a kind there is
    (folder / "aaaaaaaaaaaaaaaa.json").write_text(json.dumps(good))  # its name isn't its id
    (folder / "bbbbbbbbbbbbbbbb.json").write_text("[1, 2]")
    assert asks.listing(tmp_path, now=T) == [] and not list(folder.glob("*.json"))
