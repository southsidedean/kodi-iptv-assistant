import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kia import cli, doctor
from kia.config import load

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture
def kia(tmp_path: Path):
    """A kia folder with a good last refresh; tests change the files they're about."""
    (tmp_path / "sources.txt").write_text("https://example.com/us.m3u\n")
    (tmp_path / "kia.toml").write_text("")
    for d in ("output", "state", "work/logs"):
        (tmp_path / d).mkdir(parents=True)
    write(tmp_path, "output/status.json", {
        "result": "ok", "last_run": {"started": iso(NOW - 2 * HOUR), "finished": iso(NOW - HOUR), "minutes": 18, "exit_code": 0},
        "sources": [{"name": "us", "ok": 10, "flaky": 1, "dead": 2}], "coverage": {"full": 8, "partial": 1, "none": 2},
        "alerts": [], "env_private": True})  # fmt: skip
    return tmp_path


def write(base: Path, name: str, data) -> None:
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))


def status(base: Path, **changes) -> None:
    s = json.loads((base / "output/status.json").read_text())
    s.update(changes)
    write(base, "output/status.json", s)


def titles(report: doctor.Report) -> list[str]:
    return [f.title for f in report.findings]


def overview(base: Path) -> doctor.Report:
    return doctor.overview(load(base / "kia.toml"), now=NOW)


# The overview


def test_a_good_refresh_says_nothing_needs_you(kia):
    r = overview(kia)
    assert r.worst == doctor.OK and titles(r) == ["The last refresh worked", "Nothing needs you"]


def test_no_status_yet(kia):
    (kia / "output/status.json").unlink()
    r = overview(kia)
    assert r.worst == doctor.PROBLEM and titles(r) == ["kia hasn't written its status yet"]


def test_a_failed_refresh_points_to_troubleshooting(kia):
    status(kia, result="failed", last_run={"finished": iso(NOW - HOUR), "exit_code": 1})
    f = overview(kia).findings[0]
    assert f.level == doctor.PROBLEM and "exit code 1" in f.detail
    assert f.fix == "docs/troubleshooting.md#the-dashboard-says-the-refresh-failed"


def test_no_refresh_for_over_a_day(kia):
    status(kia, last_run={"finished": iso(NOW - 30 * HOUR), "minutes": 20})
    assert "kia hasn't refreshed for over a day" in titles(overview(kia))


@pytest.mark.parametrize("result, title", [("running", "A refresh is running now"), ("never", "The first refresh hasn't finished")])
def test_a_refresh_running_or_not_yet_done(kia, result, title):
    status(kia, result=result)
    assert titles(overview(kia))[0] == title


def test_alerts_failing_playlists_and_coverage(kia):
    status(kia, result="alerts", alerts=[{"time": iso(NOW - HOUR), "title": "kia: guide site failing: x.tv"}],
           sources=[{"name": "us", "ok": 5}, {"name": "broken", "ok": 0, "flaky": 0, "dead": 40}],
           coverage={"full": 1, "partial": 1, "none": 8})  # fmt: skip
    t = titles(overview(kia))
    assert "Alert: kia: guide site failing: x.tv" in t
    assert "None of the streams in broken work" in t and "None of the streams in us work" not in t
    assert "Most TV channels have no guide" in t and "Nothing needs you" not in t


def test_a_part_of_the_stack_that_doesnt_answer(kia):
    write(kia, "output/stack.json", {"checked": iso(NOW), "services": [
        {"service": "plex", "title": "Plex", "ok": True}, {"service": "threadfin", "title": "Threadfin", "ok": False}]})  # fmt: skip
    f = next(f for f in overview(kia).findings if f.level == doctor.PROBLEM)
    assert f.title == "Threadfin isn't answering" and f.fix.endswith("docker compose logs threadfin")


def test_env_readable_by_others_and_pluto_fallback(kia):
    status(kia, env_private=False, pluto_channels={"5f0" * 8: "Pluto Westerns"})
    write(kia, "output/pluto.json", {"updated": iso(NOW), "channels": [{"id": "5f0" * 8, "viewers": 1, "fallback_minutes": 6}]})
    t = titles(overview(kia))
    assert ".env can be read by other users on this computer" in t
    assert "Pluto Westerns is playing without kia's fix" in t


# One channel

STREAM = {"source": "us", "name": "CNN", "tvg_id": "CNN.us@SD", "group": "News", "url": "https://a/cnn.m3u8",
          "status": "ok", "selected": True, "height": 720, "error": None}  # fmt: skip


def kept(base: Path, *lines: str) -> None:
    write(base, "output/curated.m3u", "#EXTM3U\n" + "".join(lines))


def channel(base: Path, query: str) -> list[doctor.Report]:
    return doctor.channel(load(base / "kia.toml"), query)


CNN = '#EXTINF:-1 tvg-id="CNN.us@SD" group-title="News",CNN\nhttps://a/cnn.m3u8\n'


def test_a_channel_with_a_good_stream_and_a_full_guide(kia):
    kept(kia, CNN)
    write(kia, "output/report.json", {"streams": [STREAM]})
    write(kia, "state/history.json", {"https://a/cnn.m3u8": {"checks": 8, "successes": 8, "first_seen": iso(NOW - 96 * HOUR)}})
    write(kia, "work/mapping.json", {"CNN.us@SD": ["i.mjh.nz", "tvguide.com"]})
    write(kia, "work/guide-choices.json", {"CNN.us@SD": {"site": "i.mjh.nz", "hours_24h": 24.0, "programs": 40, "ends": None}})
    [r] = channel(kia, "cnn")
    assert r.subject == "CNN (CNN.us@SD)" and r.worst == doctor.OK
    assert titles(r) == ["Its stream works", "It has a full guide"]
    assert "720p" in r.findings[0].detail and "8 of 8 checks" in r.findings[0].detail
    assert "From i.mjh.nz: 24.0 of the next 24 hours, 40 programs." in r.findings[1].detail


