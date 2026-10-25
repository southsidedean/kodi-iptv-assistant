"""kia's Grafana dashboards: every panel says why it's empty instead of Grafana's "No data",
so a panel that can't fill on this computer explains itself (scripts/dashboards.py)."""

import json
from pathlib import Path

import pytest

BOARDS = sorted((Path(__file__).resolve().parent.parent / "deploy" / "monitoring" / "dashboards").glob("*.json"))


@pytest.mark.parametrize("path", BOARDS, ids=lambda p: p.stem)
def test_every_panel_says_why_it_is_empty(path: Path):
    board = json.loads(path.read_text())
    panels = [p for p in board["panels"] if p["type"] != "row"]
    assert panels
    for p in panels:
        note = p["fieldConfig"]["defaults"].get("noValue")
        assert note, f"{p['title']} has no note for when it's empty"
        assert "—" not in note and "–" not in note
        for target in p.get("targets", []):
            assert "no_value" not in target["expr"] and note not in target["expr"], p["title"]  # a note, not a query


def test_tiles_show_their_note_when_the_source_isnt_set_up():
    """`or vector(0)` gives 0 whether or not anything's there, so the note never shows."""
    for path in BOARDS:
        for panel in json.loads(path.read_text())["panels"]:
            for target in panel.get("targets", []):
                assert "vector(0)" not in target.get("expr", ""), (path.name, panel["title"])


def test_running_on_battery_works_on_linux_too():
    hardware = json.loads(next(p for p in BOARDS if p.stem == "kia-hardware").read_text())
    expr = next(p for p in hardware["panels"] if p["title"] == "Running on")["targets"][0]["expr"]
    assert 'node_power_supply_info{type="Mains"}' in expr and "1 - max(node_power_supply_online" in expr
