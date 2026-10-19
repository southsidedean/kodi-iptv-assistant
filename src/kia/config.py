"""Load kia.toml and the playlist sources file."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from kia.alerts import DEFAULT_EVENTS, EVENTS, AlertConfig


@dataclass
class Source:
    name: str
    url: str
    params: dict[str, str] = field(default_factory=dict)

    @property
    def is_radio_browser(self) -> bool:
        return self.url.startswith("radio-browser:")

    @property
    def is_local_file(self) -> bool:
        """A playlist file on this computer (yours), rather than one from the internet."""
        return not self.is_radio_browser and not re.match(r"[a-z][a-z0-9+.-]*://", self.url, re.I)


# How kia identifies itself to stream servers. Servers differ in which players they turn
# away: jmp2.uk (Pluto, Samsung, Plex) refuses Kodi's identity, and some radio servers
# refuse ffmpeg's. VLC's is accepted most widely: in a test of 74 streams that had
# failed, 29 worked as VLC, 16 as ffmpeg, and 11 as Kodi.
DEFAULT_USER_AGENT = "VLC/3.0.20 LibVLC/3.0.20"


@dataclass
class EpgConfig:
    repo_dir: Path = Path("vendor/epg")
    work_dir: Path = Path("work")
    days: int = 3
    max_sites_per_channel: int = 3
    prefer_sites: list[str] = field(default_factory=list)
    exclude_sites: list[str] = field(default_factory=list)
    runner: str = "docker"
    docker_image: str = "node:lts-alpine"
    parallel_sites: int = 4
    max_connections: int = 5
    placeholders: bool = True
    placeholder_hours: int = 2
    radio_placeholders: bool = False
    native_grabbers: bool = True
    name_matching: bool = True
    playlist_guides: bool = True
    site_time_limit: int = 30  # minutes per guide site; 0 = no limit
    site_connections: dict[str, int] = field(default_factory=dict)
    site_delay_ms: dict[str, int] = field(default_factory=dict)
    accept_name_matches: list[str] = field(default_factory=list)
    ignore_name_matches: list[str] = field(default_factory=list)
    git_url: str = "https://github.com/iptv-org/epg.git"
    git_ref: str = "latest"  # the repo's default branch


@dataclass
class Config:
    sources: list[Source]
    concurrency: int = 20
    per_host: int = 2
    per_host_limits: dict[str, int] = field(default_factory=dict)
    host_delay_ms: int = 100
    timeout: float = 15.0
    require_video: bool = True
    grace_failures: int = 2
    merge_duplicates: bool = True
    user_agent: str | None = DEFAULT_USER_AGENT
    ffprobe: str = "ffprobe"
    exclude_groups: list[str] = field(default_factory=list)
    exclude_labels: list[str] = field(default_factory=list)
    exclude_name_patterns: list[str] = field(default_factory=list)
    output_dir: Path = Path("output")
    state_dir: Path = Path("state")
    epg_url: str | None = None
    channel_numbers: bool = True
    public_url: str | None = None
    dns_resolvers: tuple[str, ...] = ("8.8.8.8", "1.1.1.1")
    alerts: AlertConfig = field(default_factory=AlertConfig)
    history_days: int = 30
    upstream_url: str | None = None
    upstream_fallback: str = "grab"
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
    # Files saved by Windows Notepad can start with a byte-order mark.
    for raw in text.removeprefix("\ufeff").splitlines():
        line = re.sub(r"(^|\s)#.*", "", raw).strip()
        if not line:
            continue
        if line.startswith("radio-browser:"):
            source = _radio_source(line, taken)
        else:
            url, _, name = line.partition(" ")
            source = Source(name=name.strip() or source_name(url, taken), url=url)
        if source.name in taken:
            raise ValueError(f"duplicate source name {source.name!r}")
        taken.add(source.name)
        sources.append(source)
    return sources


def _radio_source(line: str, taken: set[str]) -> Source:
    """`radio-browser: country=US top=200 tag=jazz name=jazz` -> a radio-browser query."""
    params = {}
    for token in line.removeprefix("radio-browser:").split():
        key, eq, value = token.partition("=")
        if not eq or not value:
            raise ValueError(f"radio-browser: expected key=value, got {token!r}")
        params[key.strip().lower()] = value.strip()
    name = params.pop("name", None)
    if not name:
        base = "radio-" + "-".join(params[k].lower() for k in ("country", "tag", "language", "station") if k in params)
        name, n = base.rstrip("-") or "radio", 2
        while name in taken:
            name, n = f"{base}-{n}", n + 1
    from kia.radio import build_query

    build_query(params)  # check the filters now, not at 4 AM
    return Source(name=name, url=line, params=params)


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
        # KIA_SOURCES_FILE lets a server use its own list (e.g. sources.local.txt, which git
        # ignores) so updating kia never conflicts with your choices.
        named = os.environ.get("KIA_SOURCES_FILE") or data.get("sources_file", "sources.txt")
        sources_file = sources_file or base / named
        sources = parse_sources(sources_file.read_text())
        origin = str(sources_file)
    if not sources:
        raise ValueError(f"{origin}: no playlist sources listed")
    return Config(
        sources=sources,
        concurrency=_env_int("KIA_CHECK_CONCURRENCY", check.get("concurrency", 20)),
        timeout=check.get("timeout", 15.0),
        per_host=check.get("per_host", 2),
        per_host_limits=dict(check.get("per_host_limits", {})),
        host_delay_ms=check.get("host_delay_ms", 100),
        require_video=check.get("require_video", True),
        grace_failures=check.get("grace_failures", 2),
        merge_duplicates=check.get("merge_duplicates", True),
        user_agent=check.get("user_agent", DEFAULT_USER_AGENT) or None,
        ffprobe=check.get("ffprobe", "ffprobe"),
        exclude_groups=_merge_env_list("KIA_EXCLUDE_GROUPS", filters.get("exclude_groups", [])),
        exclude_labels=filters.get("exclude_labels", []),
        exclude_name_patterns=filters.get("exclude_name_patterns", []),
        output_dir=base / output.get("dir", "output"),
        state_dir=base / output.get("state_dir", "state"),
        epg_url=output.get("epg_url") or _derive_epg_url(output),
        channel_numbers=output.get("channel_numbers", True),
        public_url=_public_url(output),
        alerts=_load_alerts(data.get("alerts", {})),
        history_days=check.get("history_days", 30),
        upstream_url=_upstream_url(data.get("upstream", {})),
        upstream_fallback=_upstream_fallback(data.get("upstream", {})),
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


def _merge_env_list(name: str, configured: list[str]) -> list[str]:
    """kia.toml's list plus a comma-separated list from the environment (added, not replaced)."""
    extra = [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]
    return configured + [item for item in extra if item not in configured]


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def env_bool(name: str, fallback: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return fallback
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ValueError(f"{name}={raw!r}: expected true or false")


def _placeholder_hours(hours: int) -> int:
    if hours not in (1, 2, 3, 4, 6, 8, 12, 24):
        raise ValueError(f"placeholder_hours = {hours}: must divide a day evenly (1, 2, 3, 4, 6, 8, 12, 24)")
    return hours


def _load_alerts(alerts: dict) -> AlertConfig:
    events = os.environ.get("KIA_ALERT_EVENTS", "").strip()
    chosen = [e.strip() for e in events.split(",") if e.strip()] if events else alerts.get("events", DEFAULT_EVENTS)
    unknown = sorted(set(chosen) - set(EVENTS))
    if unknown:
        raise ValueError(f"unknown alert events {unknown}; choose from {sorted(EVENTS)}")
    return AlertConfig(
        command=os.environ.get("KIA_ALERT_COMMAND") or alerts.get("command") or None,
        ntfy_url=(os.environ.get("KIA_NTFY_URL") or alerts.get("ntfy_url") or "").strip() or None,
        events=tuple(chosen),
        drop_percent=alerts.get("drop_percent", 25),
        site_fail_runs=alerts.get("site_fail_runs", 3),
    )


def _upstream_url(upstream: dict) -> str | None:
    """Another kia server to take the guide from; KIA_UPSTREAM_URL (from .env) wins."""
    url = os.environ.get("KIA_UPSTREAM_URL") or upstream.get("url") or ""
    return url.rstrip("/") or None


def _upstream_fallback(upstream: dict) -> str:
    value = (os.environ.get("KIA_UPSTREAM_FALLBACK") or upstream.get("fallback") or "grab").strip().lower()
    if value not in ("grab", "cached"):
        raise ValueError(f"upstream fallback {value!r}: expected 'grab' or 'cached'")
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
        exclude_sites=_merge_env_list("KIA_EXCLUDE_SITES", epg.get("exclude_sites", [])),
        # KIA_EPG_RUNNER lets the Docker image (which bundles Node) force "local".
        runner=os.environ.get("KIA_EPG_RUNNER") or epg.get("runner", d.runner),
        docker_image=epg.get("docker_image", d.docker_image),
        parallel_sites=_env_int("KIA_PARALLEL_SITES", epg.get("parallel_sites", d.parallel_sites)),
        max_connections=epg.get("max_connections", d.max_connections),
        placeholders=env_bool("KIA_PLACEHOLDERS", epg.get("placeholders", d.placeholders)),
        placeholder_hours=_placeholder_hours(epg.get("placeholder_hours", d.placeholder_hours)),
        radio_placeholders=env_bool("KIA_RADIO_PLACEHOLDERS", epg.get("radio_placeholders", d.radio_placeholders)),
        git_url=os.environ.get("KIA_EPG_GIT_URL") or epg.get("git_url", d.git_url),
        git_ref=os.environ.get("KIA_EPG_GIT_REF") or epg.get("git_ref", d.git_ref),
        native_grabbers=epg.get("native_grabbers", d.native_grabbers),
        name_matching=epg.get("name_matching", d.name_matching),
        playlist_guides=epg.get("playlist_guides", d.playlist_guides),
        site_time_limit=epg.get("site_time_limit", d.site_time_limit),
        site_connections=dict(epg.get("site_connections", {})),
        site_delay_ms=dict(epg.get("site_delay_ms", {})),
        accept_name_matches=epg.get("accept_name_matches", []),
        ignore_name_matches=epg.get("ignore_name_matches", []),
    )
