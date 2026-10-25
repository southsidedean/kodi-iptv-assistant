import dataclasses
import json
from pathlib import Path

import pytest

from kia import api, auth, channels, m3u, profiles
from kia.m3u import Entry, write
from kia.numbers import ChannelNumbers


def entries() -> list[Entry]:
    return [
        Entry("Alpha", "http://a/1", {"tvg-id": "Alpha.us", "group-title": "News"}),
        Entry("Bravo", "http://a/2", {"tvg-id": "Bravo.us", "group-title": "Kids"}),
        Entry("Charlie", "http://a/3", {"tvg-id": "Charlie.us"}),
        Entry("KINK", "http://a/4", {"radio": "true"}),
    ]


def test_favorites_come_first_with_their_own_category():
    choices = channels.set_choice(channels.load(Path("/nonexistent")), "Charlie.us", "tv", favorite=True)
    out = channels.apply(entries(), choices)
    assert [e.name for e in out] == ["Charlie", "Alpha", "Bravo", "KINK"]
    assert out[0].group == "Favorites" and out[1].group == "News"
    # Applying again doesn't add the category twice, and un-starring removes it.
    again = channels.apply(out, choices)
    assert again[0].group == "Favorites"
    channels.set_choice(choices, "Charlie.us", "tv", favorite=False)
    back = channels.apply(again, choices)
    assert [e.name for e in back] == ["Alpha", "Bravo", "Charlie", "KINK"] and "group-title" not in back[2].attrs


def test_chosen_numbers_pin_like_a_playlist_file(tmp_path: Path):
    choices = channels.load(tmp_path)
    channels.set_choice(choices, "Bravo.us", "tv", number=1)
    es = channels.apply(entries(), choices)
    numbers = ChannelNumbers(tmp_path / "n.json")
    numbers.assign(es, "2026-10-20T00:00:00+00:00")
    by = {e.name: e.attrs["tvg-chno"] for e in es}
    assert by["Bravo"] == "1" and by["Alpha"] != "1" and by["KINK"] == "1"  # radio is numbered separately
    # A pin from your own playlist file wins.
    file_pinned = entries()
    file_pinned[1].pinned_number = 7
    assert channels.apply(file_pinned, choices)[1].pinned_number == 7


def test_number_choices_are_checked():
    choices = {"favorites": [], "numbers": {"tv": {"A": 5}, "radio": {}}}
    for bad in (0, -1, 100000, 2.5, "3", True):
        with pytest.raises(ValueError):
            channels.set_choice(choices, "B", "tv", number=bad)
    channels.set_choice(choices, "B", "tv", number=5)
    assert choices["numbers"]["tv"] == {"B": 5}  # the newest choice takes the number
    channels.set_choice(choices, "B", "tv", number=None)
    assert choices["numbers"]["tv"] == {}


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch) -> api.ApiConfig:
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    out, state = tmp_path / "output", tmp_path / "state"
    out.mkdir()
    es = entries()
    n = ChannelNumbers(state / "channel-numbers.json")
    n.assign(es, "2026-10-20T00:00:00+00:00")
    n.save()
    (out / "curated.m3u").write_text(write(es, "http://tv.lan:8780/guide.xml.gz"))
    profiles.save(tmp_path / "profiles.toml", [profiles.from_rules("favs", {"groups": ["Favorites"]})])
    return api.ApiConfig(out, state, tmp_path / "profiles.toml", "http://tv.lan:8780/guide.xml.gz",
                         auth.Auth(state, "secret"), 8743)  # fmt: skip


def call(cfg, body, password="secret", secure=True):
    headers = {"Host": "tv.lan:8743", "X-Kia-Password": password}
    return api.handle(cfg, "PUT", "/api/channel", headers, json.dumps(body).encode(), secure)


def test_api_stars_and_numbers_rewrite_the_playlists_now(cfg):
    assert call(cfg, {"key": "Charlie.us", "favorite": True}, secure=False)[0] == 403
    assert call(cfg, {"key": "Charlie.us", "favorite": True}, password="nope")[0] == 401
    status, data = call(cfg, {"key": "Charlie.us", "favorite": True})
    assert status == 200 and data["favorites"] == ["Charlie.us"] and data["channel"]["favorite"]
    curated = m3u.parse((cfg.output_dir / "curated.m3u").read_text())
    assert curated[0].name == "Charlie" and curated[0].group == "Favorites"
    assert (cfg.output_dir / "curated.m3u").read_text().startswith('#EXTM3U x-tvg-url="http://tv.lan:8780/guide.xml.gz"')
    assert [e.name for e in m3u.parse((cfg.output_dir / "favs.m3u").read_text())] == ["Charlie"]  # profiles too

    status, data = call(cfg, {"key": "Charlie.us", "number": 1})
    assert status == 200 and data["channel"]["number"] == "1"
    curated = {e.name: e.attrs["tvg-chno"] for e in m3u.parse((cfg.output_dir / "curated.m3u").read_text())}
    assert curated["Charlie"] == "1" and curated["Alpha"] != "1"  # Alpha had 1 and moved
    assert call(cfg, {"key": "Nobody.us", "favorite": True})[0] == 404
    assert call(cfg, {"key": "Charlie.us", "number": 0})[0] == 400
    get = api.handle(cfg, "GET", "/api/channel-choices", {"Host": "tv.lan:8780"}, b"", False)
    assert get == (200, {"favorites": ["Charlie.us"], "removed": {}, "numbers": {"tv": {"Charlie.us": 1}, "radio": {}}})


