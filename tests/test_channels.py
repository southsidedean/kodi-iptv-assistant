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
    ChannelNumbers(state / "channel-numbers.json").assign(es, "2026-10-20T00:00:00+00:00")
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
    assert get == (200, {"favorites": ["Charlie.us"], "numbers": {"tv": {"Charlie.us": 1}, "radio": {}}})


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
