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
