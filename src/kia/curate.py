"""Decide which streams make the curated playlist, and report on the rest."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from kia.m3u import Entry
from kia.probe import ProbeResult

STATUS_RANK = {"ok": 0, "flaky": 1, "dead": 2}


@dataclass
class Checked:
    entry: Entry
    result: ProbeResult
    status: str
    height: int | None = None
    selected: bool = False

    def to_dict(self) -> dict:
        e = self.entry
        return {
            "source": e.source,
            "name": e.name,
            "tvg_id": e.tvg_id,
            "group": e.group,
            "url": e.url,
            "status": self.status,
            "selected": self.selected,
            "height": self.height,
            "elapsed": self.result.elapsed,
            "error": self.result.error,
        }


def is_excluded(
    entry: Entry,
    groups: list[str],
    labels: list[str],
    name_patterns: list[str],
) -> bool:
    entry_groups = {g.strip().casefold() for g in entry.group.split(";") if g.strip()}
    if entry_groups & {g.casefold() for g in groups}:
        return True
    if {lb.casefold() for lb in entry.labels} & {lb.casefold() for lb in labels}:
        return True
    return any(re.search(p, entry.name, re.IGNORECASE) for p in name_patterns)


def classify(result: ProbeResult, history: dict, grace_failures: int) -> str:
    """ok: works now. flaky: failed now but worked recently. dead: everything else."""
    if result.ok:
        return "ok"
    if history.get("last_ok") and history.get("consecutive_failures", 0) <= grace_failures:
        return "flaky"
    return "dead"


def _rank(c: Checked) -> tuple:
    return (STATUS_RANK[c.status], -(c.height or 0), c.result.elapsed)


def select(checked: list[Checked]) -> list[Checked]:
    """Pick the best live stream for each channel; mark it selected."""
    groups: dict[str, list[Checked]] = {}
    for c in checked:
        if c.status != "dead":
            groups.setdefault(c.entry.key, []).append(c)
    chosen = []
    for items in groups.values():
        best = min(items, key=_rank)
        best.selected = True
        chosen.append(best)
    chosen.sort(key=lambda c: c.entry.name.casefold())
    return chosen


def render_markdown(
    checked: list[Checked],
    chosen: list[Checked],
    generated: str,
    resolvers: tuple[str, ...] = ("8.8.8.8", "1.1.1.1"),
) -> str:
    status = Counter(c.status for c in checked)
    unique_urls = len({c.entry.url for c in checked})
    lines = [
        "# Stream check report",
        "",
        f"Generated {generated}",
        "",
        "| | Count |",
        "|---|---:|",
        f"| Playlist entries checked | {len(checked)} |",
        f"| Unique stream URLs | {unique_urls} |",
        f"| Working (ok) | {status['ok']} |",
        f"| Recently working (flaky, kept) | {status['flaky']} |",
        f"| Dead | {status['dead']} |",
        f"| **Channels in curated playlist** | **{len(chosen)}** |",
        "",
    ]
    local_dns = sum(1 for c in checked if (c.result.error or "").startswith("LOCAL DNS FAILURE"))
    if local_dns:
        lines += [
            f"> **Warning:** {local_dns} streams failed because their hostnames don't resolve on this network's "
            f"DNS but do on the backup resolvers ({' / '.join(resolvers)}). Check your local DNS or ad-blocker before trusting "
            "those results.",
            "",
        ]

    by_source = Counter((c.entry.source, c.status) for c in checked)
    sources = sorted({c.entry.source for c in checked})
    if len(sources) > 1:
        lines += ["## By source", "", "| Source | ok | flaky | dead |", "|---|---:|---:|---:|"]
        for s in sources:
            lines.append(f"| {s} | {by_source[s, 'ok']} | {by_source[s, 'flaky']} | {by_source[s, 'dead']} |")
        lines.append("")

    errors = Counter(c.result.error for c in checked if c.status == "dead")
    if errors:
        lines += ["## Most common failures", "", "| Error | Streams |", "|---|---:|"]
        for err, n in errors.most_common(15):
            lines.append(f"| {(err or '?').replace('|', '/')} | {n} |")
        lines.append("")

    lines += [
        "## Curated channels",
        "",
        "| Channel | tvg-id | Group | Resolution | Status |",
        "|---|---|---|---|---|",
    ]
    for c in chosen:
        res = f"{c.height}p" if c.height else "?"
        lines.append(
            f"| {c.entry.name.replace('|', '/')} | `{c.entry.tvg_id or '-'}` | {c.entry.group} | {res} | {c.status} |"
        )
    return "\n".join(lines) + "\n"
