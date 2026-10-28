"""Load kia.toml and the playlist sources file."""

from __future__ import annotations

import math
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from kia.alerts import DEFAULT_EVENTS, EVENTS, AlertConfig, EmailConfig


# Lines in a sources file that ask a radio service for its stations, instead of naming a
# playlist: radio-browser.info's directory, SomaFM's channels, and KEXP (kia.radio).
STATION_PREFIXES = ("radio-browser:", "somafm:", "kexp:")


def is_station_line(text: str) -> bool:
    return text.lstrip().lower().startswith(STATION_PREFIXES)


@dataclass
class Source:
    name: str
    url: str
    params: dict[str, str] = field(default_factory=dict)
    # Everything in the playlist is radio (for lists that don't mark their stations).
    radio: bool = False
    # Its streams may be on your own network, though the playlist comes from elsewhere (kia.lan).
    allow_lan: bool = False

    @property
    def is_yours(self) -> bool:
        """A playlist of your own, whose streams may be on your network (kia.lan): a file
        on this computer, one downloaded from an address on your network, or one marked lan.
        The address counts by what it says (lan.names_your_network), never by a DNS lookup."""
        from kia import lan

        if self.is_stations:
            return False
        return self.allow_lan or self.is_local_file or lan.names_your_network(lan.host_of(self.url))

    @property
    def is_radio_browser(self) -> bool:
        return self.url.startswith("radio-browser:")

    @property
    def is_stations(self) -> bool:
        """A station line (radio-browser:, somafm:, kexp:), rather than a playlist."""
        return is_station_line(self.url)

    @property
    def is_local_file(self) -> bool:
        """A playlist file on this computer (yours), rather than one from the internet."""
        return not self.is_stations and not re.match(r"[a-z][a-z0-9+.-]*://", self.url, re.I)


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
    # The sources file in use, and sources.txt (the list of playlists to choose from).
    sources_file: Path | None = None
    config_file: Path | None = None
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
    # Reuse the number of a channel last seen this many days ago (0: never reuse numbers).
    recycle_numbers_after_days: int = 0
    profiles_file: Path | None = None
    public_url: str | None = None
    # The secure Dashboard (kia.tls): its certificate's folder, the port people open
    # (with Docker, the one published on this machine), and the names the certificate
    # covers (empty: the host in public_url).
    tls_dir: Path = Path("tls")
    https_port: int = 8743
    tls_names: list[str] = field(default_factory=list)
    # The front door's two host names (KIA_FRONTDOOR_HOST, KIA_FRONTDOOR_AUTH_HOST): added to
    # the certificate so browsers trust it on those names too (docs/front-door.md).
    frontdoor_host: str | None = None
    frontdoor_auth_host: str | None = None
    dns_resolvers: tuple[str, ...] = ("8.8.8.8", "1.1.1.1")
    alerts: AlertConfig = field(default_factory=AlertConfig)
    history_days: int = 30
    upstream_url: str | None = None
    upstream_fallback: str = "grab"
    # Pluto TV channels play through kia (kia.pluto), so they keep going across ad breaks.
    pluto_relay: bool = True
    # Requests from AI assistants (kia.asks, [asks]): minutes one waits for you, and how many
    # can wait at once.
    asks_minutes: int = 10
    asks_waiting: int = 4  # kia.asks.WAITING: one of each kind
    # The kinds that run without you, only from these gateway devices (empty: any caller),
    # at most asks_per_hour a kind (kia.asks.why_auto).
    asks_auto: tuple[str, ...] = ()
    asks_devices: tuple[str, ...] = ()
    asks_per_hour: int = 20
    epg: EpgConfig = field(default_factory=EpgConfig)


def source_name(url: str, taken: set[str]) -> str:
    """Derive a short name from a playlist URL: .../countries/us.m3u -> "us"."""
    stem = Path(urlparse(url).path).stem or urlparse(url).netloc or "source"
    stem = re.sub(r"[^\w.-]+", "-", stem).strip("-.")[:60] or "source"  # it becomes a file name
    name, n = stem, 2
    while name in taken:
        name, n = f"{stem}-{n}", n + 1
    return name