def test_found_by_exact_id_too(kia):
    kept(kia, CNN, '#EXTINF:-1 tvg-id="CNNi.us",CNN International\nhttps://a/i\n')
    assert [r.subject for r in channel(kia, "CNN.us@SD")] == ["CNN (CNN.us@SD)"]
    assert len(channel(kia, "cnn")) == 2


def test_a_partial_guide_and_a_flaky_stream_in_two_playlists(kia):
    kept(kia, CNN)
    write(kia, "output/report.json", {"streams": [dict(STREAM, status="flaky"), dict(STREAM, source="extra", selected=False,
                                                                                    url="https://b/cnn", status="dead")]})  # fmt: skip
    write(kia, "state/history.json", {"https://a/cnn.m3u8": {"checks": 5, "successes": 4, "consecutive_failures": 1}})
    write(kia, "work/mapping.json", {"CNN.us@SD": ["s.tv"]})
    write(kia, "work/guide-choices.json", {"CNN.us@SD": {"site": "s.tv", "hours_24h": 11.0, "programs": 6, "ends": iso(NOW + 11 * HOUR)}})
    [r] = channel(kia, "CNN")
    assert titles(r) == ["Its stream failed in the last check, and is kept for now", "It's in 2 playlists", "It has a partial guide"]
    assert "failed the last 1 in a row" in r.findings[0].detail and "us (flaky), extra (dead)" in r.findings[1].detail
    assert "Its listings run to" in r.findings[2].detail


def test_no_guide_match(kia):
    kept(kia, CNN)
    write(kia, "work/mapping.json", {})
    [r] = channel(kia, "cnn")
    f = r.findings[-1]
    assert f.title == "No guide source matched it" and "accept_name_matches" in f.fix


def test_matched_but_its_sites_sent_nothing_quotes_their_logs(kia):
    kept(kia, CNN)
    write(kia, "work/mapping.json", {"CNN.us@SD": ["broken.tv", "fine.tv"]})
    write(kia, "work/guide-choices.json", {"CNN.us@SD": {"site": None, "hours_24h": 0, "programs": 0, "ends": None}})
    write(kia, "state/alert-state.json", {"site_failures": {"broken.tv": 3}})
    write(kia, "work/logs/broken.tv.log", "starting\nERR: getaddrinfo ENOTFOUND broken.tv\n")
    write(kia, "work/logs/fine.tv.log", "all good\n")
    [r] = channel(kia, "cnn")
    assert titles(r)[-2:] == ["Its guide sources sent nothing for the next day", "broken.tv had errors"]
    assert "failed 3 refreshes in a row" in r.findings[-1].detail and "ENOTFOUND" in r.findings[-1].detail


def test_before_the_first_guide_build_with_choices(kia):
    kept(kia, CNN)
    write(kia, "work/mapping.json", {"CNN.us@SD": ["a.tv"]})
    [r] = channel(kia, "cnn")
    assert r.findings[-1].title == "It has guide sources; which one it uses isn't recorded yet"


def test_radio_and_no_id(kia):
    kept(kia, '#EXTINF:-1 tvg-id="KINK.nl" radio="true",KINK\nhttps://k\n', "#EXTINF:-1,Local Feed\nhttps://l\n")
    assert channel(kia, "kink")[0].findings[-1].title == "No guide is expected"
    assert channel(kia, "local feed")[0].findings[-1].title == "It has no ID, so it can't have a guide"


def test_a_channel_tonights_check_dropped(kia):
    kept(kia)
    write(kia, "output/report.json", {"streams": [dict(STREAM, status="dead", selected=False, error="HTTP 404")]})
    write(kia, "state/history.json", {"https://a/cnn.m3u8": {"checks": 9, "successes": 6, "consecutive_failures": 3}})
    [r] = channel(kia, "cnn")
    assert r.worst == doctor.PROBLEM and titles(r) == ["It isn't in the playlists: its stream failed"]
    assert "us: HTTP 404" in r.findings[0].detail and "failed the last 3 in a row" in r.findings[0].detail


def test_a_channel_you_removed(kia):
    kept(kia)
    write(kia, "state/channels.json", {"removed": {"CNN.us@SD": {"when": iso(NOW - 24 * HOUR), "name": "CNN", "good_nights": 1}}})
    [r] = channel(kia, "cnn")
    assert titles(r) == ["You took it out of the playlists"] and "(1 so far)" in r.findings[0].detail


def test_unknown_too_many_and_empty(kia):
    kept(kia, *(f'#EXTINF:-1 tvg-id="N{i}.us",News {i}\nhttps://n/{i}\n' for i in range(8)))
    assert titles(channel(kia, "zzz")[0]) == ["No channel by that name"]
    assert titles(channel(kia, "news")[0]) == ["8 channels match"]
    assert titles(channel(kia, "  ")[0]) == ["Say which channel"]


# Doctor changes nothing, and the command


def test_a_damaged_history_is_left_as_it_is(kia):
    kept(kia, CNN)
    write(kia, "output/report.json", {"streams": [STREAM]})
    write(kia, "state/history.json", "{not json")
    before = sorted(p.name for p in (kia / "state").iterdir())
    assert titles(channel(kia, "cnn")[0])[0] == "Its stream works"
    assert sorted(p.name for p in (kia / "state").iterdir()) == before  # no history.json.bad


