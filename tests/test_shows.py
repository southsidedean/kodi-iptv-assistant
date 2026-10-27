"""kia.shows: finding shows in kia's guide, and in Plex's and Jellyfin's own guides."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kia import cli, shows
from kia.config import load
from kia.jellyfin import JellyfinError
from kia.m3u import Entry, write
from kia.plex import PlexError

NOW = datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def t(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S +0000")


def programme(channel: str, start: datetime, hours: float, title: str, extra: str = "", attrs: str = "") -> str:
    return (f'  <programme start="{t(start)}" stop="{t(start + hours * HOUR)}" channel="{channel}"{attrs}>'
            f"<title>{title}</title>{extra}</programme>\n")  # fmt: skip


@pytest.fixture
def kia(tmp_path: Path):
    """A kia folder with two channels and a guide: an NFL game tonight, sports now, a show about bears."""
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://example.com/us.m3u\n")
    out = tmp_path / "output"
    out.mkdir()
    (out / "curated.m3u").write_text(write([Entry("FOX (720p)", "http://a/1", {"tvg-id": "FOX.us", "tvg-chno": "5"}),
                                            Entry("Stadium", "http://a/2", {"tvg-id": "Stadium.us", "tvg-chno": "9"})], None))  # fmt: skip
    (out / "guide.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n  <channel id="FOX.us"><display-name>FOX</display-name></channel>\n'
        + programme("FOX.us", NOW + 2 * HOUR, 3, "NFL Football", "<sub-title>Bears at Packers</sub-title><category>Sports</category>")
        + programme("FOX.us", NOW - HOUR, 2, "Pawn Stars", "<desc>A deal on a statue of two bears &amp; a cub</desc>")
        + programme("Stadium.us", NOW - HOUR, 2, "College Football", "<category>Sports</category>")
        + programme("Stadium.us", NOW - HOUR, 2, "College Football", "<category>Sports</category>")  # in two playlists: listed twice
        + programme("Stadium.us", NOW + 30 * HOUR, 1, "NFL Throwback", "<sub-title>NFL Throwback</sub-title>")  # the title again
        + programme("FOX.us", NOW + 3 * HOUR, 1, "FOX", attrs=' kia-placeholder="1"')  # kia's stand-in: never a show
        + programme("FOX.us", NOW + 4 * HOUR, 1, "The Influencers")  # "nfl" inside a word: not a match
        + "</tv>\n"
    )
    return load(tmp_path / "kia.toml")


def titles(found: dict) -> list[str]:
    return [s["title"] for s in found["shows"]]


def test_kia_guide_by_words_category_and_time(kia):
    r = shows.search(kia, "NFL", now=NOW, env={})
    assert titles(r) == ["NFL Football"]  # Throwback is past the next 24 hours; "Influencers" isn't the word
    game = r["shows"][0]
    assert (game["channel"], game["number"], game["episode"], game["where"]) == ("FOX (720p)", "5", "Bears at Packers", "kia")
    later = shows.search(kia, "NFL", hours=48, now=NOW, env={})["shows"]
    assert [s["title"] for s in later] == ["NFL Football", "NFL Throwback"] and "episode" not in later[1]
    assert titles(shows.search(kia, category="sports", hours=0, now=NOW, env={})) == ["College Football"]  # on now, once
    # Named for the words first, then soonest: the game before the show whose description mentions bears.
    assert titles(shows.search(kia, "bears", now=NOW, env={})) == ["NFL Football", "Pawn Stars"]
    assert shows.search(kia, "curling", now=NOW, env={})["notes"][0].startswith("Nothing in the guides matches")
    assert titles(shows.search(kia, "bears & a cub", now=NOW, env={})) == ["Pawn Stars"]  # & is &amp; in the file


def test_after_and_before_narrow_to_a_time_of_day(kia):
    # Two football shows today, this afternoon and tonight. Times are built from the machine's
    # own offset, so the local clock is known whatever the test's time zone.
    local = datetime.now().astimezone()

    def utc_for_local(hour: int) -> datetime:
        return local.replace(hour=hour, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    now, afternoon, evening = utc_for_local(13), utc_for_local(14), utc_for_local(20)
    (kia.output_dir / "guide.xml").write_text(
        '<?xml version="1.0"?>\n<tv>\n'
        + programme("FOX.us", afternoon, 2, "College Football", "<category>Sports</category>")
        + programme("FOX.us", evening, 3, "NFL Football", "<category>Sports</category>")
        + "</tv>\n")
    assert set(titles(shows.search(kia, "football", now=now, env={}))) == {"College Football", "NFL Football"}
    assert titles(shows.search(kia, "football", now=now, after="18:00", env={})) == ["NFL Football"]
    assert titles(shows.search(kia, "football", now=now, after="12:00", before="18:00", env={})) == ["College Football"]
    bad = shows.search(kia, "football", now=now, after="6pm", env={})
    assert any("24-hour clock" in note for note in bad["notes"])


def test_many_matches_are_cut_and_said(kia, monkeypatch):
    monkeypatch.setattr(shows, "MAX_RESULTS", 1)
    r = shows.search(kia, "football", hours=48, now=NOW, env={})
    assert len(r["shows"]) == 1 and r["notes"][0].startswith("2 shows match; these are the first 1,")


def test_guide_text_is_quoted(kia):
    path = kia.output_dir / "guide.xml"
    path.write_text(path.read_text().replace("<title>NFL Football</title>",
                                             "<title>NFL https://me:pw@cdn/x " + "long " * 60 + "</title>"))  # fmt: skip
    title = shows.search(kia, "NFL", now=NOW, env={})["shows"][0]["title"]
    assert "pw" not in title and len(title) <= 120


def test_no_guide_yet(tmp_path):
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://example.com/us.m3u\n")
    assert shows.search(load(tmp_path / "kia.toml"), "NFL", now=NOW, env={})["shows"] == []


# Plex's own guide (an antenna's)


class FakePlex:
    def __init__(self, dvrs=None, answers=None):
        self._dvrs = [{"epgIdentifier": "tv.plex.providers.epg.cloud:5"}, {"epgIdentifier": "tv.plex.providers.epg.xmltv:8"},
                      {"epgIdentifier": "../../library"}] if dvrs is None else dvrs  # fmt: skip
        self.answers, self.asked = answers or {}, []

    def dvrs(self):
        return self._dvrs

    def call(self, method, path, params=None):
        self.asked.append(path)
        answer = next((v for k, v in self.answers.items() if k in path), {})
        if isinstance(answer, Exception):
            raise answer
        return answer


def airing(start: datetime, hours: float, vcn="32.1", title="32.1 WFLDDT (FOX)") -> dict:
    return {"beginsAt": int(start.timestamp()), "endsAt": int((start + hours * HOUR).timestamp()), "channelVcn": vcn, "channelTitle": title}


def test_plex_own_guide_only(kia):
    search = {"MediaContainer": {"Hub": [{"Metadata": [
        {"grandparentTitle": "NFL Football", "title": "Bears at Lions", "Media": [airing(NOW + HOUR, 3)]},
        {"title": "NFL Football", "Media": [airing(NOW - 5 * HOUR, 3)]},  # over already
        {"title": "Sunflower", "Media": [airing(NOW, 1)]}, "nonsense"]}, "nonsense"]}}  # fmt: skip
    plex = FakePlex(answers={"hubs/search": search})
    r = shows.search(kia, "NFL", now=NOW, env={}, plex=plex)
    assert [(s["title"], s.get("episode"), s["where"], s["number"]) for s in r["shows"]] == [
        ("NFL Football", "Bears at Lions", "Plex", "32.1"), ("NFL Football", "Bears at Packers", "kia", "5")]
    assert plex.asked == ["/tv.plex.providers.epg.cloud:5/hubs/search"]  # kia's guide in Plex, and an odd name, aren't asked


def test_plex_failing_or_asked_for_a_category(kia):
    r = shows.search(kia, "NFL", now=NOW, env={}, plex=FakePlex(answers={"hubs/search": PlexError("Plex answered 401 (check the token)")}))
    assert titles(r) == ["NFL Football"] and r["notes"] == ["Plex didn't answer: Plex answered 401 (check the token)"]
    r = shows.search(kia, "NFL", "sports", now=NOW, env={}, plex=FakePlex())
    assert r["notes"][0].startswith("Plex's guide search doesn't say shows' categories")


def test_plex_on_now(kia):
    grid = {"MediaContainer": {"Metadata": [
        {"grandparentTitle": "Marshals", "Media": [airing(NOW - HOUR, 2, "2.1", "2.1 WBBMDT (CBS)") | {"channelCallSign": "WBBMDT"}]},
        {"title": "Tracker", "Media": [airing(NOW + HOUR, 1, "2.1", "2.1 WBBMDT (CBS)")]},
        {"title": "CSI", "Media": [airing(NOW - HOUR, 2, "2.2", "2.2 WBBMDT2 (Start TV)")]}]}}  # fmt: skip
    plex = FakePlex(answers={"grid": grid})
    [cbs] = shows.plex_on_now({}, "2.1", NOW, plex=plex)
    assert (cbs["channel"], cbs["on_now"]["title"], [n["title"] for n in cbs["next"]]) == ("2.1 WBBMDT (CBS)", "Marshals", ["Tracker"])
    assert "beginsAt<=" in plex.asked[0]
    assert shows.plex_on_now({}, "", NOW, plex=plex) == [] and shows.plex_on_now({}, "2.1", NOW) == []  # no words; no Plex
    assert shows.plex_on_now({}, "2.1", NOW, plex=FakePlex(answers={"grid": PlexError("down")})) == []


# Jellyfin's own guide


class FakeJellyfin:
    def __init__(self, guides=("/config/sd.xml",), items=None, error=None):
        self.guides, self.items, self.error, self.asked = guides, items or [], error, []

    def call(self, path):
        self.asked.append(path)
        if self.error:
            raise self.error
        if path.startswith("/System/Configuration/livetv"):
            return {"ListingProviders": [{"Type": "xmltv", "Path": g} for g in self.guides]}
        if path == "/Users":
            return [{"Id": "u1"}]
        return {"Items": self.items}


def item(name, start, hours, channel="WGN (1080p)", number="9.1", genres=("Sports",)):
    iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    return {"Name": name, "StartDate": iso(start), "EndDate": iso(start + hours * HOUR), "ChannelName": channel, "ChannelNumber": number,
            "Genres": list(genres), "EpisodeTitle": None}  # fmt: skip


def test_jellyfin_own_guide(kia):
    jf = FakeJellyfin(items=[item("NFL Football", NOW + HOUR, 3), item("NFL Football", NOW - 9 * HOUR, 3),
                             item("NFL Football", NOW + 2 * HOUR, 3, channel="FOX (720p)"), "nonsense"])  # fmt: skip
    r = shows.search(kia, "NFL", now=NOW, env={}, jellyfin=jf)
    # Jellyfin's game on WGN; its copy of kia's FOX listing at the same time isn't repeated; the old one's over.
    assert [(s["channel"], s["where"]) for s in r["shows"]] == [("WGN (1080p)", "Jellyfin"), ("FOX (720p)", "kia")]
    assert any("includeItemTypes=LiveTvProgram" in p and "searchTerm=nfl" in p for p in jf.asked)  # any case finds it


def test_jellyfin_reading_only_kias_guide_isnt_asked(kia):
    (kia.output_dir / "tv.guide.xml.gz").write_text("")
    jf = FakeJellyfin(guides=("http://web/tv.guide.xml.gz",), items=[item("NFL Football", NOW + HOUR, 3)])
    assert titles(shows.search(kia, "NFL", now=NOW, env={}, jellyfin=jf)) == ["NFL Football"]  # kia's own only
    assert jf.asked == ["/System/Configuration/livetv"]
    r = shows.search(kia, "NFL", now=NOW, env={}, jellyfin=FakeJellyfin(error=JellyfinError("Can't reach Jellyfin")))
    assert r["notes"] == ["Jellyfin didn't answer: Can't reach Jellyfin"]


# The command and the MCP tool


def test_kia_find(kia, capsys, monkeypatch):
    import functools

    monkeypatch.setattr(shows, "search", functools.partial(shows.search, now=NOW))  # the guide's clock, not today's
    config = str(kia.config_file)
    assert cli.main(["find", "-c", config, "NFL", "--hours", "48"]) == 0
    out = capsys.readouterr().out
    assert "NFL Football: Bears at Packers  (FOX (720p) 5, kia)" in out
    assert cli.main(["find", "-c", config, "curling", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["shows"] == []
    assert cli.main(["find", "-c", config]) == 2
