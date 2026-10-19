"""Merge per-site XMLTV guides into one guide whose channel ids match the playlist."""

from __future__ import annotations

import gzip
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from kia.m3u import Entry

# Marks programmes kia generated for channels without guide data. Kodi ignores unknown
# attributes; kia uses it to skip another kia server's placeholders when reading its guide.
PLACEHOLDER_ATTR = "kia-placeholder"

_TAG_RE = re.compile(r"\s*(\(\d{3,4}p\)|\[[^\]]*\])")


def display_name(entry: Entry) -> str:
    """"FNX KVCR-DT (720p) [Geo-blocked]" -> "FNX KVCR-DT"."""
    return _TAG_RE.sub("", entry.name).strip() or entry.name


def parse_time(value: str) -> datetime:
    value = value.strip()
    if " " in value:
        return datetime.strptime(value, "%Y%m%d%H%M%S %z")
    return datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def format_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S %z")


def _open(path: Path):
    return gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb")


def has_title(el: ET.Element) -> bool:
    """A programme without a title isn't guide data: Kodi would show an empty block.
    (In October 2026, every programme from ontvtonight.com had none.)"""
    return any((t.text or "").strip() for t in el.iter("title"))


def scan(path: Path) -> dict[str, list[tuple[datetime, datetime]]]:
    """Programme time slots per channel id, without keeping the programmes in memory."""
    slots: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    with _open(path) as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag == "programme" and (el.get(PLACEHOLDER_ATTR) or not has_title(el)):
                el.clear()
            elif el.tag == "programme":
                try:
                    slots[el.get("channel", "")].append((parse_time(el.get("start")), parse_time(el.get("stop"))))
                except (TypeError, ValueError):
                    pass
                el.clear()
    return slots


