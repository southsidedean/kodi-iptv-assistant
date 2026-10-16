"""Load kia.toml and the playlist sources file."""

from __future__ import annotations

import os
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
class EpgConfig:
    repo_dir: Path = Path("vendor/epg")
    work_dir: Path = Path("work")
    days: int = 3
    max_sites_per_channel: int = 3
    prefer_sites: list[str] = field(default_factory=list)
    exclude_sites: list[str] = field(default_factory=list)
    runner: str = "docker"
    docker_image: str = "node:22-alpine"
    parallel_sites: int = 4
    max_connections: int = 5
    placeholders: bool = True


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
    public_url: str | None = None
    dns_resolvers: tuple[str, ...] = ("8.8.8.8", "1.1.1.1")
    alert_command: str | None = None
    epg: EpgConfig = field(default_factory=EpgConfig)


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
        concurrency=_env_int("KIA_CHECK_CONCURRENCY", check.get("concurrency", 20)),
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
        epg_url=output.get("epg_url") or _derive_epg_url(output),
        public_url=_public_url(output),
        alert_command=data.get("alerts", {}).get("command") or None,
        dns_resolvers=tuple(data.get("dns", {}).get("verify_resolvers", ("8.8.8.8", "1.1.1.1"))),
        epg=_load_epg(data.get("epg", {}), base),
    )


def _env_int(name: str, fallback: int) -> int:
    """A positive integer from the environment (e.g. Docker Compose's .env), else the fallback."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r}: expected a whole number") from None
    if value < 1:
        raise ValueError(f"{name}={raw!r}: must be 1 or more")
    return value


def _public_url(output: dict) -> str | None:
    """Base URL where the output folder is served; KIA_PUBLIC_URL (set by Docker Compose) wins."""
    url = os.environ.get("KIA_PUBLIC_URL") or output.get("public_url") or ""
    return url.rstrip("/") or None


def _derive_epg_url(output: dict) -> str | None:
    base = _public_url(output)
    return f"{base}/guide.xml.gz" if base else None


def _load_epg(epg: dict, base: Path) -> EpgConfig:
    d = EpgConfig()
    return EpgConfig(
        repo_dir=base / epg.get("repo_dir", d.repo_dir),
        work_dir=base / epg.get("work_dir", d.work_dir),
        days=_env_int("KIA_EPG_DAYS", epg.get("days", d.days)),
        max_sites_per_channel=epg.get("max_sites_per_channel", d.max_sites_per_channel),
        prefer_sites=epg.get("prefer_sites", []),
        exclude_sites=epg.get("exclude_sites", []),
        # KIA_EPG_RUNNER lets the Docker image (which bundles Node) force "local".
        runner=os.environ.get("KIA_EPG_RUNNER") or epg.get("runner", d.runner),
        docker_image=epg.get("docker_image", d.docker_image),
        parallel_sites=_env_int("KIA_PARALLEL_SITES", epg.get("parallel_sites", d.parallel_sites)),
        max_connections=epg.get("max_connections", d.max_connections),
        placeholders=epg.get("placeholders", d.placeholders),
    )
