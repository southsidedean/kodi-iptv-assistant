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
    asks.ask(tmp_path, "other", {}, "other", now=T, waiting=2)
    monkeypatch.setitem(asks.KINDS, "third", "A third")
    with pytest.raises(asks.AskError, match="2 requests are already waiting"):
        asks.ask(tmp_path, "third", {}, "third", now=T, waiting=2)


@pytest.mark.parametrize("ask_id", ["", "../x", "0123456789abcdeG", "0" * 17, "0" * 15])
def test_only_kias_own_ids(tmp_path: Path, ask_id):
    assert asks.get(tmp_path, ask_id) is None
    with pytest.raises(asks.AskError, match="no such request"):
        asks.answer(tmp_path, ask_id, True, lambda *x: pytest.fail("ran"))


def test_a_damaged_or_planted_file_is_never_a_request(tmp_path: Path):
    folder = tmp_path / asks.FOLDER
    folder.mkdir()
    good = {"id": "0123456789abcdef", "kind": "test_channel", "args": {"key": "k"}, "what": "w", "state": "waiting",
            "expires": T + 600, "who": "192.0.2.5"}  # fmt: skip
    (folder / "0123456789abcdef.json").write_text(json.dumps({**good, "state": "approved"}))  # not a state kia uses
    (folder / "fedcba9876543210.json").write_text(json.dumps({**good, "kind": "apply_stack"}))  # not a kind there is
    (folder / "aaaaaaaaaaaaaaaa.json").write_text(json.dumps(good))  # its name isn't its id
    (folder / "bbbbbbbbbbbbbbbb.json").write_text("[1, 2]")
    (folder / "cccccccccccccccc.json").write_text(json.dumps({**good, "id": "cccccccccccccccc", "state": "done", "answered_at": "yesterday"}))
    (folder / "dddddddddddddddd.json").write_text(json.dumps({**good, "id": "dddddddddddddddd", "who": None}))
    assert asks.listing(tmp_path, now=T) == [] and not list(folder.glob("*.json"))
    (folder / "eeeeeeeeeeeeeeee.json").write_text(json.dumps({**good, "id": "eeeeeeeeeeeeeeee"}))
    assert [r["id"] for r in asks.listing(tmp_path, now=T)] == ["eeeeeeeeeeeeeeee"]  # the good one alone still counts


def test_a_request_kia_restarted_under_is_failed_not_running_for_ever(tmp_path: Path):
    r = asks.ask(tmp_path, "refresh_guide", {}, "Refresh the guide", now=T)
    assert asks.take(tmp_path, r["id"], True, now=T)["state"] == "running"  # then kia restarted
    assert asks.get(tmp_path, r["id"], now=T + 60)["state"] == "running"
    gone = asks.get(tmp_path, r["id"], now=T + asks.RUNNING_UP_TO + 1)
    assert gone["state"] == "failed" and "restarted" in gone["error"]


def test_how_long_a_request_waits_is_the_callers(tmp_path: Path):
    r = asks.ask(tmp_path, "test_channel", {"key": "k"}, "one", now=T, expires=3600)
    assert asks.get(tmp_path, r["id"], now=T + asks.EXPIRES + 1)["state"] == "waiting"
    assert asks.get(tmp_path, r["id"], now=T + 3601)["state"] == "expired"


def test_what_runs_without_you(tmp_path: Path):
    def why(kind="test_channel", who="192.0.2.5", device="", kinds=(), devices=(), per_hour=20, now=T):
        return asks.why_auto(tmp_path, kind, who, device, kinds, devices, per_hour, now)

    assert why() == ""  # off until you choose
    assert why(kinds=("test_channel",)) == "kia.toml's auto_approve"
    assert why(kind="refresh_guide", kinds=("test_channel",)) == ""
    assert why(kind="add_to_plex", kinds=("add_to_plex",)) == ""  # never, even if a setting slipped through
    assert why(kinds=("test_channel",), devices=("laptop",)) == ""  # not through the gateway
    assert why(kinds=("test_channel",), devices=("laptop",), device="phone") == ""
    assert why(kinds=("test_channel",), devices=("laptop",), device="laptop") == "kia.toml's auto_approve for laptop"