def parse_sources(text: str) -> list[Source]:
    """Parse a sources file: one playlist URL or path per line, optionally followed by a name,
    the word `radio` (everything in that playlist is a radio station), and the word `lan`
    (its streams may be on your own network; see kia.lan).

    Blank lines and anything after a ``#`` that starts a line or follows whitespace are ignored.
    """
    sources: list[Source] = []
    taken: set[str] = set()
    # Files saved by Windows Notepad can start with a byte-order mark.
    for raw in text.removeprefix("\ufeff").splitlines():
        line = re.sub(r"(^|\s)#.*", "", raw).strip()
        if not line:
            continue
        if is_station_line(line):
            source = _radio_source(line, taken)
        else:
            url, *rest = line.split()
            flags = {w for w in rest if w.lower() in ("radio", "lan")}
            words = {w.lower() for w in flags}
            name = next((w for w in rest if w not in flags), "")
            source = Source(name=name or source_name(url, taken), url=url, radio="radio" in words, allow_lan="lan" in words)
        # The name becomes a file name (work/sources/<name>.m3u): letters, digits, ., _ and - only.
        if not re.fullmatch(r"[\w.-]{1,80}", source.name):
            raise ValueError(f"source name {source.name!r}: use letters, digits, dots, dashes, and underscores")
        if source.name in taken:
            raise ValueError(f"duplicate source name {source.name!r}")
        taken.add(source.name)
        sources.append(source)
    return sources


