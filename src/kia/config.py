"""Load kia.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


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


def load(path: Path) -> Config:
    data = tomllib.loads(path.read_text())
    base = path.parent.resolve()
    check = data.get("check", {})
    filters = data.get("filters", {})
    output = data.get("output", {})
    sources = [Source(name=s["name"], url=s["url"]) for s in data.get("sources", [])]
    if not sources:
        raise ValueError(f"{path}: no [[sources]] defined")
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
