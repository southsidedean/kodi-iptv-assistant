"""Profiles: playlists for a person or a device, made from the full channel list.

A profile is a named set of rules in profiles/profiles.toml, for example a "kids" playlist
with only the Kids and Animation categories. kia writes each one as <name>.m3u next to
curated.m3u after every check, with its own guide, <name>.guide.xml.gz, holding only its
channels (a small guide loads much faster on a slow device). A channel has the same number
in every playlist, unless a profile says renumber = true.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import replace
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from kia.m3u import Entry

# Names already used by files in the output folder.
RESERVED = {"curated", "guide", "index", "status", "report", "guide-report", "mapping-report"}
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


@dataclass
class Profile:
    name: str
    title: str = ""
    # What to include. When none of these is set, the profile starts from every channel.
    groups: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)  # tvg-ids, always included
    # What to take out.
    exclude_groups: list[str] = field(default_factory=list)
    exclude_names: list[str] = field(default_factory=list)
    exclude_channels: list[str] = field(default_factory=list)  # tvg-ids, always left out
    tv: bool = True
    radio: bool = True
    # Number this playlist's channels 1, 2, 3... instead of using the full list's numbers.
    renumber: bool = False
    # Write ’ for ' so Threadfin reads the playlist right (it turns ' into ", which breaks the line).
    threadfin: bool = False

    @property
    def label(self) -> str:
        return self.title or self.name.replace("-", " ").capitalize()

    @property
    def file(self) -> str:
        return f"{self.name}.m3u"

    @property
    def guide(self) -> str:
        return f"{self.name}.guide.xml.gz"


# A playlist kia always makes when there's radio: TV only, for players such as Jellyfin
# that list radio stations as TV channels. A profile of your own named "tv" replaces it.
TV_ONLY = Profile("tv", title="TV only", radio=False)

LISTS = ("groups", "sources", "names", "channels", "exclude_groups", "exclude_names", "exclude_channels")
FLAGS = ("tv", "radio")  # on unless set to false
SWITCHES = ("renumber", "threadfin")  # off unless set to true


def load(path: Path) -> list[Profile]:
    """The profiles in a profiles file, or none if it doesn't exist."""
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text())
    return [from_rules(name, rules, path.name) for name, rules in data.items()]


def from_rules(name: str, rules: object, where_file: str = "profile") -> Profile:
    """A checked Profile from a [name] section's settings (from the file or the Dashboard)."""
    where = f"{where_file}, [{name}]"
    if not isinstance(rules, dict):
        raise ValueError(f"{where}: expected a section like [{name}] with settings under it")
    if not isinstance(name, str) or not _NAME_RE.match(name) or name in RESERVED:
        raise ValueError(
            f"{where}: profile names use lowercase letters, digits, and dashes"
            + (f", and {name!r} is already used by kia" if name in RESERVED else "")
        )
    known = set(LISTS) | set(FLAGS) | set(SWITCHES) | {"title"}
    unknown = sorted(set(rules) - known)
    if unknown:
        raise ValueError(f"{where}: unknown setting(s) {unknown}; use {sorted(known)}")
    if not isinstance(rules.get("title", ""), str):
        raise ValueError(f"{where}: title must be text")
    for key in LISTS:
        value = rules.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{where}: {key} must be a list of text, like [\"News\"]")
    for key in FLAGS + SWITCHES:
        if not isinstance(rules.get(key, key in FLAGS), bool):
            raise ValueError(f"{where}: {key} must be true or false")
    for key in ("names", "exclude_names"):
        for pattern in rules.get(key, []):
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{where}: {key} pattern {pattern!r}: {exc}") from None
    return Profile(name=name, **rules)


def rules(p: Profile) -> dict:
    """A profile's settings, leaving out the ones at their defaults."""
    out: dict = {}
    if p.title:
        out["title"] = p.title
    out.update({k: getattr(p, k) for k in LISTS if getattr(p, k)})
    out.update({k: False for k in FLAGS if not getattr(p, k)})
    out.update({k: True for k in SWITCHES if getattr(p, k)})
    return out


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)  # a TOML basic string uses the same escapes


HEADER = """# Playlists for people and devices. kia's Dashboard edits this file, and you can edit
# it by hand too (the Dashboard rewrites it without comments when it saves).
# Each [section] is a playlist: http://<your address>:8780/<name>.m3u. See docs/profiles.md.
"""