def covered_hours(slots: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> float:
    """Hours of [start, end) covered by at least one programme."""
    clipped = sorted((max(a, start), min(b, end)) for a, b in slots if b > start and a < end)
    total = timedelta()
    cur_a = cur_b = None
    for a, b in clipped:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total.total_seconds() / 3600


@dataclass
class Choice:
    tvg_id: str
    site: str | None
    hours_24h: float
    programmes: int


def choose_sources(
    tvg_ids: list[str],
    site_slots: dict[str, dict[str, list[tuple[datetime, datetime]]]],
    site_order: dict[str, list[str]],
    now: datetime,
) -> dict[str, Choice]:
    """For each channel, the site with the most guide coverage over the next 24 hours.

    site_order gives each channel's candidate sites in preference order, which breaks ties.
    """
    window_end = now + timedelta(hours=24)
    choices = {}
    for tid in tvg_ids:
        best = Choice(tid, None, 0.0, 0)
        for site in site_order.get(tid, []):
            slots = site_slots.get(site, {}).get(tid, [])
            if not slots:
                continue
            hours = covered_hours(slots, now, window_end)
            if hours <= 0:
                continue  # only old or far-off listings: the channel's name shows instead
            if (hours, len(slots)) > (best.hours_24h, best.programmes):
                best = Choice(tid, site, round(hours, 1), len(slots))
        choices[tid] = best
    return choices


def _placeholders(tvg_id: str, title: str, start: datetime, days: int, hours: int = 2) -> list[str]:
    start = start.replace(minute=0, second=0, microsecond=0)
    start -= timedelta(hours=start.hour % hours)
    out = []
    for i in range(days * 24 // hours):
        a = start + timedelta(hours=i * hours)
        b = a + timedelta(hours=hours)
        out.append(
            f"  <programme start={quoteattr(format_time(a))} stop={quoteattr(format_time(b))} "
            f'channel={quoteattr(tvg_id)} {PLACEHOLDER_ATTR}="1"><title>{escape(title)}</title></programme>'
        )
    return out


def write_guide(
    out_path: Path,
    entries: list[Entry],
    choices: dict[str, Choice],
    site_files: dict[str, Path],
    placeholders: bool,
    days: int,
    now: datetime,
    placeholder_hours: int = 2,
    radio_placeholders: bool = False,
) -> None:
    """Write guide.xml (and guide.xml.gz): one <channel> per playlist entry, programmes from the chosen site."""
    wanted: dict[str, set[str]] = defaultdict(set)
    for c in choices.values():
        if c.site:
            wanted[c.site].add(c.tvg_id)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="kodi-iptv-assistant">\n')
        for e in entries:
            f.write(f"  <channel id={quoteattr(e.tvg_id)}>")
            f.write(f"<display-name>{escape(display_name(e))}</display-name>")
            if logo := e.attrs.get("tvg-logo"):
                f.write(f"<icon src={quoteattr(logo)}/>")
            f.write("</channel>\n")

        for site, ids in sorted(wanted.items()):
            with _open(site_files[site]) as src:
                for _, el in ET.iterparse(src, events=("end",)):
                    if el.tag != "programme":
                        continue
                    if el.get("channel") in ids and not el.get(PLACEHOLDER_ATTR) and has_title(el):
                        el.tail = None
                        f.write("  " + ET.tostring(el, encoding="unicode") + "\n")
                    el.clear()

        if placeholders:
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            for e in entries:
                if e.is_radio and not radio_placeholders:
                    continue
                if not choices.get(e.tvg_id) or not choices[e.tvg_id].site:
                    f.write("\n".join(_placeholders(e.tvg_id, display_name(e), day_start, days, placeholder_hours)) + "\n")
        f.write("</tv>\n")
    tmp.replace(out_path)

    gz_path = out_path.with_name(out_path.name + ".gz")
    gz_tmp = gz_path.with_name(gz_path.name + ".tmp")
    with open(out_path, "rb") as src, gzip.open(gz_tmp, "wb") as dst:
        shutil.copyfileobj(src, dst)
    gz_tmp.replace(gz_path)


def render_report(entries: list[Entry], choices: dict[str, Choice], generated: str) -> str:
    radio_count = sum(1 for e in entries if e.is_radio)
    entries = [e for e in entries if not e.is_radio]  # radio rarely has guide data; report it separately
    choices = {e.tvg_id: choices[e.tvg_id] for e in entries if e.tvg_id in choices}
    total = len(entries)
    full = [c for c in choices.values() if c.site and c.hours_24h >= 20]
    partial = [c for c in choices.values() if c.site and c.hours_24h < 20]
    none = [e for e in entries if not choices.get(e.tvg_id) or not choices[e.tvg_id].site]
    by_site = Counter(c.site for c in choices.values() if c.site)
    pct = (lambda n: f"{100 * n / total:.0f}%") if total else (lambda n: "-")
    names = {e.tvg_id: display_name(e) for e in entries}
    lines = [
        "# Guide coverage report",
        "",
        f"Generated {generated}",
        "",
        "| | Channels | Share |",
        "|---|---:|---:|",
        f"| Full guide (20+ of the next 24 hours) | {len(full)} | {pct(len(full))} |",
        f"| Partial guide | {len(partial)} | {pct(len(partial))} |",
        f"| No guide data (placeholders) | {len(none)} | {pct(len(none))} |",
        "",
        *([f"Plus {radio_count} radio stations, which usually have no guide data.", ""] if radio_count else []),
        "## Guide data by site",
        "",
        "| Site | Channels |",
        "|---|---:|",
    ]
    lines += [f"| {site} | {n} |" for site, n in by_site.most_common()]
    if partial:
        lines += ["", "## Partial coverage", "", "| Channel | Site | Hours of next 24 |", "|---|---|---:|"]
        for c in sorted(partial, key=lambda c: c.hours_24h):
            lines.append(f"| {names.get(c.tvg_id, c.tvg_id)} | {c.site} | {c.hours_24h} |")
    return "\n".join(lines) + "\n"
