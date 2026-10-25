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
    status(kia, env_private=False)
    write(kia, "output/pluto.json", {"fallback": True, "channels": []})
    t = titles(overview(kia))
    assert ".env can be read by other users on this computer" in t
    assert "A Pluto TV channel is playing without kia's fix" in t


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
    assert cli.main(["doctor", "-c", config, "plex"]) == 2


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
