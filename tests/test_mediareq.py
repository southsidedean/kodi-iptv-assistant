"""The web-to-media request channel (kia.mediareq): web leaves a request, media claims it and
answers. This is the transport only; what a kind does is the caller's handler, so these check
the protocol: strict checks, a single slot, claim exactly once, and freeing the slot."""

import os
import stat
import time

import pytest

from kia import mediareq


def dirs(tmp_path):
    return tmp_path / "state", tmp_path / "state" / "media-service"


def test_a_refresh_guide_request_is_left_taken_and_answered(tmp_path):
    state, service = dirs(tmp_path)
    rid = mediareq.ask(state, service, "refresh-guide")
    assert mediareq.waiting(state, service) == rid  # left, not yet taken
    taken = mediareq.take(state, service)
    assert taken == {"id": rid, "kind": "refresh-guide"}
    assert not (state / "media-request").exists()  # claimed: moved out of the inbox
    assert mediareq.waiting(state, service) == rid  # now media is working on it
    mediareq.answer(service, rid, "refresh-guide", state="done", exit_code=0, output="refreshed")
    assert mediareq.waiting(state, service) is None  # answered: slot free again
    got = mediareq.result(service)
    assert got["id"] == rid and got["state"] == "done" and got["output"] == "refreshed\n"


def test_a_check_carries_the_address(tmp_path):
    state, service = dirs(tmp_path)
    rid = mediareq.ask(state, service, "check", url="http://example.com/p.m3u?token=abc=def")
    taken = mediareq.take(state, service)
    assert taken == {"id": rid, "kind": "check", "url": "http://example.com/p.m3u?token=abc=def"}


def test_a_test_channel_carries_the_key(tmp_path):
    state, service = dirs(tmp_path)
    rid = mediareq.ask(state, service, "test-channel", key="CNN.us")
    taken = mediareq.take(state, service)
    assert taken == {"id": rid, "kind": "test-channel", "key": "CNN.us"}


def test_the_request_file_is_owner_only(tmp_path):
    # It can hold a playlist address with a login in it, so no one else on the computer reads it.
    state, service = dirs(tmp_path)
    mediareq.ask(state, service, "check", url="http://user:pass@example.com/p.m3u")
    assert stat.S_IMODE((state / "media-request").stat().st_mode) == 0o600


def test_bad_requests_are_refused_before_they_are_left(tmp_path):
    state, service = dirs(tmp_path)
    with pytest.raises(mediareq.MediaError):
        mediareq.ask(state, service, "make-coffee")  # unknown kind
    with pytest.raises(mediareq.MediaError):
        mediareq.ask(state, service, "refresh-guide", url="http://x")  # takes no address
    with pytest.raises(mediareq.MediaError):
        mediareq.ask(state, service, "check")  # needs an address
    with pytest.raises(mediareq.MediaError):
        mediareq.ask(state, service, "test-channel")  # needs a key
    with pytest.raises(mediareq.MediaError):
        mediareq.ask(state, service, "check", url="http://x\nid=evil")  # no newline injection
    with pytest.raises(mediareq.MediaError):
        mediareq.ask(state, service, "test-channel", key="k", url="http://x")  # one field, not two


def test_only_one_request_waits_at_a_time(tmp_path):
    state, service = dirs(tmp_path)
    mediareq.ask(state, service, "refresh-guide")
    with pytest.raises(mediareq.MediaError):
        mediareq.ask(state, service, "check", url="http://x")  # the slot is taken
    # Once it's answered, the slot is free for the next one.
    taken = mediareq.take(state, service)
    mediareq.answer(service, taken["id"], taken["kind"], state="done", exit_code=0, output="")
    assert mediareq.ask(state, service, "check", url="http://x")


def test_take_returns_none_when_there_is_nothing(tmp_path):
    state, service = dirs(tmp_path)
    assert mediareq.take(state, service) is None


def test_an_expired_request_is_dropped_not_run(tmp_path):
    state, service = dirs(tmp_path)
    mediareq.ask(state, service, "refresh-guide")
    old = time.time() - mediareq.REQUEST_EXPIRES - 1
    os.utime(state / "media-request", (old, old))
    assert mediareq.take(state, service) is None  # too old: nobody is waiting for it
    assert mediareq.waiting(state, service) is None  # and the slot is free again
    assert mediareq.result(service) is None  # it never ran, so there's no answer


def test_a_known_kind_with_bad_fields_gets_a_failed_answer(tmp_path):
    # A request that parses but fails its checks (here, a doctored kind straight in the file)
    # is answered as failed so the page can show why, and the slot frees.
    state, service = dirs(tmp_path)
    (state).mkdir(parents=True)
    (state / "media-request").write_text("id=" + "a" * 16 + "\nkind=check\n")  # check with no url
    assert mediareq.take(state, service) is None
    got = mediareq.result(service)
    assert got["state"] == "failed" and got["exit"] == "2"
    assert mediareq.waiting(state, service) is None  # freed


def test_a_request_with_a_junk_id_is_dropped_silently(tmp_path):
    state, service = dirs(tmp_path)
    state.mkdir(parents=True)
    (state / "media-request").write_text("id=not a real id\nkind=refresh-guide\n")
    assert mediareq.take(state, service) is None
    assert mediareq.result(service) is None  # junk: dropped without an answer
    assert mediareq.waiting(state, service) is None


def test_a_symlink_in_the_inbox_is_not_followed(tmp_path):
    # Defense in depth: a link where a request should be is claimed and dropped, never read, so
    # it can't point media at another file.
    state, service = dirs(tmp_path)
    state.mkdir(parents=True)
    secret = tmp_path / "secret"
    secret.write_text("id=" + "b" * 16 + "\nkind=refresh-guide\n")
    (state / "media-request").symlink_to(secret)
    assert mediareq.take(state, service) is None
    assert secret.read_text().startswith("id=")  # untouched


def test_result_is_none_before_the_first_answer(tmp_path):
    _state, service = dirs(tmp_path)
    assert mediareq.result(service) is None