def to_toml(profiles: list[Profile]) -> str:
    parts = [HEADER]
    for p in profiles:
        parts.append(f"\n[{p.name}]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in rules(p).items()))
    return "".join(parts)


def save(path: Path, profiles: list[Profile]) -> None:
    """Write the profiles file in one step, keeping the previous one as <name>.bak."""
    text = to_toml(profiles)
    if [p.name for p in load_text(text)] != [p.name for p in profiles]:
        raise ValueError("the profiles didn't read back the same; nothing was saved")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        (path.with_name(path.name + ".bak")).write_text(path.read_text())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def load_text(text: str) -> list[Profile]:
    return [from_rules(name, r) for name, r in tomllib.loads(text).items()]


def _groups(e: Entry) -> set[str]:
    return {g.strip().casefold() for g in e.group.split(";") if g.strip()}


def _matches(pattern_list: list[str], name: str) -> bool:
    return any(re.search(p, name, re.IGNORECASE) for p in pattern_list)


def select(profile: Profile, items: list[tuple[Entry, set[str]]]) -> list[Entry]:
    """The profile's channels, in playlist order. items: (entry, playlists it came from)."""
    groups = {g.casefold() for g in profile.groups}
    exclude_groups = {g.casefold() for g in profile.exclude_groups}
    sources = set(profile.sources)
    pinned = set(profile.channels)
    left_out = set(profile.exclude_channels)
    includes = bool(groups or sources or profile.names)
    out = []
    for entry, from_sources in items:
        if entry.tvg_id and entry.tvg_id in left_out:
            continue
        if entry.tvg_id and entry.tvg_id in pinned:
            out.append(entry)
            continue
        if includes and not (
            (groups and _groups(entry) & groups)
            or (sources and from_sources & sources)
            or (profile.names and _matches(profile.names, entry.name))
        ):
            continue
        if _groups(entry) & exclude_groups or _matches(profile.exclude_names, entry.name):
            continue
        if (entry.is_radio and not profile.radio) or (not entry.is_radio and not profile.tv):
            continue
        out.append(entry)
    return out


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def save_sources(state_dir: Path, items: list[tuple[Entry, set[str]]]) -> None:
    """Which playlists each kept channel came from, so profiles can be rebuilt without a check."""
    state_dir.mkdir(parents=True, exist_ok=True)
    _write(state_dir / "channel-sources.json", json.dumps({e.key: sorted(s) for e, s in items}, indent=1))


def numbered(profile: Profile, picked: list[Entry]) -> list[Entry]:
    """The profile's channels as its playlist shows them: their numbers, and for Threadfin, ’ for '."""
    if profile.renumber:
        picked = [replace(e, attrs={**e.attrs, "tvg-chno": str(i)}) for i, e in enumerate(picked, 1)]
    if profile.threadfin:
        picked = [replace(e, name=e.name.replace("'", "\u2019"),
                          attrs={k: v if k == "tvg-id" or "://" in v else v.replace("'", "\u2019")
                                 for k, v in e.attrs.items()})
                  for e in picked]  # fmt: skip
    return picked


def write_guides(output_dir: Path, wanted: dict[str, set[str]]) -> set[str]:
    """Write <name>.guide.xml.gz for each profile from guide.xml, in one pass over it.
    wanted: profile name -> its channels' tvg-ids. Returns the names written."""
    full = output_dir / "guide.xml"
    if not wanted or not full.exists():
        return set()
    files = {name: output_dir / f"{name}.guide.xml.gz.tmp" for name in wanted}
    outs = {name: gzip.open(path, "wt", encoding="utf-8", compresslevel=6) for name, path in files.items()}  # noqa: SIM115
    try:
        for f in outs.values():
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="kodi-iptv-assistant">\n')
        context = ET.iterparse(full, events=("start", "end"))
        _, root = next(context)
        for event, el in context:
            if event != "end" or el.tag not in ("channel", "programme"):
                continue
            channel = el.get("id") if el.tag == "channel" else el.get("channel")
            text = None
            for name, ids in wanted.items():
                if channel in ids:
                    if text is None:
                        el.tail = None
                        text = "  " + ET.tostring(el, encoding="unicode") + "\n"
                    outs[name].write(text)
            root.clear()  # keep memory flat on a large guide
        for f in outs.values():
            f.write("</tv>\n")
    finally:
        for f in outs.values():
            f.close()
    for name, path in files.items():
        path.replace(output_dir / f"{name}.guide.xml.gz")
    return set(wanted)


def update_guides(output_dir: Path, wanted: dict[str, set[str]], before: dict[str, str | None],
                  link_tv: bool = False) -> tuple[set[str], dict[str, str]]:  # fmt: skip
    """Bring each profile's guide up to date, doing only the work that's needed: a guide is
    rewritten only when its channels changed or guide.xml is newer, and kia's own TV-only
    guide is the full guide under another name (a hard link: radio has almost no listings,
    and players ignore channels they don't have). Returns (profiles with a guide, the
    digest of each one's channel IDs, to compare next time)."""
    full = output_dir / "guide.xml"
    if not wanted or not full.exists():
        return set(), {}
    digests = {name: hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:16] for name, ids in wanted.items()}
    newest = full.stat().st_mtime
    todo = {}
    for name, ids in wanted.items():
        target = output_dir / f"{name}.guide.xml.gz"
        if link_tv and name == TV_ONLY.name and _link(output_dir / "guide.xml.gz", target):
            continue
        if target.exists() and before.get(name) == digests[name] and target.stat().st_mtime >= newest:
            continue  # the same channels, and guide.xml hasn't changed since
        todo[name] = ids
    write_guides(output_dir, todo)
    return set(wanted), digests


