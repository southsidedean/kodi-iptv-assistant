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