def _radio_source(line: str, taken: set[str]) -> Source:
    """`radio-browser: country=US top=200 tag=jazz name=jazz` -> a radio-browser query;
    `somafm: channels=groovesalad,dronezone` and `kexp:` likewise."""
    prefix, _, rest = line.partition(":")
    prefix = prefix.strip().lower()
    params = {}
    for token in rest.split():
        key, eq, value = token.partition("=")
        if not eq or not value:
            raise ValueError(f"{prefix}: expected key=value, got {token!r}")
        params[key.strip().lower()] = value.strip()
    name = params.pop("name", None)
    if not name:
        if prefix == "radio-browser":
            base = "radio-" + "-".join(params[k].lower() for k in ("country", "tag", "language", "station") if k in params)
        else:
            base = prefix
        name, n = base.rstrip("-") or "radio", 2
        while name in taken:
            name, n = f"{base}-{n}", n + 1
    from kia import radio

    radio.check_params(prefix, params)  # check the choices now, not at 4 AM
    return Source(name=name, url=f"{prefix}:{rest}".strip(), params=params)


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
        text = sources_file.read_text()
        sources = parse_sources(text)
        origin = str(sources_file)
        # Choices made on the Dashboard (kia.sources), on top of the file.
        from kia import sources as web_sources

        state = web_sources.load_state(base / output.get("state_dir", "state"))
        if any(state.values()):
            catalog = base / "sources.txt"
            offers = web_sources.offered(text)
            if catalog.exists() and catalog.resolve() != sources_file.resolve():
                offers += web_sources.offered(catalog.read_text())
            sources = web_sources.apply(sources, offers, state)
    if not sources:
        raise ValueError(f"{origin}: no playlist sources listed")
    return Config(
        sources=sources,
        sources_file=None if urls else sources_file,
        config_file=path.resolve(),
        concurrency=_at_least_one("concurrency", _env_int("KIA_CHECK_CONCURRENCY", check.get("concurrency", 20))),
        timeout=_number("[check] timeout", check.get("timeout", 15.0), above=0),
        per_host=_at_least_one("per_host", check.get("per_host", 2)),
        per_host_limits={host: _at_least_one(f"per_host_limits {host}", n)
                         for host, n in dict(check.get("per_host_limits", {})).items()},
        host_delay_ms=_whole("[check] host_delay_ms", check.get("host_delay_ms", 100), 0),
        require_video=_switch("[check] require_video", check.get("require_video", True)),
        grace_failures=_whole("[check] grace_failures", check.get("grace_failures", 2), 0),
        merge_duplicates=_switch("[check] merge_duplicates", check.get("merge_duplicates", True)),
        user_agent=check.get("user_agent", DEFAULT_USER_AGENT) or None,
        ffprobe=check.get("ffprobe", "ffprobe"),
        exclude_groups=_merge_env_list("KIA_EXCLUDE_GROUPS", filters.get("exclude_groups", [])),
        exclude_labels=filters.get("exclude_labels", []),
        exclude_name_patterns=_patterns(filters.get("exclude_name_patterns", [])),
        output_dir=base / output.get("dir", "output"),
        state_dir=base / output.get("state_dir", "state"),
        epg_url=output.get("epg_url") or _derive_epg_url(output),
        channel_numbers=_switch("[output] channel_numbers", output.get("channel_numbers", True)),
        recycle_numbers_after_days=_recycle_days(output.get("recycle_numbers_after_days", 0)),
        profiles_file=base / (os.environ.get("KIA_PROFILES_FILE") or data.get("profiles_file", "profiles/profiles.toml")),
        public_url=_public_url(output),
        tls_dir=base / output.get("tls_dir", "tls"),
        https_port=_port("KIA_HTTPS_PORT", _whole("[output] https_port", output.get("https_port", 8743), 1)),
        tls_names=_merge_env_list("KIA_TLS_NAMES", list(output.get("tls_names", []))),
        frontdoor_host=(os.environ.get("KIA_FRONTDOOR_HOST") or "").strip() or None,
        frontdoor_auth_host=(os.environ.get("KIA_FRONTDOOR_AUTH_HOST") or "").strip() or None,
        alerts=_load_alerts(data.get("alerts", {})),
        history_days=_whole("[check] history_days", check.get("history_days", 30), 1),
        upstream_url=_upstream_url(data.get("upstream", {})),
        upstream_fallback=_upstream_fallback(data.get("upstream", {})),
        pluto_relay=env_bool("KIA_PLUTO_RELAY", data.get("pluto", {}).get("relay", True)),
        dns_resolvers=_texts("[dns] verify_resolvers", data.get("dns", {}).get("verify_resolvers", ("8.8.8.8", "1.1.1.1"))),
        asks_minutes=_within("[asks] expires_minutes", data.get("asks", {}).get("expires_minutes", 10), 1, 1440),
        asks_waiting=_within("[asks] waiting", data.get("asks", {}).get("waiting", 4), 1, 4),  # one of each of the 4 kinds at most
        asks_auto=_auto_kinds(data.get("asks", {}).get("auto_approve", [])),
        asks_devices=_devices(data.get("asks", {}).get("auto_devices", [])),
        asks_per_hour=_within("[asks] auto_per_hour", data.get("asks", {}).get("auto_per_hour", 20), 1, 200),
        epg=_load_epg(data.get("epg", {}), base),
    )


def _recycle_days(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"recycle_numbers_after_days {value!r}: expected a whole number of days (0 = never)")
    if 0 < value < 60:
        raise ValueError("recycle_numbers_after_days: use 60 or more, so Kodi has forgotten the old channel")
    return value


