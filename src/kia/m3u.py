"""Parse and write extended M3U playlists (the iptv-org / Kodi IPTV Simple dialect)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ATTR_RE = re.compile(r'\s*([A-Za-z0-9_-]+)="([^"]*)"')
_EXTINF_RE = re.compile(r"#EXTINF:\s*(-?\d+(?:\.\d+)?)(.*)")
_QUALITY_RE = re.compile(r"\((\d{3,4})p\)")
_LABEL_RE = re.compile(r"\[([^\]]+)\]")


@dataclass
class Entry:
    name: str
    url: str
    attrs: dict[str, str] = field(default_factory=dict)
    vlcopts: dict[str, str] = field(default_factory=dict)
    kodiprops: list[str] = field(default_factory=list)
    duration: str = "-1"
    source: str = ""

    @property
    def tvg_id(self) -> str:
        return self.attrs.get("tvg-id", "")

    @property
    def group(self) -> str:
        return self.attrs.get("group-title", "")

    @property
    def user_agent(self) -> str | None:
        return self.vlcopts.get("http-user-agent") or self.attrs.get("http-user-agent") or None

    @property
    def referrer(self) -> str | None:
        return self.vlcopts.get("http-referrer") or self.attrs.get("http-referrer") or None

    @property
    def labels(self) -> list[str]:
        """Bracketed tags iptv-org appends to names, e.g. ["Geo-blocked", "Not 24/7"]."""
        return _LABEL_RE.findall(self.name)

    @property
    def advertised_height(self) -> int | None:
        m = _QUALITY_RE.search(self.name)
        return int(m.group(1)) if m else None

    @property
    def key(self) -> str:
        """Identity used to group duplicate streams of the same channel."""
        return self.tvg_id or "name:" + normalize_name(self.name)


def normalize_name(name: str) -> str:
    name = _QUALITY_RE.sub("", name)
    name = _LABEL_RE.sub("", name)
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _parse_extinf(line: str) -> tuple[str, dict[str, str], str]:
    m = _EXTINF_RE.match(line)
    if not m:
        return "-1", {}, line.partition(",")[2].strip()
    duration, rest = m.group(1), m.group(2)
    attrs: dict[str, str] = {}
    pos = 0
    while am := _ATTR_RE.match(rest, pos):
        attrs[am.group(1)] = am.group(2)
        pos = am.end()
    name = rest[pos:].strip()
    if name.startswith(","):
        name = name[1:]
    else:
        name = name.partition(",")[2]
    return duration, attrs, name.strip()


def parse(text: str, source: str = "") -> list[Entry]:
    entries: list[Entry] = []
    pending: Entry | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF"):
            duration, attrs, name = _parse_extinf(line)
            pending = Entry(name=name, url="", attrs=attrs, duration=duration, source=source)
        elif line.startswith("#EXTVLCOPT:"):
            key, _, value = line[len("#EXTVLCOPT:"):].partition("=")
            if pending is not None:
                pending.vlcopts[key.strip()] = value.strip()
        elif line.startswith("#KODIPROP:"):
            if pending is not None:
                pending.kodiprops.append(line[len("#KODIPROP:"):])
        elif line.startswith("#"):
            continue
        else:
            entry = pending or Entry(name=line, url="", source=source)
            entry.url = line
            entries.append(entry)
            pending = None
    return entries


def write(entries: list[Entry], epg_url: str | None = None) -> str:
    header = "#EXTM3U"
    if epg_url:
        header += f' x-tvg-url="{epg_url}"'
    lines = [header]
    for e in entries:
        attrs = "".join(f' {k}="{v}"' for k, v in e.attrs.items())
        lines.append(f"#EXTINF:{e.duration}{attrs},{e.name}")
        lines.extend(f"#EXTVLCOPT:{k}={v}" for k, v in e.vlcopts.items())
        lines.extend(f"#KODIPROP:{p}" for p in e.kodiprops)
        lines.append(e.url)
    return "\n".join(lines) + "\n"
