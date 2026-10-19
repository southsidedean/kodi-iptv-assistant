"""Profiles: playlists for a person or a device, made from the full channel list.

A profile is a named set of rules in profiles.local.toml, for example a "kids" playlist
with only the Kids and Animation categories. kia writes each one as <name>.m3u next to
curated.m3u after every check. Profiles share the guide and the channel numbers, so a
channel has the same number in every playlist.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, fields
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
    tv: bool = True
    radio: bool = True

    @property
    def label(self) -> str:
        return self.title or self.name.replace("-", " ").capitalize()

    @property
    def file(self) -> str:
        return f"{self.name}.m3u"


def load(path: Path) -> list[Profile]:
    """The profiles in a profiles file, or none if it doesn't exist."""
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text())
    known = {f.name for f in fields(Profile)} - {"name"}
    profiles = []
    for name, rules in data.items():
        where = f"{path.name}, [{name}]"
        if not isinstance(rules, dict):
            raise ValueError(f"{where}: expected a section like [{name}] with settings under it")
        if not _NAME_RE.match(name) or name in RESERVED:
            raise ValueError(
                f"{where}: profile names use lowercase letters, digits, and dashes"
                + (f", and {name!r} is already used by kia" if name in RESERVED else "")
            )
        unknown = sorted(set(rules) - known)
        if unknown:
            raise ValueError(f"{where}: unknown setting(s) {unknown}; use {sorted(known)}")
        for key in ("names", "exclude_names"):
            for pattern in rules.get(key, []):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"{where}: {key} pattern {pattern!r}: {exc}") from None
        profiles.append(Profile(name=name, **rules))
    return profiles


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
    includes = bool(groups or sources or profile.names)
    out = []
    for entry, from_sources in items:
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