def test_third_party_text_in_a_log_is_cut_short(kia):
    kept(kia, CNN)
    write(kia, "work/mapping.json", {"CNN.us@SD": ["x.tv"]})
    write(kia, "work/guide-choices.json", {"CNN.us@SD": {"site": None}})
    write(kia, "work/logs/x.tv.log", "ERR " + "A" * 5000 + "\n")
    assert len(channel(kia, "cnn")[0].findings[-1].detail) < 300


def test_the_command_text_json_and_exit_codes(kia, capsys):
    config = str(kia / "kia.toml")
    assert cli.main(["doctor", "-c", config]) == 0
    assert "OK       Nothing needs you" in capsys.readouterr().out
    kept(kia)
    assert cli.main(["doctor", "-c", config, "channel", "nothing", "here", "--json"]) == 1
    [report] = json.loads(capsys.readouterr().out)
    assert report["subject"] == "nothing here" and report["worst"] == "problem"
    assert report["findings"][0]["evidence"] == ["output/curated.m3u", "output/report.json"]
    assert cli.main(["doctor", "-c", config, "hardware"]) == 0  # a note: without Prometheus there's nothing to ask
    assert "kia can't see the hardware without Prometheus" in capsys.readouterr().out
    assert cli.main(["doctor", "-c", config, "pluto"]) == 0
    assert "No Pluto TV channels go through kia" in capsys.readouterr().out
    assert cli.main(["doctor", "-c", config, "guide"]) == 0
    assert "kia hasn't built a guide yet" in capsys.readouterr().out
    assert cli.main(["doctor", "-c", config, "jellyfin"]) == 0
    assert "Jellyfin isn't set up with kia" in capsys.readouterr().out
    assert cli.main(["doctor", "-c", config, "nonsense"]) == 2


def test_a_login_in_quoted_text_is_hidden(kia):
    kept(kia)
    write(kia, "output/report.json", {"streams": [dict(STREAM, status="dead", selected=False,
                                                      error="403 from https://me:s3cret@example.com/live.m3u8")]})  # fmt: skip
    detail = channel(kia, "cnn")[0].findings[0].detail
    assert "s3cret" not in detail and "https://•••@example.com/live.m3u8" in detail


def test_the_dashboard_and_playlist_pages_offer_it(kia):
    from kia import channeleditor, doctorview, htmlpage, status

    page = status.render_html(status.collect(kia / "output", kia / "state", {"result": "ok"}, None))
    assert 'id="ask-why"' in page and "window.kiaWhy" in page
    # Answers go into the page as text; only kia's own docs pages become links.
    assert "innerHTML" not in doctorview.SCRIPT and htmlpage.DOCS_URL in doctorview.SCRIPT
    assert "window.kiaWhy(root, row.dataset.id || name(row), into)" in channeleditor.SCRIPT


@pytest.mark.parametrize(("text", "hidden", "kept"), [
    ("https://user:p@ss@host/x", "p@ss", "host/x"),
    ("1https://me:secret@host/", "secret", "host/"),
    ("//me:secret@host/live", "secret", "host/live"),
    ("http://u:secret@[fd00::5]:80/", "secret", "[fd00::5]:80/"),
    ("http://host:8080/get.php?username=USER&password=PASS", "PASS", "get.php?username="),
    ("http://host:8080/live/USER/PASS/123.ts", "PASS", "/live/"),
    ("url=https%3A%2F%2Fuser%3Asecret%40host%2F", "secret", "host"),
    ("me:secret@host:8080/live", "secret", "host:8080/live"),
    ("HTTPS://U:SECRET@HOST/", "SECRET", "HOST/"),
])
def test_logins_in_addresses_are_hidden(text, hidden, kept):
    shown = doctor.hide_logins(text)
    assert hidden not in shown and kept in shown and "•••" in shown


@pytest.mark.parametrize("text", ["mail me@example.com", "ERR: getaddrinfo ENOTFOUND x.tv", "https://example.com/docs#a"])
def test_ordinary_text_is_left_alone(text):
    assert doctor.hide_logins(text) == text


def test_damaged_files_of_the_wrong_shape(kia):
    kept(kia, CNN)
    write(kia, "output/report.json", [1, 2])
    write(kia, "work/mapping.json", {"CNN.us@SD": "not a list"})
    write(kia, "state/alert-state.json", {"site_failures": [1]})
    write(kia, "state/history.json", {"https://a/cnn.m3u8": "x"})
    status(kia, alerts="x", sources=[1, "y"], coverage=[1])
    write(kia, "output/stack.json", {"services": "nope"})
    assert overview(kia).findings and channel(kia, "cnn")[0].findings


# The guide


def guide_setup(base: Path) -> None:
    status(base, guide_updated=iso(NOW - 2 * HOUR), coverage={"full": 2, "partial": 1, "none": 2})
    write(base, "work/guide-choices.json", {
        "A.us": {"site": "pluto.tv"}, "B.us": {"site": "pluto.tv"}, "C.us": {"site": "tvguide.com"},
        "D.us": {"site": None}, "E.us": {"site": None}, "R.us": {"site": None}})  # fmt: skip
    write(base, "work/mapping.json", {"A.us": ["pluto.tv"], "D.us": ["distro.tv"]})
    write(base, "output/curated.m3u", '#EXTM3U\n#EXTINF:-1 tvg-id="R.us" radio="true",Radio\nhttps://r\n')


def test_guide_all_told(kia):
    guide_setup(kia)
    r = doctor.guide(load(kia / "kia.toml"), NOW)
    assert r.worst == doctor.NOTE
    assert titles(r) == ["The guide was rebuilt on " + doctor.readable(iso(NOW - 2 * HOUR)), "It comes from 2 guide sources",
                         "1 channel matched a guide site that sent nothing for the next day", "1 channel matched no guide source"]  # fmt: skip
    assert r.findings[0].detail == "2 TV channels have a full guide, 1 a partial one, and 2 only their name."
    assert r.findings[1].detail == "The most channels from pluto.tv (2), tvguide.com (1)."
    assert "distro.tv (1)" in r.findings[2].detail  # and the radio station isn't counted as missing a guide


