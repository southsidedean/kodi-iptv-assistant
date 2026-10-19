"""Match curated channels to guide sources in an iptv-org/epg checkout."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from kia.config import EpgConfig


@dataclass
class SiteChannel:
    site: str
    site_id: str
    lang: str
    xmltv_id: str
    name: str
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class Candidate:
    tvg_id: str
    channel: SiteChannel
    exact: bool  # False when only the channel matched, not the feed (e.g. @East vs @SD)


def base_id(xmltv_id: str) -> str:
    """CNN.us@East -> CNN.us"""
    return xmltv_id.partition("@")[0]


_SITE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_safe_site(name: str) -> bool:
    """Site names look like domains (pluto.tv); anything else could be a path."""
    return bool(_SITE_NAME_RE.match(name)) and ".." not in name


def load_site_channels(repo_dir: Path) -> list[SiteChannel]:
    """Every channel in sites/*/*.channels.xml that has an xmltv_id."""
    found = []
    for path in sorted((repo_dir / "sites").glob("*/*.channels.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for el in root.iter("channel"):
            xmltv_id = (el.get("xmltv_id") or "").strip()
            if not xmltv_id or not el.get("site") or not el.get("site_id"):
                continue
            if not is_safe_site(el.get("site", "")):
                continue  # used as a file name; never let one reach outside work/
            extra = {k: v for k, v in el.attrib.items() if k not in ("site", "site_id", "lang", "xmltv_id")}
            found.append(
                SiteChannel(
                    site=el.get("site", ""),
                    site_id=el.get("site_id", ""),
                    lang=el.get("lang", ""),
                    xmltv_id=xmltv_id,
                    name=(el.text or "").strip(),
                    attrs=extra,
                )
            )
    return found


_SITE_ROW_RE = re.compile(r'<a href="sites/([^"]+)">.*?<td align="center">([^<]*)</td>')


def broken_sites(repo_dir: Path) -> set[str]:
    """Sites flagged 🔴 in the epg repo's SITES.md."""
    path = repo_dir / "SITES.md"
    if not path.exists():
        return set()
    return {site for site, status in _SITE_ROW_RE.findall(path.read_text()) if "🔴" in status}


def find_candidates(
    tvg_ids: list[str],
    site_channels: list[SiteChannel],
    cfg: EpgConfig,
    skip_sites: set[str] = frozenset(),
) -> dict[str, list[Candidate]]:
    """Up to cfg.max_sites_per_channel guide sources per tvg-id, best guesses first."""
    skip = set(skip_sites) | set(cfg.exclude_sites)
    by_id: dict[str, list[SiteChannel]] = defaultdict(list)
    by_base: dict[str, list[SiteChannel]] = defaultdict(list)
    for sc in site_channels:
        if sc.site in skip:
            continue
        by_id[sc.xmltv_id].append(sc)
        by_base[base_id(sc.xmltv_id)].append(sc)

    raw: dict[str, list[Candidate]] = {}
    for tid in tvg_ids:
        if not tid:
            continue
        if by_id.get(tid):
            matches = [Candidate(tid, sc, True) for sc in by_id[tid]]
        else:
            matches = [Candidate(tid, sc, False) for sc in by_base.get(base_id(tid), [])]
        one_per_site: dict[str, Candidate] = {}
        for c in matches:
            one_per_site.setdefault(c.channel.site, c)
        if one_per_site:
            raw[tid] = list(one_per_site.values())

    # Sites that cover many of our channels tend to be the maintained, general-purpose ones.
    popularity = Counter(c.channel.site for cands in raw.values() for c in cands)
    prefer = {site: i for i, site in enumerate(cfg.prefer_sites)}

    def rank(c: Candidate) -> tuple:
        return (prefer.get(c.channel.site, len(prefer)), -popularity[c.channel.site], c.channel.site)

    return {tid: sorted(cands, key=rank)[: cfg.max_sites_per_channel] for tid, cands in raw.items()}


def write_channel_files(candidates: dict[str, list[Candidate]], out_dir: Path) -> dict[str, int]:
    """One <site>.channels.xml per site, with xmltv_id rewritten to the playlist's tvg-id."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.channels.xml"):
        old.unlink()
    by_site: dict[str, list[Candidate]] = defaultdict(list)
    for cands in candidates.values():
        for c in cands:
            by_site[c.channel.site].append(c)
    for site, cands in by_site.items():
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<channels>"]
        for c in sorted(cands, key=lambda c: c.tvg_id):
            sc = c.channel
            attrs = {"site": sc.site, "site_id": sc.site_id, "lang": sc.lang, "xmltv_id": c.tvg_id, **sc.attrs}
            attr_text = " ".join(f"{k}={quoteattr(v)}" for k, v in attrs.items())
            lines.append(f"  <channel {attr_text}>{escape(sc.name)}</channel>")
        lines.append("</channels>")
        (out_dir / f"{site}.channels.xml").write_text("\n".join(lines) + "\n")
    return {site: len(cands) for site, cands in sorted(by_site.items())}


def render_report(
    curated: list[tuple[str, str]],
    candidates: dict[str, list[Candidate]],
    per_site: dict[str, int],
    skipped_sites: set[str],
) -> str:
    """curated: (tvg_id, name) pairs from the curated playlist."""
    exact = sum(1 for c in candidates.values() if c[0].exact)
    unmatched = [(tid, name) for tid, name in curated if tid not in candidates]
    lines = [
        "# Guide mapping report",
        "",
        "| | Channels |",
        "|---|---:|",
        f"| Curated channels | {len(curated)} |",
        f"| Matched to a guide site (same feed) | {exact} |",
        f"| Matched to a different feed of the channel | {len(candidates) - exact} |",
        f"| No guide source found | {len(unmatched)} |",
        "",
    ]
    if skipped_sites:
        lines += [f"Skipped sites marked broken or excluded: {', '.join(sorted(skipped_sites))}", ""]
    lines += ["## Sites to grab", "", "| Site | Channels |", "|---|---:|"]
    lines += [f"| {site} | {n} |" for site, n in sorted(per_site.items(), key=lambda kv: -kv[1])]
    lines += ["", "## Channels with no guide source", "", "| Channel | tvg-id |", "|---|---|"]
    lines += [f"| {name.replace('|', '/')} | `{tid or '-'}` |" for tid, name in unmatched]
    return "\n".join(lines) + "\n"
