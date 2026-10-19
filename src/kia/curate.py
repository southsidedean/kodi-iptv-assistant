"""Decide which streams make the curated playlist, and report on the rest."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace

from kia.epgmap import country, name_key
from kia.history import readable
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
    # The same channel from other playlists, merged into this one (see merge_by_name).
    also: list[Entry] = field(default_factory=list)

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


def select(
    checked: list[Checked], source_order: dict[str, int] | None = None, merge_duplicates: bool = False
) -> list[Checked]:
    """Pick the best live stream for each channel; mark it selected.

    With merge_duplicates, a channel that several playlists list under different ids
    becomes one channel (see merge_by_name). It keeps the identity (id, name, logo) of its
    copy in the earliest playlist in source_order, so its number and guide row stay the
    same from night to night, and plays whichever copy's stream tested best.
    """
    order = source_order or {}
    groups: dict[str, list[Checked]] = {}
    for c in checked:
        if c.status != "dead":
            groups.setdefault(c.entry.key, []).append(c)
    clusters = list(groups.values())
    if merge_duplicates:
        clusters = merge_by_name(clusters)
    chosen = []
    for items in clusters:
        best = min(items, key=_rank)
        best.selected = True
        identity = min(items, key=lambda c: (order.get(c.entry.source, len(order)), _rank(c)))
        out = best if identity is best else Checked(
            _play_from(identity.entry, best.entry), best.result, best.status, best.height, selected=True
        )
        seen = {(identity.entry.source, identity.entry.tvg_id)}
        for c in sorted(items, key=_rank):
            if c.entry.tvg_id and (c.entry.source, c.entry.tvg_id) not in seen:
                seen.add((c.entry.source, c.entry.tvg_id))
                if c.entry.tvg_id != identity.entry.tvg_id or c.entry.source != identity.entry.source:
                    out.also.append(c.entry)
        chosen.append(out)
    chosen.sort(key=lambda c: c.entry.name.casefold())
    return chosen


def _play_from(identity: Entry, stream: Entry) -> Entry:
    """identity's channel (id, name, logo, group) with stream's address and how to open it."""
    attrs = {k: v for k, v in identity.attrs.items() if k not in ("http-user-agent", "http-referrer")}
    attrs.update({k: v for k, v in stream.attrs.items() if k in ("http-user-agent", "http-referrer")})
    return replace(
        identity, url=stream.url, attrs=attrs, vlcopts=dict(stream.vlcopts), kodiprops=list(stream.kodiprops)
    )


def merge_by_name(clusters: list[list[Checked]]) -> list[list[Checked]]:
    """Join the same TV channel from different playlists: the same name (see name_key),
    each playlist contributing one channel of that name, and no clash of countries.

    A playlist with two channels of one name (e.g. two cities' feeds) makes that name
    ambiguous, so those stay apart.
    """
    by_name: dict[str, list[int]] = defaultdict(list)
    for i, items in enumerate(clusters):
        e = items[0].entry
        key = name_key(_TAG_RE.sub("", e.name))
        if not e.is_radio and len(key) >= 3:
            by_name[key].append(i)
    merged_into: dict[int, int] = {}
    for indexes in by_name.values():
        if len(indexes) < 2:
            continue
        sources = [{c.entry.source for c in clusters[i]} for i in indexes]
        if sum(len(s) for s in sources) != len(set().union(*sources)):
            continue  # some playlist has two channels of this name
        countries = {country(c.entry.tvg_id) for i in indexes for c in clusters[i]} - {""}
        if len(countries) > 1:
            continue
        for i in indexes[1:]:
            merged_into[i] = indexes[0]
    out: dict[int, list[Checked]] = {}
    for i, items in enumerate(clusters):
        out.setdefault(merged_into.get(i, i), []).extend(items)
    return list(out.values())


_TAG_RE = re.compile(r"\s*(\(\d{3,4}[pi]\)|\[[^\]]*\])")


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
        f"Generated {readable(generated)}",
        "",
        "| | Count |",
        "|---|---:|",
        f"| Playlist entries checked | {len(checked)} |",
        f"| Unique stream URLs | {unique_urls} |",
        f"| Working (ok) | {status['ok']} |",
        f"| Recently working (flaky, kept) | {status['flaky']} |",
        f"| Dead | {status['dead']} |",
        f"| Channels listed by more than one playlist, merged | {sum(1 for c in chosen if c.also)} |",
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