def test_guide_problems(kia):
    guide_setup(kia)
    status(kia, guide_updated=iso(NOW - 30 * HOUR))
    write(kia, "state/alert-state.json", {"site_failures": {"distro.tv": 3, "xumo.tv": 1, "../x": 5, "y": "many"}})
    write(kia, "work/logs/distro.tv.log", "[ERR] Connection refused for https://me:pw@epg/listings\n")
    r = doctor.guide(load(kia / "kia.toml"), NOW)
    t = titles(r)
    assert t[0].startswith("The guide hasn't been rebuilt since") and r.findings[0].level == doctor.PROBLEM
    failing = {f.title: f for f in r.findings if "download" in f.title}
    assert failing["distro.tv failed its last 3 downloads"].level == doctor.PROBLEM  # the alert's threshold
    assert failing["xumo.tv failed its last 1 download"].level == doctor.NOTE
    assert "Connection refused" in failing["distro.tv failed its last 3 downloads"].detail
    assert "pw" not in failing["distro.tv failed its last 3 downloads"].detail
    assert failing["../x failed its last 5 downloads"].detail.startswith("Its channels")  # no log read for an odd name
    assert not any(f.title.startswith("y ") for f in r.findings)
    # The overview carries the guide's problems.
    assert "distro.tv failed its last 3 downloads" in titles(overview(kia))


def test_guide_before_its_first_build_and_with_an_upstream(kia):
    assert titles(doctor.guide(load(kia / "kia.toml"), NOW)) == ["kia hasn't built a guide yet"]
    status(kia, guide_updated=iso(NOW))
    assert titles(doctor.guide(load(kia / "kia.toml"), NOW))[-1] == "Which site each channel's guide comes from isn't recorded yet"
    guide_setup(kia)
    (kia / "kia.toml").write_text('[upstream]\nurl = "http://192.168.1.20:8780"\n')
    assert titles(doctor.guide(load(kia / "kia.toml"), NOW))[-1] == "2 channels got nothing from the upstream server"


# Pluto TV

PLUTO_ID = "5f0" * 8


def pluto_report(base: Path) -> doctor.Report:
    return doctor.pluto(load(base / "kia.toml"), NOW)


def test_pluto_quiet_and_off(kia, monkeypatch):
    assert titles(pluto_report(kia)) == ["No Pluto TV channels go through kia"]
    status(kia, pluto_channels={PLUTO_ID: "Pluto Westerns"})
    assert titles(pluto_report(kia)) == ["1 Pluto TV channel goes through kia's fix"]  # nothing playing now
    monkeypatch.setenv("KIA_PLUTO_RELAY", "false")
    assert titles(pluto_report(kia)) == ["kia's Pluto TV fix is off"]


def test_pluto_playing_and_falling_back(kia):
    status(kia, pluto_channels={PLUTO_ID: "Pluto Westerns", "a" * 24: "Pluto News"})
    write(kia, "output/pluto.json", {"updated": iso(NOW), "channels": [
        {"id": PLUTO_ID, "viewers": 2, "fallback_minutes": 6, "suspicious": True,
         "problems": [{"time": iso(NOW), "text": "Pluto answered 403 for https://me:pw@service/x"}]},
        {"id": "a" * 24, "viewers": 1, "fallback_minutes": 2}]})  # fmt: skip
    write(kia, "state/alerts.log", f"{iso(NOW - 2 * HOUR)}\tpluto_fallback\tkia: Pluto\tx\n{iso(NOW - 9 * 24 * HOUR)}\tpluto_fallback\told\tx\n"
                                   f"{iso(NOW)}\tguide_drop\tother\tx\nnot a line\n")  # fmt: skip
    r = pluto_report(kia)
    levels = {f.title: f.level for f in r.findings}
    assert levels["2 playing now, for 3 devices"] == doctor.OK
    assert levels["Pluto Westerns is playing without kia's fix"] == doctor.PROBLEM  # past the alert's 5 minutes
    assert levels["Pluto News is playing without kia's fix"] == doctor.NOTE
    problems = next(f for f in r.findings if f.title == "Pluto Westerns had problems")
    assert problems.detail.startswith("Pluto flagged kia's session as suspicious.") and "pw" not in problems.detail
    assert "Pluto TV fell back to its plain links 1 time in the last week" in levels  # the 9-day-old one isn't counted


def test_pluto_json_gone_quiet(kia):
    status(kia, pluto_channels={PLUTO_ID: "Pluto Westerns"})
    write(kia, "output/pluto.json", {"updated": iso(NOW - HOUR), "channels": [{"id": PLUTO_ID, "fallback_minutes": 60}]})
    t = titles(pluto_report(kia))
    assert t[1].startswith("pluto.json was last written on") and len(t) == 2  # an old file's channels aren't believed


# kia's setup


def setup_report(base: Path) -> doctor.Report:
    return doctor.setup(load(base / "kia.toml"), NOW)


def test_setup_all_right(kia, monkeypatch):
    monkeypatch.setenv("KIA_PUBLIC_URL", "http://192.168.1.20:8780")
    monkeypatch.setenv("KIA_NTFY_URL", "https://ntfy.sh/example")
    status(kia, secure={"tls": {"expires": iso(NOW + 400 * 24 * HOUR), "generated": True, "key_matches": True}})
    r = setup_report(kia)
    assert r.worst == doctor.OK and titles(r) == ["kia's setup looks right"]


