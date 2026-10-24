"""Which of kia's Grafana dashboard panels have data on this computer: every query of every
panel, asked of Prometheus as Grafana would. scripts/stack.sh status shows it."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DASHBOARDS = Path("deploy/monitoring/dashboards")


@dataclass
class Panel:
    board: str
    section: str  # the row it's in
    title: str
    has_data: bool
    note: str  # what Grafana shows instead when it's empty


def _query(expr: str) -> str:
    """A panel's query as Prometheus takes it: Grafana's variables filled in."""
    expr = re.sub(r"\$__rate_interval|\$__interval", "2m", expr)
    expr = re.sub(r"\$__range", "1h", expr)
    return re.sub(r'=~?"\$[a-z_]+"', '=~".+"', expr)  # a dashboard variable: any value


def _ask(prometheus: str, expr: str, timeout: float) -> int:
    url = prometheus.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": _query(expr)})
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return len(json.load(r)["data"]["result"])


def check(prometheus: str, folder: Path = DASHBOARDS, timeout: float = 20, ask=_ask) -> list[Panel]:
    """Each panel of each dashboard, with whether any of its queries returned data."""
    panels = []
    for path in sorted(folder.glob("*.json")):
        board = json.loads(path.read_text())
        section = ""
        for p in board.get("panels", []):
            if p.get("type") == "row":
                section = p.get("title", "")
                continue
            exprs = [t["expr"] for t in p.get("targets", []) if t.get("expr")]
            if not exprs:
                continue
            has = any(ask(prometheus, e, timeout) for e in exprs)
            note = p.get("fieldConfig", {}).get("defaults", {}).get("noValue", "")
            panels.append(Panel(board.get("title", path.stem), section, p.get("title", "?"), has, note))
    return panels


def report(panels: list[Panel]) -> list[str]:
    """One line per dashboard section: how many panels have data, and why the others don't."""
    lines, groups = [], {}
    for p in panels:
        groups.setdefault((p.board, p.section), []).append(p)
    for (board, section), items in groups.items():
        full = sum(p.has_data for p in items)
        line = f"{board} / {section or 'top'}: {full} of {len(items)} panels have data"
        # Small tiles have short versions of a panel's note; a note that's part of a longer
        # one's words (Needs monitoring-gpu, Needs the monitoring-gpu profile ...) is left out.
        notes = {p.note for p in items if not p.has_data and p.note}
        words = {n: set(n.lower().replace("(", " ").replace(")", " ").split()) for n in notes}
        why = sorted(n for n in notes if not any(o != n and words[n] < words[o] for o in notes))
        if why:
            line += " (" + "; ".join(why) + ")"
        lines.append(line)
    return lines
