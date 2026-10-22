import json
from datetime import datetime

import pytest

from kia import cli
from kia.alerts import EXIT_ALERT


def test_next_run():
    assert cli.next_run(datetime(2026, 10, 16, 3, 0), "04:00") == datetime(2026, 10, 16, 4, 0)
    assert cli.next_run(datetime(2026, 10, 16, 4, 0), "04:00") == datetime(2026, 10, 17, 4, 0)
    assert cli.next_run(datetime(2026, 10, 16, 23, 30), "04:15") == datetime(2026, 10, 17, 4, 15)
    with pytest.raises(ValueError):
        cli.next_run(datetime(2026, 10, 16), "4am")


@pytest.mark.parametrize(
    "codes, expected, steps_run",
    [
        ({}, 0, 4),
        ({"check": EXIT_ALERT}, EXIT_ALERT, 4),
        ({"grab": 1}, 1, 4),  # guide still builds from previous site guides
        ({"map": 2}, 2, 2),
        ({"check": 1}, 1, 1),
    ],
)
def test_run_pipeline(monkeypatch, codes, expected, steps_run):
    ran = []

    def fake_main(argv):
        ran.append(argv)
        return codes.get(argv[0], 0)

    monkeypatch.setattr(cli, "main", fake_main)
    assert cli.run_pipeline("kia.toml", update_epg=True) == expected
    assert len(ran) == steps_run
    if steps_run >= 2:
        assert ran[1] == ["map", "-c", "kia.toml", "--update"]


def test_next_run_keeps_wall_clock_time_across_dst(monkeypatch):
    import time

    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        # The evening before US clocks go back (Nov 1, 2026): 04:00 that morning is EST.
        nxt = cli.next_run_local("04:00", datetime(2026, 10, 31, 23, 0))
        assert (nxt.hour, nxt.utcoffset().total_seconds() / 3600) == (4, -5)
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_a_run_missed_while_the_computer_was_off_happens_at_start(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    cfg = type("Cfg", (), {"state_dir": state})()
    now = datetime(2026, 10, 22, 9, 30)  # local time; the 04:00 run was due this morning

    def ran(when: datetime) -> None:
        (state / "run.json").write_text(json.dumps({"started": when.astimezone().isoformat(timespec="seconds")}))

    assert not cli.missed_run(cfg, "04:00", now)  # no run yet: a new install runs anyway, when it has no guide
    ran(datetime(2026, 10, 22, 4, 0))
    assert not cli.missed_run(cfg, "04:00", now)  # this morning's run happened
    ran(datetime(2026, 10, 21, 4, 0))
    assert cli.missed_run(cfg, "04:00", now)  # off at 04:00 today: run now, not tomorrow
    assert not cli.missed_run(cfg, "04:00", datetime(2026, 10, 22, 3, 0))  # before today's run was due
    (state / "run.json").write_text("{")
    assert not cli.missed_run(cfg, "04:00", now)