def test_setup_notes_and_problems(kia, monkeypatch):
    from kia import doctor as d

    write(kia, "state/admin-password.txt", "made-by-kia")
    status(kia, secure={"tls": {"expires": iso(NOW + 10 * 24 * HOUR), "generated": False, "key_matches": False}},
           sources=[{"name": "us", "ok": 0, "dead": 9}, {"name": "uk", "ok": 0, "dead": 4}])  # fmt: skip
    monkeypatch.setattr(d.shutil, "disk_usage", lambda path: type("U", (), {"free": 200 * 1024**2})())
    r = setup_report(kia)
    levels = {f.title: f.level for f in r.findings}
    assert levels["The secure Dashboard's certificate and key don't match"] == doctor.PROBLEM
    assert any(t.startswith("Your certificate for the secure Dashboard expires on") for t in levels)
    assert levels["The Dashboard still has the password kia made"] == doctor.NOTE
    assert levels["kia has nowhere to send alerts"] == doctor.NOTE
    assert levels["Only 200 MB free for kia's folders"] == doctor.PROBLEM
    assert levels["kia's address is this computer only"] == doctor.NOTE
    assert levels["Every playlist's streams failed their test"] == doctor.PROBLEM
    # The overview carries the problems only.
    t = titles(overview(kia))
    assert "Only 200 MB free for kia's folders" in t and "kia has nowhere to send alerts" not in t
    # Your own certificate, already expired; kia's own is never flagged (kia renews it).
    status(kia, secure={"tls": {"expires": iso(NOW - HOUR), "generated": False}})
    assert any(t.startswith("Your certificate for the secure Dashboard expired on") for t in titles(setup_report(kia)))
    status(kia, secure={"tls": {"expires": iso(NOW - HOUR), "generated": True}})
    assert not any("certificate" in t for t in titles(setup_report(kia)))


# Plex


def plex_setup(base: Path, playlist: str = "plex", channels: int = 3) -> None:
    write(base, "threadfin/conf/settings.json", {"files": {"m3u": {"M1": {"file.source": f"http://web/{playlist}.m3u"}}}})
    lines = "".join(f'#EXTINF:-1 tvg-id="C{i}.us",Channel {i}\nhttps://a/{i}\n' for i in range(channels))
    write(base, f"output/{playlist}.m3u", "#EXTM3U\n" + lines)


def plex_report(base: Path, env: dict) -> doctor.Report:
    return doctor.plex(load(base / "kia.toml"), env=env, now=NOW)


def test_plex_not_set_up(kia):
    r = plex_report(kia, {})
    assert r.worst == doctor.NOTE and titles(r) == ["Plex isn't set up with kia"]


def test_plex_all_good(kia, monkeypatch):
    from kia import plex as plex_module

    plex_setup(kia)
    write(kia, "output/stack.json", {"services": [{"service": "threadfin", "ok": True}]})
    write(kia, "state/plex-tuners.json", {"when": iso(NOW - HOUR), "ok": True, "dvr": "7",
                                          "tuners": [{"title": "plex", "address": "threadfin:34400", "channels": 3}]})  # fmt: skip
    monkeypatch.setattr(plex_module, "has_codecs", lambda base: True)
    r = plex_report(kia, {"KIA_PLEX_URL": "http://plex:32400"})
    assert r.worst == doctor.OK and titles(r) == ["Threadfin reads plex.m3u: 3 TV channels", "Plex has kia's tuners"]
    assert "DVR 7" in r.findings[1].detail


def test_plex_problems(kia, monkeypatch):
    from kia import plex as plex_module

    plex_setup(kia, channels=481)
    write(kia, "output/stack.json", {"services": [{"service": "threadfin", "ok": False}, {"service": "plex", "ok": True}]})
    write(kia, "state/plex-tuners.json", {"when": iso(NOW - 72 * HOUR), "ok": False,
                                          "error": "Plex answered 401 to GET /livetv/dvrs (check the token)"})  # fmt: skip
    monkeypatch.setattr(plex_module, "has_codecs", lambda base: False)
    t = titles(plex_report(kia, {"KIA_PLEX_URL": "http://plex:32400"}))
    assert "Threadfin isn't answering" in t and "plex.m3u has 481 TV channels, more than Plex takes from one tuner (480)" in t
    assert "kia couldn't add the tuners to Plex" in t and "Plex hasn't downloaded its codecs yet" in t
    # And the overview carries Plex's problems (not its notes).
    with monkeypatch.context() as m:
        m.setenv("KIA_PLEX_URL", "http://plex:32400")
        assert "kia couldn't add the tuners to Plex" in titles(overview(kia))


def test_plex_left_over_from_an_earlier_stack(kia, monkeypatch):
    # After a switch from Plex to Jellyfin, the Threadfin folder and KIA_PLEX_URL stay behind; kia's
    # check of the stack says neither runs, so there's nothing wrong with Plex to report.
    from kia import plex as plex_module

    plex_setup(kia, playlist="gone")
    (kia / "output/gone.m3u").unlink()
    monkeypatch.setattr(plex_module, "has_codecs", lambda base: False)
    write(kia, "output/stack.json", {"services": [{"service": "jellyfin", "ok": True}]})
    r = plex_report(kia, {"KIA_PLEX_URL": "http://plex:32400"})
    assert r.worst == doctor.NOTE and titles(r) == ["KIA_PLEX_URL names Plex in kia's stack, which isn't in it now"]
    assert titles(plex_report(kia, {})) == ["Plex isn't set up with kia"]
    # Before kia's first check of the stack, the folder is all there is to go on.
    (kia / "output/stack.json").unlink()
    assert "Threadfin reads gone.m3u, which doesn't exist" in titles(plex_report(kia, {}))