def test_api_renumber(cfg):
    call(cfg, {"key": "Charlie.us", "number": 7})
    status, data = api.handle(cfg, "POST", "/api/renumber", {"Host": "tv.lan:8743", "X-Kia-Password": "secret"}, b"", True)
    assert status == 200 and data == {"tv": 3, "radio": 1}
    listed = {e.name: e.attrs["tvg-chno"] for e in m3u.parse((cfg.output_dir / "curated.m3u").read_text())}
    assert listed == {"Alpha": "1", "Bravo": "2", "Charlie": "7", "KINK": "1"}  # the chosen number stays
    assert api.handle(cfg, "POST", "/api/renumber", {"Host": "tv.lan:8743"}, b"", True)[0] == 401


def test_recycle_setting_is_checked(tmp_path: Path, monkeypatch):
    from kia.config import load

    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    for bad in ("30", "-1", "true"):
        (tmp_path / "kia.toml").write_text(f"[output]\nrecycle_numbers_after_days = {bad}\n")
        with pytest.raises(ValueError, match="recycle_numbers_after_days"):
            load(tmp_path / "kia.toml")
    (tmp_path / "kia.toml").write_text("[output]\nrecycle_numbers_after_days = 90\n")
    assert load(tmp_path / "kia.toml").recycle_numbers_after_days == 90


def test_test_remove_and_put_back(cfg, monkeypatch):
    from kia.probe import ProbeResult

    async def fake_probe(url, **kw):
        return ProbeResult(ok=False, error="HTTP error 404 Not Found", elapsed=0.4)

    monkeypatch.setattr("kia.probe.probe", fake_probe)
    headers = {"Host": "tv.lan:8743", "X-Kia-Password": "secret"}

    def post(path, body):
        return api.handle(cfg, "POST", path, headers, json.dumps(body).encode(), True)

    status, r = post("/api/channel/test", {"key": "Bravo.us"})
    assert status == 200 and not r["ok"] and "404" in r["error"]
    status, choices = post("/api/channel/remove", {"key": "Bravo.us"})
    assert status == 200 and choices["removed"]["Bravo.us"]["name"] == "Bravo"
    names = [e.name for e in m3u.parse((cfg.output_dir / "curated.m3u").read_text())]
    assert "Bravo" not in names
    status, choices = post("/api/channel/restore", {"key": "Bravo.us"})
    assert status == 200 and choices["removed"] == {}
    back = {e.name: e for e in m3u.parse((cfg.output_dir / "curated.m3u").read_text())}
    assert "Bravo" in back and back["Bravo"].group == "Kids"
    assert post("/api/channel/restore", {"key": "Bravo.us"})[0] == 404
    assert api.handle(cfg, "POST", "/api/channel/test", {"Host": "tv.lan:8743"}, b'{"key": "Bravo.us"}', True)[0] == 401


def test_pluto_channels_are_tested_at_pluto():
    assert api.stream_url("http://192.168.1.20:8780/pluto/66ba495ffe11e5000881f049.m3u8") == \
        "https://jmp2.uk/plu-66ba495ffe11e5000881f049.m3u8"
    assert api.stream_url("https://example.org/a.m3u8") == "https://example.org/a.m3u8"


def test_nightly_check_puts_back_what_works(tmp_path: Path, monkeypatch):
    from kia import cli
    from kia.probe import ProbeResult

    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    monkeypatch.delenv("KIA_PUBLIC_URL", raising=False)
    (tmp_path / "list.m3u").write_text(write(entries()[:3]))
    (tmp_path / "sources.txt").write_text(f"{tmp_path / 'list.m3u'} mine\n")
    (tmp_path / "kia.toml").write_text("")
    choices = channels.load(tmp_path / "state")
    for name in ("Alpha", "Bravo"):
        e = next(x for x in entries() if x.name == name)
        choices["removed"][e.key] = channels.removal(e)
    channels.save(tmp_path / "state", choices)
    working = {"http://a/1": True, "http://a/2": False, "http://a/3": True}

    async def fake_probe_all(es, cfg, verbose):
        return {e.url: ProbeResult(ok=working[e.url], error=None if working[e.url] else "gone", height=720) for e in es}

    monkeypatch.setattr(cli, "probe_all", fake_probe_all)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli, "double_check_dns", lambda results, resolvers: {})

    def night():
        assert cli.main(["check", "-c", str(tmp_path / "kia.toml")]) == 0
        return [e.name for e in m3u.parse((tmp_path / "output" / "curated.m3u").read_text())]

    # One good night isn't enough: kia's check only opens the stream.
    assert night() == ["Charlie"]
    assert channels.load(tmp_path / "state")["removed"]["Alpha.us"]["good_nights"] == 1
    working["http://a/1"] = False  # a bad night starts the count again
    night()
    assert channels.load(tmp_path / "state")["removed"]["Alpha.us"]["good_nights"] == 0
    working["http://a/1"] = True
    assert night() == ["Charlie"] and night() == ["Charlie"]
    assert night() == ["Alpha", "Charlie"]  # three good nights in a row; Bravo still doesn't work
    assert list(channels.load(tmp_path / "state")["removed"]) == ["Bravo.us"]