def _at_least_one(name: str, value: object) -> int:
    """Limits on streams tested at once: 0 would make the check wait forever."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"[check] {name} = {value!r}: expected a whole number, 1 or more")
    return value


def _whole(name: str, value: object, least: int) -> int:
    """A whole number from kia.toml, checked when it's read: a typo such as days = "3" would
    otherwise stop the refresh hours later, part way through."""
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        raise ValueError(f"{name} = {value!r}: expected a whole number, {least} or more")
    return value


def _within(name: str, value: object, least: int, most: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not least <= value <= most:
        raise ValueError(f"{name} = {value!r}: expected a whole number from {least} to {most}")
    return value


def _auto_kinds(value: object) -> tuple[str, ...]:
    """[asks] auto_approve: request kinds that run without you. Only those that test, refresh,
    or check qualify (kia.asks.AUTO_KINDS); add_to_plex always waits."""
    from kia.asks import AUTO_KINDS

    kinds = _texts("[asks] auto_approve", value, '["test_channel"]')
    if bad := [k for k in kinds if k not in AUTO_KINDS]:
        raise ValueError(f"[asks] auto_approve: {bad[0]!r} can't run without you; choose from {', '.join(AUTO_KINDS)}")
    return kinds


def _devices(value: object) -> tuple[str, ...]:
    """[asks] auto_devices: gateway device names, as scripts/gateway.sh allows them."""
    names = _texts("[asks] auto_devices", value, '["laptop"]')
    if bad := [n for n in names if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", n)]:
        raise ValueError(f"[asks] auto_devices: {bad[0]!r} isn't a device name (lowercase letters, digits, and dashes)")
    return names


def _number(name: str, value: object, above: float, most: float | None = None) -> float:
    """A number from kia.toml (whole or not), more than `above` and at most `most`."""
    # (TOML has nan and inf: nan passes every comparison, and inf breaks every sum.)
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= above
            or (most is not None and value > most)):  # fmt: skip
        limits = f"more than {above:g}" + (f" and at most {most:g}" if most is not None else "")
        raise ValueError(f"{name} = {value!r}: expected a number, {limits}")
    return value


def _patterns(value: object) -> list[str]:
    """[filters] exclude_name_patterns: regular expressions, compiled once here, so a broken
    one is reported at the start instead of stopping every check."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"[filters] exclude_name_patterns = {value!r}: expected a list of quoted patterns")
    for pattern in value:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"[filters] exclude_name_patterns: {pattern!r} isn't a valid pattern ({exc})") from None
    return value


def _port(name: str, fallback: int) -> int:
    port = _env_int(name, fallback)
    if port > 65535:
        raise ValueError(f"{name}={port}: a port is at most 65535")
    return port


def _switch(name: str, value: object) -> bool:
    """true or false from kia.toml: the text "false" would otherwise count as on."""
    if not isinstance(value, bool):
        raise ValueError(f"{name} = {value!r}: expected true or false (without quotes)")
    return value


def _texts(name: str, value: object, like: str = '["8.8.8.8", "1.1.1.1"]') -> tuple[str, ...]:
    """A list of text from kia.toml: a single "8.8.8.8" would otherwise be read letter by letter."""
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{name} = {value!r}: expected a list, like {like}")
    return tuple(value)


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


def _placeholder_hours(hours: object) -> int:
    if type(hours) is not int or hours not in (1, 2, 3, 4, 6, 8, 12, 24):  # 2.0 would pass "in" and fail later
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
        drop_percent=_number("[alerts] drop_percent", alerts.get("drop_percent", 25), above=0, most=100),
        site_fail_runs=_whole("[alerts] site_fail_runs", alerts.get("site_fail_runs", 3), 1),
        new_with_guide=_switch("[alerts] new_channels_with_guide", alerts.get("new_channels_with_guide", False)),
        new_categories=_texts("[alerts] new_channels_categories", alerts.get("new_channels_categories", []), '["News", "Sports"]'),
        email=_load_email(alerts.get("email", {})),
    )


def _load_email(email: dict) -> EmailConfig | None:
    """Email alerts: KIA_SMTP_* and KIA_EMAIL_* in .env, or [alerts.email] in kia.toml."""

    def get(env: str, key: str) -> str:
        return (os.environ.get(env) or str(email.get(key) or "")).strip()

    host, to = get("KIA_SMTP_HOST", "host"), get("KIA_EMAIL_TO", "to")
    if not host and not to:
        return None
    if not host or not to:
        raise ValueError("email alerts need both KIA_SMTP_HOST (the email server) and KIA_EMAIL_TO (who gets them)")
    security = (get("KIA_SMTP_SECURITY", "security") or "starttls").lower()
    if security not in ("starttls", "ssl", "none"):
        raise ValueError(f"KIA_SMTP_SECURITY={security!r}: expected starttls, ssl, or none")
    port = get("KIA_SMTP_PORT", "port") or ("465" if security == "ssl" else "587" if security == "starttls" else "25")
    if not port.isdigit():
        raise ValueError(f"KIA_SMTP_PORT={port!r}: expected a number")
    return EmailConfig(host=host, to=[t.strip() for t in to.split(",") if t.strip()], port=int(port),
                       user=get("KIA_SMTP_USER", "user") or None, password=get("KIA_SMTP_PASSWORD", "password") or None,
                       sender=get("KIA_EMAIL_FROM", "from") or None, security=security)  # fmt: skip


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