def test_plex_missing_playlist_url_and_settings(kia):
    write(kia, "threadfin/conf/settings.json", {"files": {"m3u": {"M1": {"file.source": "http://web/plex-1.m3u"}}}})
    t = titles(plex_report(kia, {}))
    assert "Threadfin reads plex-1.m3u, which doesn't exist" in t and "kia doesn't keep Plex's channel list up to date" in t
    write(kia, "state/plex-tuners.json", {"when": iso(NOW), "ok": False, "missing": ["KIA_PLEX_TOKEN (see docs/plex.md)"]})
    assert "kia can't reach Plex: settings are missing" in titles(plex_report(kia, {"KIA_PLEX_URL": "http://192.168.1.20:32400"}))


# Hardware

HEALTHY = {'up{job="node"}': [({"job": "node"}, 1.0)], "count(smartctl_device)": [({}, 2.0)],
           "node_filesystem_avail_bytes": [({"device": "/dev/sda1", "mountpoint": "/"}, 40.0)]}  # fmt: skip


def hardware_report(base: Path, answers: dict, env: dict | None = None) -> doctor.Report:
    """doctor.hardware with Prometheus's answers: each query gets the answer whose key it contains."""

    def ask(expr: str):
        if isinstance(answers, Exception):
            raise answers
        return next((v for k, v in answers.items() if k in expr), [])

    write(base, "output/stack.json", {"services": [{"service": "prometheus", "ok": True}]})
    return doctor.hardware(load(base / "kia.toml"), env=env or {}, ask=ask)


def test_hardware_without_prometheus(kia):
    r = doctor.hardware(load(kia / "kia.toml"), env={}, ask=lambda expr: pytest.fail("asked Prometheus"))
    assert r.worst == doctor.NOTE and titles(r) == ["kia can't see the hardware without Prometheus"]


def test_hardware_all_good(kia):
    r = hardware_report(kia, HEALTHY)
    assert titles(r) == ["The hardware looks fine"]
    assert "the health of 2 disks (SMART)" in r.findings[0].detail and "graphics card" not in r.findings[0].detail
    # Without the disks' exporter, it says what would add them.
    assert "needs observability-disks" in hardware_report(kia, {**HEALTHY, "count(smartctl_device)": []}).findings[0].detail


def test_hardware_prometheus_or_node_exporter_down(kia):
    r = hardware_report(kia, OSError("Connection refused"))
    assert r.worst == doctor.PROBLEM and titles(r) == ["Prometheus isn't answering"] and "Connection refused" in r.findings[0].detail
    r = hardware_report(kia, {'up{job="node"}': [({"job": "node"}, 0.0)]})
    assert titles(r) == ["Prometheus can't reach node-exporter"]


def test_hardware_picks_the_same_disks_as_grafana():
    # A disk doctor calls nearly full should be the one the Hardware dashboard's Free space panel
    # shows, and Time Machine's snapshots of a Mac's main disk aren't disks of their own.
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("dashboards", root / "scripts" / "dashboards.py")
    dashboards = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dashboards)
    assert doctor._DISKS == dashboards.REAL_FS and "/Volumes/com.apple.TimeMachine" in doctor._DISKS


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"data": {"result": [{"value": []}]}}', b'{"data": null}'])
def test_prometheus_answer_in_the_wrong_shape(monkeypatch, body):
    # Whatever answers at Prometheus's address, an answer kia can't read counts as no answer (OSError).
    import io
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: io.BytesIO(body))
    with pytest.raises(OSError, match="didn't expect"):
        doctor._prometheus("http://prometheus:9090", "up")


def test_prometheus_answer(monkeypatch):
    import io
    import urllib.request

    asked = []
    body = b'{"data": {"result": [{"metric": {"device": "sda"}, "value": [1, "37"]}]}}'
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: asked.append(url) or io.BytesIO(body))
    assert doctor._prometheus("http://prometheus:9090/", 'up{job="node"}') == [({"device": "sda"}, 37.0)]
    assert asked == ["http://prometheus:9090/api/v1/query?query=up%7Bjob%3D%22node%22%7D"]


def test_hardware_problems(kia):
    answers = {**HEALTHY,
               "node_filesystem_avail_bytes": [({"device": "/dev/sda1", "mountpoint": "/"}, 4.2),
                                               ({"device": "/dev/sda1", "mountpoint": "/var/snap/firefox/common/host-hunspell"}, 4.2),
                                               ({"device": "/dev/sdb1", "mountpoint": "/media/content"}, 55.0)],
               'node_md_disks{state="failed"}': [({"device": "md0"}, 1.0)],
               "node_md_state": [({"device": "md1"}, 1.0)],
               "node_edac_uncorrectable": [({}, 2.0)],
               "node_cpu_core_throttles": [({}, 12.0)],
               "Battery Power": [({}, 1.0)], "current_capacity": [({}, 15.0)], "time_to_empty": [({}, 600.0)],
               "smartctl_device_smart_status": [({"device": "sdc"}, 0.0)],
               "Current_Pending_Sector": [({"device": "sdc"}, 8.0)],
               "Reallocated_Sector_Ct": [({"device": "sdd"}, 1.0)],
               "smartctl_device_temperature": [({"device": "sdd"}, 63.0)],
               "percentage_used": [({"device": "nvme0"}, 97.0)],
               "DCGM_FI_DEV_GPU_TEMP": [({}, 91.0)]}  # fmt: skip
    r = hardware_report(kia, answers)
    assert titles(r) == [
        "/ is nearly full: 4% free",  # once, though the snap sees the same disk
        "RAID array md0 has 1 failed disk", "RAID array md1 is rebuilding or checking itself",
        "The memory had errors it couldn't correct in the last day",
        "The processor slowed itself down 12 times in the last hour",
        "The computer is running on its battery",
        "Disk sdc fails its own health check (SMART)", "Disk sdc has 8 sectors it couldn't read",
        "Disk sdd has replaced 1 worn sector", "Disk sdd is hot: 63°C", "Disk nvme0 has used 97% of the writes it's made for",
        "The graphics card is hot: 91°C"]  # fmt: skip
    battery = r.findings[5]
    assert battery.level == doctor.PROBLEM and battery.detail.startswith("15% charged, about 10 minutes left.")
    # A fuller battery is only a note; corrected memory errors are a note too.
    r = hardware_report(kia, {**HEALTHY, "Battery Power": [({}, 1.0)], "current_capacity": [({}, 80.0)],
                              "node_edac_correctable": [({}, 3.0)]})  # fmt: skip
    assert [(f.level, f.title) for f in r.findings] == [(doctor.NOTE, "The memory corrected 3 errors in the last day"),
                                                         (doctor.NOTE, "The computer is running on its battery")]  # fmt: skip


