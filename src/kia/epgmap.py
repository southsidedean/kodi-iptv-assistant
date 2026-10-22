"""Match curated channels to guide sources in an iptv-org/epg checkout."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from kia.config import EpgConfig
from kia.m3u import xml_attr, xml_text


@dataclass
class SiteChannel:
    site: str
    site_id: str
    lang: str
    xmltv_id: str  # empty when iptv-org hasn't linked the site's channel to a channel id
    name: str
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class Candidate:
    tvg_id: str
    channel: SiteChannel
    exact: bool  # False when only the channel matched, not the feed (e.g. @East vs @SD)
    how: str = "id"  # "id", or "name" when matched by the channel's name


def base_id(xmltv_id: str) -> str:
    """CNN.us@East -> CNN.us"""
    return xmltv_id.partition("@")[0]


def country(xmltv_id: str) -> str:
    """CNN.us@East -> us"""
    return base_id(xmltv_id).rpartition(".")[2].lower() if "." in xmltv_id else ""


_SITE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_safe_site(name: str) -> bool:
    """Site names look like domains (pluto.tv); anything else could be a path."""
    return bool(_SITE_NAME_RE.match(name)) and ".." not in name


def load_site_channels(repo_dir: Path) -> list[SiteChannel]:
    """Every channel in sites/*/*.channels.xml, including ones without an xmltv_id."""
    found = []
    for path in sorted((repo_dir / "sites").glob("*/*.channels.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for el in root.iter("channel"):
            if not el.get("site") or not el.get("site_id"):
                continue
            if not is_safe_site(el.get("site", "")):
                continue  # used as a file name; never let one reach outside work/
            extra = {k: v for k, v in el.attrib.items() if k not in ("site", "site_id", "lang", "xmltv_id")}
            found.append(
                SiteChannel(
                    site=el.get("site", ""),
                    site_id=el.get("site_id", ""),
                    lang=el.get("lang", ""),
                    xmltv_id=(el.get("xmltv_id") or "").strip(),
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


# Name matching. Guide sites list many channels iptv-org hasn't linked to an id, and some
# playlist ids match no site (a channel was renamed, say). Matching by name finds guides
# for those, but a wrong match is worse than none: the guide would show another channel's
# programs. So only an exact name match counts, in the same country, and only when the
# name belongs to one channel. Looser matches are only suggested in the report.
_TAG_RE = re.compile(r"\s*(\(\d{3,4}[pi]\)|\[[^\]]*\])")
_LOOSE_WORDS = {"hd", "tv", "channel", "network", "the", "us", "usa", "fhd", "uhd", "4k", "sd"}


def _words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _TAG_RE.sub("", name).lower().replace("+", " plus ").replace("&", " and "))


def name_key(name: str) -> str:
    """'News 12 New Jersey' and 'News12 New Jersey' match; 'News12+ CT' and 'News 12 CT' don't."""
    return "".join(_words(name))


def loose_key(name: str) -> str:
    words = _words(name)
    return "".join(w for w in words if w not in _LOOSE_WORDS) or "".join(words)


@dataclass
class Suggestion:
    """A looser name match, listed in the report but not used."""

    tvg_id: str
    name: str
    site: str
    site_name: str
    channel: SiteChannel | None = None


def find_candidates(
    tvg_ids: list[str],
    site_channels: list[SiteChannel],
    cfg: EpgConfig,
    skip_sites: set[str] = frozenset(),
    names: dict[str, str] | None = None,
    aliases: dict[str, list[str]] | None = None,
) -> dict[str, list[Candidate]]:
    """Up to cfg.max_sites_per_channel guide sources per tvg-id, best guesses first.

    Ids match first, ignoring case: the same feed, then another feed of the channel. With
    `names` (tvg-id -> channel name) and cfg.name_matching on, channels no id matches are
    then matched by exact name (see name_key). `aliases` (tvg-id -> the ids the same
    channel has in other playlists) are tried after the channel's own id.
    """
    usable = usable_channels(site_channels, cfg, skip_sites)
    by_id: dict[str, list[SiteChannel]] = defaultdict(list)
    by_base: dict[str, list[SiteChannel]] = defaultdict(list)
    for sc in usable:
        if sc.xmltv_id:
            by_id[sc.xmltv_id.lower()].append(sc)
            by_base[base_id(sc.xmltv_id).lower()].append(sc)

    raw: dict[str, list[Candidate]] = {}
    for tid in tvg_ids:
        if not tid:
            continue
        ids = [tid, *(aliases or {}).get(tid, [])]
        exact = next((i for i in ids if by_id.get(i.lower())), None)
        if exact:
            matches = [Candidate(tid, sc, True) for sc in by_id[exact.lower()]]
        else:
            feed = next((i for i in ids if by_base.get(base_id(i).lower())), None)
            matches = [Candidate(tid, sc, False) for sc in by_base.get(base_id(feed).lower(), [])] if feed else []
        if matches:
            raw[tid] = _one_per_site(matches)

    if names and cfg.name_matching:
        raw.update(match_by_name(tvg_ids, names, usable, set(raw), set(cfg.ignore_name_matches)))

    # Sites that cover many of our channels tend to be the maintained, general-purpose ones.
    popularity = Counter(c.channel.site for cands in raw.values() for c in cands)
    prefer = {site: i for i, site in enumerate(cfg.prefer_sites)}

    def rank(c: Candidate) -> tuple:
        return (prefer.get(c.channel.site, len(prefer)), -popularity[c.channel.site], c.channel.site)

    return {tid: sorted(cands, key=rank)[: cfg.max_sites_per_channel] for tid, cands in raw.items()}


def radio_schedule_candidates(radio: dict[str, str], skip_sites: set[str]) -> dict[str, list[Candidate]]:
    """Radio stations (tvg-id -> name) with a schedule kia reads itself (native.RADIO_SCHEDULES)."""
    from kia.native import RADIO_SCHEDULES

    found = {}
    for tid, name in radio.items():
        site_id = RADIO_SCHEDULES.get(name_key(name))
        if tid and site_id and site_id[0] not in skip_sites:
            found[tid] = [Candidate(tid, SiteChannel(site_id[0], site_id[1], "", "", name), True, "schedule")]
    return found


def usable_channels(site_channels: list[SiteChannel], cfg: EpgConfig, skip_sites: set[str]) -> list[SiteChannel]:
    skip = set(skip_sites) | set(cfg.exclude_sites)
    return [sc for sc in site_channels if sc.site not in skip]


def _one_per_site(matches: list[Candidate]) -> list[Candidate]:
    one: dict[str, Candidate] = {}
    for c in matches:
        one.setdefault(c.channel.site, c)
    return list(one.values())


def _by_name(site_channels: list[SiteChannel], key) -> dict[tuple[str, str], list[SiteChannel]]:
    """(country, key(name)) -> site channels. A channel without an id takes its site's main country."""
    seen: dict[str, Counter] = defaultdict(Counter)
    for sc in site_channels:
        if sc.xmltv_id:
            seen[sc.site][country(sc.xmltv_id)] += 1
    site_country = {site: counts.most_common(1)[0][0] for site, counts in seen.items()}
    index: dict[tuple[str, str], list[SiteChannel]] = defaultdict(list)
    for sc in site_channels:
        c = country(sc.xmltv_id) if sc.xmltv_id else site_country.get(sc.site, "")
        k = key(sc.name)
        if c and k:
            index[c, k].append(sc)
    return index


def match_by_name(
    tvg_ids: list[str],
    names: dict[str, str],
    site_channels: list[SiteChannel],
    already: set[str],
    ignore: set[str],
) -> dict[str, list[Candidate]]:
    """Exact-name matches for channels not in `already`: same country, one channel only."""
    index = _by_name(site_channels, name_key)
    found = {}
    for tid in tvg_ids:
        if not tid or tid in already or tid in ignore or tid not in names:
            continue
        rows = index.get((country(tid), name_key(names[tid])), [])
        # Two different channel ids behind one name: there's no telling which is ours.
        if not rows or len({sc.xmltv_id for sc in rows if sc.xmltv_id}) > 1:
            continue
        found[tid] = _one_per_site([Candidate(tid, sc, True, "name") for sc in rows])
    return found


def suggest_by_name(
    tvg_ids: list[str], names: dict[str, str], site_channels: list[SiteChannel], matched: set[str]
) -> list[Suggestion]:
    """Looser name matches for still-unmatched channels, for a person to check."""
    index = _by_name(site_channels, loose_key)
    out = []
    for tid in tvg_ids:
        if not tid or tid in matched or tid not in names:
            continue
        rows = index.get((country(tid), loose_key(names[tid])), [])
        if rows and len({sc.xmltv_id or name_key(sc.name) for sc in rows}) == 1:
            out.append(Suggestion(tid, names[tid], rows[0].site, rows[0].name, rows[0]))
    return out


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
            attr_text = " ".join(f"{k}={xml_attr(v)}" for k, v in attrs.items())
            lines.append(f"  <channel {attr_text}>{xml_text(sc.name)}</channel>")
        lines.append("</channels>")
        (out_dir / f"{site}.channels.xml").write_text("\n".join(lines) + "\n")
    return {site: len(cands) for site, cands in sorted(by_site.items())}


def _cell(text: str) -> str:
    return text.replace("|", "/")


def render_report(
    curated: list[tuple[str, str]],
    candidates: dict[str, list[Candidate]],
    per_site: dict[str, int],
    skipped_sites: set[str],
    suggestions: list[Suggestion] = (),
    own_guides: dict[str, list[str]] | None = None,
) -> str:
    """curated: (tvg_id, name) pairs from the curated playlist. own_guides: tvg-id -> the
    playlist guide sites that cover it (see kia.playlistguide)."""
    own_guides = own_guides or {}
    by_name = {tid: c for tid, c in candidates.items() if c[0].how == "name"}
    exact = sum(1 for tid, c in candidates.items() if c[0].exact and tid not in by_name)
    unmatched = [(tid, name) for tid, name in curated if tid not in candidates and tid not in own_guides]
    own_per_site = Counter(site for sites in own_guides.values() for site in sites)
    names = dict(curated)
    lines = [
        "# Guide mapping report",
        "",
        "| | Channels |",
        "|---|---:|",
        f"| Curated channels | {len(curated)} |",
        f"| Matched to a guide site (same feed) | {exact} |",
        f"| Matched to a different feed of the channel | {len(candidates) - exact - len(by_name)} |",
        f"| Matched by name | {len(by_name)} |",
        f"| Covered by their playlist's own guide | {len(own_guides)} |",
        f"| No guide source found | {len(unmatched)} |",
        "",
    ]
    if skipped_sites:
        lines += [f"Skipped sites marked broken or excluded: {', '.join(sorted(skipped_sites))}", ""]
    lines += ["## Sites to grab", "", "| Site | Channels |", "|---|---:|"]
    all_sites = {**per_site, **own_per_site}
    lines += [f"| {site} | {n} |" for site, n in sorted(all_sites.items(), key=lambda kv: -kv[1])]
    if by_name:
        lines += [
            "",
            "## Matched by name",
            "",
            "No guide site lists these channels' ids, so kia matched them by name. If one shows "
            "another channel's programs, add its tvg-id to `ignore_name_matches` under `[epg]` in kia.toml.",
            "",
            "| Channel | tvg-id | Guide site | Name there |",
            "|---|---|---|---|",
        ]
        for tid, cands in sorted(by_name.items(), key=lambda kv: names.get(kv[0], kv[0]).casefold()):
            c = cands[0].channel
            lines.append(f"| {_cell(names.get(tid, tid))} | `{tid}` | {c.site} | {_cell(c.name)} |")
    if suggestions:
        lines += [
            "",
            "## Possible matches (not used)",
            "",
            "Looser name matches, listed for a person to check. kia doesn't use them unless you add the "
            "tvg-id to `accept_name_matches` under `[epg]` in kia.toml.",
            "",
            "| Channel | tvg-id | Guide site | Name there |",
            "|---|---|---|---|",
        ]
        lines += [f"| {_cell(s.name)} | `{s.tvg_id}` | {s.site} | {_cell(s.site_name)} |" for s in suggestions]
    lines += ["", "## Channels with no guide source", "", "| Channel | tvg-id |", "|---|---|"]
    lines += [f"| {_cell(name)} | `{tid or '-'}` |" for tid, name in unmatched]
    return "\n".join(lines) + "\n"
