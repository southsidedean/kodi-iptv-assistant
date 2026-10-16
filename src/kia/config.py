"""Load kia.toml and the playlist sources file."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class Source:
    name: str
    url: str


@dataclass
class Config:
    sources: list[Source]
    concurrency: int = 20
    timeout: float = 15.0
    require_video: bool = True
    grace_failures: int = 2
    user_agent: str | None = None
    ffprobe: str = "ffprobe"
    exclude_groups: list[str] = field(default_factory=list)
    exclude_labels: list[str] = field(default_factory=list)
    exclude_name_patterns: list[str] = field(default_factory=list)
    output_dir: Path = Path("output")
    state_dir: Path = Path("state")
    epg_url: str | None = None


def source_name(url: str, taken: set[str]) -> str:
    """Derive a short name from a playlist URL: .../countries/us.m3u -> "us"."""
    stem = Path(urlparse(url).path).stem or urlparse(url).netloc or "source"
    name, n = stem, 2
    while name in taken:
        name, n = f"{stem}-{n}", n + 1
    return name


def parse_sources(text: str) -> list[Source]:
    """Parse a sources file: one playlist URL or path per line, optionally followed by a name.

    Blank lines and anything after a ``#`` that starts a line or follows whitespace are ignored.
    """
    sources: list[Source] = []
    taken: set[str] = set()
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*", "", raw).strip()
        if not line:
            continue
        url, _, name = line.partition(" ")
        name = name.strip() or source_name(url, taken)
        if name in taken:
            raise ValueError(f"duplicate source name {name!r}")
        taken.add(name)
        sources.append(Source(name=name, url=url))
    return sources


def load(path: Path, sources_file: Path | None = None, urls: list[str] | None = None) -> Config:
    """Read kia.toml. ``sources_file`` overrides the file it names; ``urls`` overrides both."""
    data = tomllib.loads(path.read_text())
    base = path.parent.resolve()
    check = data.get("check", {})
    filters = data.get("filters", {})
    output = data.get("output", {})
    if urls:
        sources = parse_sources("\n".join(urls))
        origin = "--url"
    else:
        sources_file = sources_file or base / data.get("sources_file", "sources.txt")
        sources = parse_sources(sources_file.read_text())
        origin = str(sources_file)
    if not sources:
        raise ValueError(f"{origin}: no playlist sources listed")
    return Config(
        sources=sources,
        concurrency=check.get("concurrency", 20),
        timeout=check.get("timeout", 15.0),
        require_video=check.get("require_video", True),
        grace_failures=check.get("grace_failures", 2),
        user_agent=check.get("user_agent") or None,
        ffprobe=check.get("ffprobe", "ffprobe"),
        exclude_groups=filters.get("exclude_groups", []),
        exclude_labels=filters.get("exclude_labels", []),
        exclude_name_patterns=filters.get("exclude_name_patterns", []),
        output_dir=base / output.get("dir", "output"),
        state_dir=base / output.get("state_dir", "state"),
        epg_url=output.get("epg_url") or None,
    )