def test_hardware_names_from_the_computer_are_text(kia):
    # A mount point's or disk's name comes from the computer: one line, cut short, logins hidden.
    r = hardware_report(kia, {**HEALTHY, "node_filesystem_avail_bytes": [({"device": "x", "mountpoint": "/mnt/a\nb" + "c" * 99}, 1.0)],
                              "smartctl_device_smart_status": [({"device": "http://me:pw@nas/x"}, 0.0)],
                              "smartctl_device_temperature": [({"device": "sda"}, float("nan"))]})  # fmt: skip
    assert len(r.findings[0].title) < 100 and "\n" not in r.findings[0].title
    assert "pw" not in r.findings[1].title and len(r.findings) == 2


def test_overview_carries_hardware_problems_when_prometheus_answers(kia, monkeypatch):
    monkeypatch.setattr(doctor, "_prometheus", lambda url, expr, timeout=3: (_ for _ in ()).throw(OSError("refused")))
    assert "Prometheus isn't answering" not in titles(overview(kia))  # not in the stack: not asked
    write(kia, "output/stack.json", {"services": [{"service": "prometheus", "ok": True}]})
    assert "Prometheus isn't answering" in titles(overview(kia))


# Jellyfin

JF_ENV = {"KIA_JELLYFIN_URL": "http://jellyfin:8096", "KIA_JELLYFIN_TOKEN": "key"}
LIVE_TV = {"TunerHosts": [{"Type": "m3u", "Url": "http://web/tv.m3u"}],
           "ListingProviders": [{"Type": "xmltv", "Path": "http://web/tv.guide.xml.gz"}]}  # fmt: skip
REFRESHED = [{"Key": "RefreshGuide", "LastExecutionResult": {"Status": "Completed", "EndTimeUtc": "2026-10-25T01:23:47.8691185Z"}}]


class FakeJellyfin:
    """Jellyfin's answers by path; an exception in place of an answer is raised."""

    def __init__(self, live_tv=LIVE_TV, channels=1509, tasks=REFRESHED):
        self.answers = {"/System/Configuration/livetv": live_tv, "/LiveTv/Channels": {"TotalRecordCount": channels},
                        "/ScheduledTasks": tasks}  # fmt: skip

    def call(self, path: str):
        answer = next(v for k, v in self.answers.items() if path.startswith(k))
        if isinstance(answer, Exception):
            raise answer
        return answer


def jellyfin_setup(base: Path, radio: int = 0) -> None:
    lines = "".join(f'#EXTINF:-1 tvg-id="C{i}.us",Channel {i}\nhttps://a/{i}\n' for i in range(3))
    lines += "".join(f'#EXTINF:-1 tvg-id="R{i}.us" radio="true",Radio {i}\nhttps://r/{i}\n' for i in range(radio))
    write(base, "output/tv.m3u", "#EXTM3U\n" + lines)
    write(base, "output/tv.guide.xml.gz", "")
    write(base, "output/guide.xml.gz", "")


def jellyfin_report(base: Path, client=None, env=JF_ENV) -> doctor.Report:
    return doctor.jellyfin(load(base / "kia.toml"), env=env, now=NOW, client=client or FakeJellyfin())


def test_jellyfin_not_set_up_or_missing_settings(kia):
    assert titles(jellyfin_report(kia, env={})) == ["Jellyfin isn't set up with kia"]
    write(kia, "output/stack.json", {"services": [{"service": "jellyfin", "ok": False}]})
    assert titles(jellyfin_report(kia, env={})) == ["Jellyfin isn't answering", "kia doesn't know Jellyfin's address"]
    assert titles(jellyfin_report(kia, env={"KIA_JELLYFIN_URL": "http://jellyfin:8096"}))[-1] == \
        "kia can't check Jellyfin's Live TV without an API key"


def test_jellyfin_left_over_from_an_earlier_stack(kia):
    # KIA_JELLYFIN_URL stays behind when Jellyfin leaves the stack: nothing to ask, and no problem.
    write(kia, "output/stack.json", {"services": [{"service": "plex", "ok": True}]})
    r = jellyfin_report(kia, FakeJellyfin(RuntimeError("asked Jellyfin")))  # raised if it asks
    assert r.worst == doctor.NOTE and titles(r) == ["KIA_JELLYFIN_URL names Jellyfin in kia's stack, which isn't in it now"]
    # A Jellyfin elsewhere (not "jellyfin" on Docker's network) is still asked.
    other = {**JF_ENV, "KIA_JELLYFIN_URL": "http://192.168.1.20:8096"}
    assert titles(jellyfin_report(kia, FakeJellyfin({"TunerHosts": []}), env=other)) == ["Jellyfin has no M3U tuner"]