def _per_site(name: str, value: object, least: int) -> dict[str, int]:
    """[epg] site_connections and site_delay_ms: a whole number for each site name."""
    if not isinstance(value, dict):
        raise ValueError(f'{name} = {value!r}: expected a table, like {{ "tvpassport.com" = 100 }}')
    return {site: _whole(f"{name} {site!r}", n, least) for site, n in value.items()}


def _runner(value: object) -> str:
    if value not in ("docker", "local"):
        raise ValueError(f"[epg] runner = {value!r}: expected \"docker\" or \"local\"")
    return value


MAX_EPG_DAYS = 14  # each day is another day of every site to download, and of name blocks


def _load_epg(epg: dict, base: Path) -> EpgConfig:
    d = EpgConfig()
    return EpgConfig(
        repo_dir=base / epg.get("repo_dir", d.repo_dir),
        work_dir=base / epg.get("work_dir", d.work_dir),
        days=_within("KIA_EPG_DAYS or [epg] days", _env_int("KIA_EPG_DAYS", _whole("[epg] days", epg.get("days", d.days), 1)), 1, MAX_EPG_DAYS),
        max_sites_per_channel=_whole("[epg] max_sites_per_channel", epg.get("max_sites_per_channel", d.max_sites_per_channel), 1),
        prefer_sites=list(_texts("[epg] prefer_sites", epg.get("prefer_sites", []), '["tvpassport.com"]')),
        exclude_sites=_merge_env_list("KIA_EXCLUDE_SITES", list(_texts("[epg] exclude_sites", epg.get("exclude_sites", []), '["plex.tv"]'))),
        # KIA_EPG_RUNNER lets the Docker image (which bundles Node) force "local".
        runner=_runner(os.environ.get("KIA_EPG_RUNNER") or epg.get("runner", d.runner)),
        docker_image=epg.get("docker_image", d.docker_image),
        parallel_sites=_env_int("KIA_PARALLEL_SITES", _whole("[epg] parallel_sites", epg.get("parallel_sites", d.parallel_sites), 1)),
        max_connections=_whole("[epg] max_connections", epg.get("max_connections", d.max_connections), 1),
        placeholders=env_bool("KIA_PLACEHOLDERS", _switch("[epg] placeholders", epg.get("placeholders", d.placeholders))),
        placeholder_hours=_placeholder_hours(epg.get("placeholder_hours", d.placeholder_hours)),
        radio_placeholders=env_bool("KIA_RADIO_PLACEHOLDERS",
                                    _switch("[epg] radio_placeholders", epg.get("radio_placeholders", d.radio_placeholders))),
        git_url=os.environ.get("KIA_EPG_GIT_URL") or epg.get("git_url", d.git_url),
        git_ref=os.environ.get("KIA_EPG_GIT_REF") or epg.get("git_ref", d.git_ref),
        native_grabbers=_switch("[epg] native_grabbers", epg.get("native_grabbers", d.native_grabbers)),
        name_matching=_switch("[epg] name_matching", epg.get("name_matching", d.name_matching)),
        playlist_guides=_switch("[epg] playlist_guides", epg.get("playlist_guides", d.playlist_guides)),
        site_time_limit=_whole("[epg] site_time_limit", epg.get("site_time_limit", d.site_time_limit), 0),
        site_connections=_per_site("[epg] site_connections", epg.get("site_connections", {}), 1),
        site_delay_ms=_per_site("[epg] site_delay_ms", epg.get("site_delay_ms", {}), 0),
        accept_name_matches=list(_texts("[epg] accept_name_matches", epg.get("accept_name_matches", []), '["News12.us@SD"]')),
        ignore_name_matches=list(_texts("[epg] ignore_name_matches", epg.get("ignore_name_matches", []), '["News12.us@SD"]')),
    )