def test_at_most_so_many_an_hour(tmp_path: Path):
    for i in range(3):
        r = asks.ask(tmp_path, "test_channel", {"key": f"k{i}"}, "a test", now=T + i)
        asks.answer(tmp_path, r["id"], True, lambda *a: {}, now=T + i)
    assert asks.why_auto(tmp_path, "test_channel", "x", "", ("test_channel",), (), 3, now=T + 10) != ""  # none were automatic
    for i in range(3):
        r = asks.ask(tmp_path, "test_channel", {"key": f"a{i}"}, "a test", now=T + 20 + i)
        asks.finish(tmp_path, asks.take(tmp_path, r["id"], True, now=T + 20 + i, auto="kia.toml's auto_approve"), lambda *a: {},
                    now=T + 20 + i)  # fmt: skip
    assert asks.why_auto(tmp_path, "test_channel", "x", "", ("test_channel",), (), 3, now=T + 30) == ""  # the fourth waits
    assert asks.why_auto(tmp_path, "refresh_guide", "x", "", ("refresh_guide",), (), 3, now=T + 30) != ""  # a kind each
    assert asks.why_auto(tmp_path, "test_channel", "x", "", ("test_channel",), (), 3, now=T + 3700) != ""  # an hour on


def test_allowed_for_an_hour(tmp_path: Path):
    with pytest.raises(asks.AskError, match="always asks you"):
        asks.allow(tmp_path, "add_to_plex", "laptop (through the gateway)", now=T)
    asks.allow(tmp_path, "test_channel", "laptop (through the gateway)", now=T)
    asks.allow(tmp_path, "test_channel", "laptop (through the gateway)", now=T + 60)  # again: one entry, a new hour
    assert asks.allowed(tmp_path, now=T + 60) == [{"kind": "test_channel", "who": "laptop (through the gateway)", "minutes_left": 60}]
    why = asks.why_auto(tmp_path, "test_channel", "laptop (through the gateway)", "laptop", now=T + 120)
    assert why == "allowed for an hour on the Dashboard"
    assert asks.why_auto(tmp_path, "test_channel", "phone (through the gateway)", "phone", now=T + 120) == ""  # that caller only
    assert asks.why_auto(tmp_path, "refresh_guide", "laptop (through the gateway)", "laptop", now=T + 120) == ""  # that kind only
    assert asks.why_auto(tmp_path, "test_channel", "laptop (through the gateway)", "laptop", now=T + 60 + 3601) == ""
    asks.allow(tmp_path, "check_stack", "stdio", now=T)
    asks.stop_allowing(tmp_path)
    assert asks.allowed(tmp_path, now=T) == [] and not (tmp_path / asks.ALLOWED).exists()


def test_a_planted_allowance_counts_for_nothing(tmp_path: Path):
    # state/ is kia's to write, and a taken-over kia container's too: only an entry kia could
    # have written counts, and none for longer than an hour.
    entries = [{"kind": "add_to_plex", "who": "x", "until": T + 60}, {"kind": "test_channel", "who": "x", "until": T + 86400},
               {"kind": "test_channel", "who": "", "until": T + 60}, {"kind": "test_channel", "who": "x" * 101, "until": T + 60},
               {"kind": "test_channel", "who": "x", "until": "later"}, "test_channel"]  # fmt: skip
    (tmp_path / asks.ALLOWED).write_text(json.dumps({"allowed": entries}))
    assert asks.allowed(tmp_path, now=T) == [] and asks.why_auto(tmp_path, "test_channel", "x", "", now=T) == ""
    (tmp_path / asks.ALLOWED).write_text("[1, 2]")
    assert asks.allowed(tmp_path, now=T) == []


def test_at_most_one_of_each_kind_waits():
    from kia.config import Config

    # [asks] waiting can't usefully be more than the kinds there are: one of each waits.
    assert asks.WAITING == len(asks.KINDS) == Config.__dataclass_fields__["asks_waiting"].default