def test_page_numbers_can_move_and_be_cleared_right_away(cfg):
    call(cfg, {"key": "Alpha.us", "number": 50})
    call(cfg, {"key": "Charlie.us", "number": 50})  # the newest choice takes 50
    nums = {e.name: e.attrs["tvg-chno"] for e in m3u.parse((cfg.output_dir / "curated.m3u").read_text())}
    assert nums["Charlie"] == "50" and nums["Alpha"] != "50"
    call(cfg, {"key": "Charlie.us", "number": None})  # cleared: no longer held
    call(cfg, {"key": "Bravo.us", "number": 50})
    nums = {e.name: e.attrs["tvg-chno"] for e in m3u.parse((cfg.output_dir / "curated.m3u").read_text())}
    assert nums["Bravo"] == "50" and nums["Charlie"] != "50"


def test_page_edits_add_no_numbers_when_numbering_is_off(cfg):
    cfg.channel_numbers = False
    plain = [Entry(e.name, e.url, {k: v for k, v in e.attrs.items() if k != "tvg-chno"}) for e in entries()]
    (cfg.output_dir / "curated.m3u").write_text(write(plain))
    call(cfg, {"key": "Charlie.us", "favorite": True})
    assert "tvg-chno" not in (cfg.output_dir / "curated.m3u").read_text()


def test_limits_of_zero_are_refused(tmp_path: Path, monkeypatch):
    from kia.config import load

    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    monkeypatch.delenv("KIA_CHECK_CONCURRENCY", raising=False)
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    for bad in ("concurrency = 0", "per_host = 0", 'per_host_limits = {"jmp2.uk" = 0}'):
        (tmp_path / "kia.toml").write_text(f"[check]\n{bad}\n")
        with pytest.raises(ValueError, match="1 or more"):
            load(tmp_path / "kia.toml")


def test_a_test_check_leaves_the_history_alone(tmp_path: Path, monkeypatch):
    from kia import cli
    from kia.probe import ProbeResult

    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    monkeypatch.delenv("KIA_PUBLIC_URL", raising=False)
    (tmp_path / "list.m3u").write_text(write(entries()[:2]))
    (tmp_path / "sources.txt").write_text(f"{tmp_path / 'list.m3u'} mine\n")
    (tmp_path / "kia.toml").write_text("")

    async def fake_probe_all(es, cfg, verbose):
        return {e.url: ProbeResult(ok=True, height=720) for e in es}

    monkeypatch.setattr(cli, "probe_all", fake_probe_all)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli, "double_check_dns", lambda results, resolvers: {})
    assert cli.main(["check", "-c", str(tmp_path / "kia.toml"), "--limit", "1"]) == 0
    assert not (tmp_path / "state" / "history.json").exists()
    assert (tmp_path / "work" / "check-test" / "report.json").exists()


def test_numbers_cant_be_changed_while_channel_numbers_are_off(cfg):
    off = dataclasses.replace(cfg, channel_numbers=False)
    status, data = call(off, {"key": "Charlie.us", "number": 7})
    assert status == 409 and "channel_numbers = false" in data["error"]
    headers = {"Host": "tv.lan:8743", "X-Kia-Password": "secret"}
    assert api.handle(off, "POST", "/api/renumber", headers, b"{}", True)[0] == 409
    assert api.handle(off, "GET", "/api/profiles", headers, b"", True)[1]["numbers"] is False
    assert call(off, {"key": "Charlie.us", "favorite": True})[0] == 200  # favorites still work


def test_the_test_button_tests_as_the_nightly_check_does(cfg, monkeypatch):
    from kia.probe import ProbeResult

    seen = {}

    async def fake_probe(url, **kw):
        seen.update(kw)
        return ProbeResult(ok=True, elapsed=0.1)

    monkeypatch.setattr("kia.probe.probe", fake_probe)
    mine = dataclasses.replace(cfg, require_video=False, user_agent=None, ffprobe="/opt/ffprobe")  # [check] in kia.toml
    headers = {"Host": "tv.lan:8743", "X-Kia-Password": "secret"}
    assert api.handle(mine, "POST", "/api/channel/test", headers, b'{"key": "Bravo.us"}', True)[0] == 200
    assert seen["require_video"] is False and seen["user_agent"] is None and seen["ffprobe"] == "/opt/ffprobe"