def _link(source: Path, target: Path) -> bool:
    """Make target the same file as source (a hard link), replacing it in one step."""
    if not source.exists():
        return False
    try:
        if target.exists() and os.path.samefile(source, target):
            return True
        tmp = target.with_name(target.name + ".tmp")
        tmp.unlink(missing_ok=True)
        os.link(source, tmp)
        tmp.replace(target)
        return True
    except OSError:
        return False  # a file system without hard links: write a copy as before


def profile_epg_url(epg_url: str | None, p: Profile) -> str | None:
    """The address of the profile's own guide, next to the full one."""
    if epg_url and epg_url.endswith("/guide.xml.gz"):
        return epg_url.removesuffix("guide.xml.gz") + p.guide
    return epg_url


def rebuild(output_dir: Path, state_dir: Path, profiles_file: Path | None, epg_url: str | None, log) -> dict:
    """Write every profile's playlist and guide from curated.m3u and guide.xml, and
    output/profiles.json for the Dashboard; remove those of deleted profiles. A mistake in
    the profiles file leaves the previous playlists in place and is reported in
    profiles.json. Returns profiles.json."""
    from kia.m3u import parse, write

    summary_path = output_dir / "profiles.json"
    try:
        defined = load(profiles_file) if profiles_file else []
    except (ValueError, OSError) as exc:  # tomllib.TOMLDecodeError is a ValueError
        log(f"Profiles not updated: {exc}")
        old = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        result = {**old, "error": str(exc)}
        _write(summary_path, json.dumps(result, indent=1))
        return result
    curated = output_dir / "curated.m3u"
    entries = parse(curated.read_text()) if curated.exists() else []
    try:
        sources = json.loads((state_dir / "channel-sources.json").read_text())
    except (OSError, ValueError):
        sources = {}
    items = [(e, set(sources.get(e.key, [e.source] if e.source else []))) for e in entries]
    builtin = set()
    if any(e.is_radio for e in entries) and TV_ONLY.name not in {p.name for p in defined}:
        defined = [*defined, TV_ONLY]
        builtin.add(TV_ONLY.name)
    picks = {p.name: numbered(p, select(p, items)) for p in defined}
    try:
        before = {p["name"]: p.get("guide_ids") for p in json.loads(summary_path.read_text()).get("profiles", [])}
    except (OSError, ValueError):
        before = {}
    wanted = {name: {e.tvg_id for e in picked if e.tvg_id} for name, picked in picks.items()}
    guides, digests = update_guides(output_dir, wanted, before, link_tv=TV_ONLY.name in builtin)
    summary = []
    for p in defined:
        picked = picks[p.name]
        _write(output_dir / p.file, write(picked, profile_epg_url(epg_url, p) if p.name in guides else epg_url))
        radio_count = sum(1 for e in picked if e.is_radio)
        summary.append({"name": p.name, "title": p.label, "file": p.file, "tv": len(picked) - radio_count,
                        "radio": radio_count, "guide": p.guide if p.name in guides else None,
                        "builtin": p.name in builtin})  # fmt: skip
        if p.name in digests:
            summary[-1]["guide_ids"] = digests[p.name]  # which channels its guide has, to compare next time
        log(f"Profile {p.name}: {len(picked)} channels -> {output_dir / p.file}")
    keep = {"curated.m3u"} | {p.file for p in defined} | {p.guide for p in defined}
    for old_file in [*output_dir.glob("*.m3u"), *output_dir.glob("*.guide.xml.gz")]:
        if old_file.name not in keep:
            old_file.unlink()
    result = {"profiles": summary}
    _write(summary_path, json.dumps(result, indent=1))
    return result