def test_jellyfin_all_good(kia):
    jellyfin_setup(kia)
    r = jellyfin_report(kia)
    assert r.worst == doctor.OK
    assert titles(r) == ["Jellyfin reads tv.m3u: 3 TV channels", "Jellyfin shows 1509 channels", "Jellyfin refreshed its guide"]
    # Jellyfin's seven decimals read (on Python 3.11 too), shown in this computer's time zone.
    assert r.findings[-1].detail.startswith("On ") and " Oct 2" in r.findings[-1].detail


def test_jellyfin_problems(kia):
    from kia.jellyfin import JellyfinError

    jellyfin_setup(kia, radio=2)
    write(kia, "output/kids.m3u", "#EXTM3U\n")
    # A playlist with radio in it, a guide for another playlist, no channels loaded, a failed refresh.
    live_tv = {"TunerHosts": [{"Type": "m3u", "Url": "http://192.168.1.20:8780/tv.m3u"}, {"Type": "hdhomerun", "Url": "x"}],
               "ListingProviders": [{"Type": "xmltv", "Path": "http://web/kids.guide.xml.gz"}]}  # fmt: skip
    failed = [{"Key": "RefreshGuide", "LastExecutionResult": {"Status": "Failed", "EndTimeUtc": "2026-10-25T01:00:00Z",
                                                              "ErrorMessage": "Couldn't download http://me:pw@web/x"}}]  # fmt: skip
    r = jellyfin_report(kia, FakeJellyfin(live_tv, channels=0, tasks=failed))
    assert titles(r) == ["Jellyfin reads tv.m3u: 3 TV channels", "tv.m3u has 2 radio stations too",
                         "Jellyfin's guide is kids.guide.xml.gz, which isn't one of kia's", "Jellyfin's guide doesn't go with tv.m3u",
                         "Jellyfin has no channels yet", "Jellyfin's last guide refresh failed"]  # fmt: skip
    assert "pw" not in r.findings[-1].detail
    # No tuner at all; no guide; a playlist that isn't kia's.
    assert titles(jellyfin_report(kia, FakeJellyfin({"TunerHosts": []}))) == ["Jellyfin has no M3U tuner"]
    t = titles(jellyfin_report(kia, FakeJellyfin({"TunerHosts": [{"Type": "m3u", "Url": "http://other/list.m3u"}]})))
    assert t[:2] == ["Jellyfin reads list.m3u, which isn't one of kia's", "Jellyfin has no XMLTV guide"]
    # The full guide fits any playlist.
    full = {**LIVE_TV, "ListingProviders": [{"Type": "xmltv", "Path": "/config/guide.xml.gz"}]}
    assert doctor.PROBLEM not in {f.level for f in jellyfin_report(kia, FakeJellyfin(full)).findings}
    # Jellyfin refusing kia's key; a later question failing counts as no data; an old refresh.
    r = jellyfin_report(kia, FakeJellyfin(JellyfinError("Jellyfin answered 401 to /System/Configuration/livetv (check KIA_JELLYFIN_TOKEN)")))
    assert titles(r) == ["kia can't ask Jellyfin"] and "KIA_JELLYFIN_TOKEN" in r.findings[0].detail
    old = [{"Key": "RefreshGuide", "LastExecutionResult": {"Status": "Completed", "EndTimeUtc": "2026-10-20T01:00:00Z"}}]
    t = titles(jellyfin_report(kia, FakeJellyfin(channels=JellyfinError("timed out"), tasks=old)))
    assert t == ["Jellyfin reads tv.m3u: 3 TV channels", "tv.m3u has 2 radio stations too",
                 "Jellyfin last refreshed its guide over two days ago"]


def test_jellyfin_damaged_answers(kia):
    # Jellyfin's answers are read as they come: anything in the wrong shape counts as missing.
    jellyfin_setup(kia)
    r = jellyfin_report(kia, FakeJellyfin(live_tv=["nope"], channels="many", tasks=[{"Key": "RefreshGuide", "LastExecutionResult": "x"}]))
    assert titles(r) == ["Jellyfin has no M3U tuner"]
    r = jellyfin_report(kia, FakeJellyfin(channels=None, tasks=[{"Key": "RefreshGuide", "LastExecutionResult": "x"}]))
    assert titles(r)[-1] == "Jellyfin hasn't refreshed its guide yet"


def test_overview_carries_jellyfin_problems_with_its_settings(kia, monkeypatch):
    from kia import jellyfin as jellyfin_module

    monkeypatch.setattr(jellyfin_module.Jellyfin, "call", lambda self, path: {"TunerHosts": []})
    assert "Jellyfin has no M3U tuner" not in titles(overview(kia))  # no settings: not asked
    for k, v in JF_ENV.items():
        monkeypatch.setenv(k, v)
    assert "Jellyfin has no M3U tuner" in titles(overview(kia))


def test_every_docs_link_in_doctors_answers_exists():
    # doctor's "what to do" links point at doc sections; a renamed heading would break them silently.
    import re

    from tests.test_docs import github_anchor

    def anchors(page: Path) -> set[str]:
        return {github_anchor(h) for h in re.findall(r"^#+ (.+)$", page.read_text(), re.M)}

    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "kia" / "doctor.py").read_text()
    links = set(re.findall(r"(docs/[\w/.-]+\.md)(?:#([\w-]+))?", source))
    assert len(links) > 10
    for page, anchor in links:
        assert (root / page).exists(), page
        if anchor:
            assert anchor in anchors(root / page), f"{page}#{anchor}"
