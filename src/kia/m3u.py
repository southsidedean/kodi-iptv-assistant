"""Parse and write extended M3U playlists (the iptv-org / Kodi IPTV Simple dialect)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

# Characters XML doesn't allow, which have no place in a name or address anyway. One of
# them in a channel name from someone's playlist would make the whole guide unreadable.
# Half a surrogate pair (\ud800 to \udfff) can come from JSON ("\ud83d" alone, in a
# station name); it can't even be written as UTF-8.
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff\ufffe\uffff]")


def xml_text(text: str) -> str:
    """Third-party text, safe inside an XML element."""
    return escape(CONTROL.sub("", text))


def xml_attr(text: str) -> str:
    """Third-party text as a quoted XML attribute."""
    return quoteattr(CONTROL.sub("", text))

_ATTR_RE = re.compile(r'\s*([A-Za-z0-9_-]+)="([^"]*)"')
_EXTINF_RE = re.compile(r"#EXTINF:\s*(-?\d+(?:\.\d+)?)(.*)")
_QUALITY_RE = re.compile(r"\((\d{3,4})p\)")
# The tags playlists add to a channel's name, such as (720p), (1080i), and [Geo-blocked]:
# left out where names are compared or shown in the guide.
TAG_RE = re.compile(r"\s*(\(\d{3,4}[pi]\)|\[[^\]]*\])")
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
    # A channel number you chose (tvg-chno in your own playlist file); see kia.numbers.
    pinned_number: int | None = None
    pinned_by: str = "file"  # "file": your own playlist file; "page": the Dashboard
    # The guides the entry's playlist names in its header (x-tvg-url); see kia.playlistguide.
    guide_urls: tuple[str, ...] = ()

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
    def is_radio(self) -> bool:
        return self.attrs.get("radio", "").lower() == "true"

    @property
    def key(self) -> str:
        """Identity used to group duplicate streams of the same channel."""
        if self.is_radio:
            # Radio directories list the same station several times (one per bitrate).
            return "radio:" + normalize_name(self.name)
        return self.tvg_id or "name:" + normalize_name(self.name)


# #KODIPROP lines tell Kodi how to play a stream (e.g. DASH through inputstream.adaptive).
# Playlists are third-party content, so only these are copied into the curated list.
_KODIPROP_RE = re.compile(r"^(inputstream|inputstreamaddon|mimetype|inputstream\.(adaptive|ffmpegdirect)\.[a-z_]+)=", re.I)


def is_allowed_kodiprop(prop: str) -> bool:
    return bool(_KODIPROP_RE.match(prop.strip()))


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
    name = name[1:] if name.startswith(",") else name.partition(",")[2]
    return duration, attrs, name.strip()


def header_guide_urls(text: str) -> list[str]:
    """The guide addresses in a playlist's #EXTM3U line (x-tvg-url or url-tvg; can be a
    comma-separated list). Only http(s) addresses are kept."""
    first = text.removeprefix("\ufeff").lstrip().partition("\n")[0].strip()
    if not first.startswith("#EXTM3U"):
        return []
    attrs = dict(_ATTR_RE.findall(first[len("#EXTM3U"):]))
    value = attrs.get("x-tvg-url") or attrs.get("url-tvg") or ""
    urls = [u.strip() for u in re.split(r"[,\s]+", value) if u.strip()]
    return [u for u in urls if re.match(r"https?://", u, re.I)]


def parse(text: str, source: str = "") -> list[Entry]:
    entries: list[Entry] = []
    pending: Entry | None = None
    for raw in text.removeprefix("\ufeff").splitlines():
        line = CONTROL.sub("", raw).strip()
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
            prop = line[len("#KODIPROP:"):]
            if pending is not None and is_allowed_kodiprop(prop):
                pending.kodiprops.append(prop)
        elif line.startswith("#"):
            continue
        else:
            entry = pending or Entry(name=line, url="", source=source)
            entry.url = line
            entries.append(entry)
            pending = None
    return entries


def _clean(text: str, quote: bool = False) -> str:
    """One line of third-party text: no line breaks (which would start a new playlist
    line, even a channel that was never checked), and in attributes no double quotes."""
    text = CONTROL.sub("", " ".join(str(text).splitlines()).replace("\r", " "))
    return text.replace('"', "'") if quote else text


def write(entries: list[Entry], epg_url: str | None = None) -> str:
    header = "#EXTM3U"
    if epg_url:
        header += f' x-tvg-url="{_clean(epg_url, True)}"'
    lines = [header]
    for e in entries:
        attrs = "".join(f' {_clean(k, True)}="{_clean(v, True)}"' for k, v in e.attrs.items())
        lines.append(f"#EXTINF:{_clean(e.duration)}{attrs},{_clean(e.name)}")
        lines.extend(f"#EXTVLCOPT:{_clean(k)}={_clean(v)}" for k, v in e.vlcopts.items())
        lines.extend(f"#KODIPROP:{_clean(p)}" for p in e.kodiprops)
        lines.append(_clean(e.url))
    return "\n".join(lines) + "\n"
