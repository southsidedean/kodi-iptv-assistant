"""The settings kia is running with, for the Dashboard's System page and `kia settings`.

Read only. Settings are changed in .env and kia.toml. A few can also be changed on the
Dashboard's secure page: the playlists kia checks (kia.sources), the password, and
KIA_STACK (the Stack page, carried out by the kia-stack service). Anything secret (the
password, the ntfy address, the alert command) shows only whether it's set.
"""

from __future__ import annotations

import os
from datetime import datetime

from kia import auth
from kia.config import Config

# Where each setting is changed: its .env name if it has one, else its place in kia.toml.
ENV = ".env: "
TOML = "kia.toml: "


def _yes(value: bool) -> str:
    return "on" if value else "off"


def _list(items: list[str] | tuple[str, ...]) -> str:
    return ", ".join(items) if items else "none"


def _set(value: object) -> str:
    return "set" if value else "not set"


def _editing_setup(cfg: Config) -> str:
    """How editing on the Dashboard is set up, in words: off, or where its password comes from
    (never the password itself; named so a scanner doesn't take it for one)."""
    source = auth.from_env(cfg.state_dir).source
    return {
        "off": "editing is off",
        "env": "your own, from .env",
        "generated": "made by kia, in state/admin-password.txt",
        "set": "changed on the Dashboard",
    }.get(source, source)


def _masked(url: str) -> str:
    from kia.api import mask_url

    return mask_url(url)


def _time_zone() -> str:
    name = os.environ.get("TZ") or ""
    abbr = datetime.now().astimezone().tzname() or ""
    return f"{name} ({abbr})" if name and abbr and name != abbr else name or abbr or "unknown"


def describe(cfg: Config, run_at: str | None = None, guide_every: float | None = None) -> list[dict]:
    """Sections of {name, value, change} rows. run_at and guide_every come from the
    daemon (None when kia isn't running as one)."""
    e = cfg.epg
    a = cfg.alerts
    every = "off" if guide_every == 0 else f"every {guide_every:g} hours" if guide_every else None
    sections = [
        ("Schedule", [
            ("Nightly refresh", run_at or "not running on a schedule", ENV + "KIA_RUN_AT"),
            ("Quick guide refresh", every or "-", ENV + "KIA_GUIDE_REFRESH_HOURS"),
            ("Time zone", _time_zone(), ENV + "TZ"),
        ]),
        ("Addresses", [
            ("Dashboard and playlists", cfg.public_url or "not set", ENV + "KIA_PUBLIC_URL"),
            ("Secure page port", str(cfg.https_port), ENV + "KIA_HTTPS_PORT"),
            ("Certificate names", _list(cfg.tls_names) if cfg.tls_names else "the host in KIA_PUBLIC_URL",
             ENV + "KIA_TLS_NAMES"),
            ("Password for changes", _editing_setup(cfg), ENV + "KIA_ADMIN_PASSWORD"),
        ]),
        ("Playlists", [
            ("Sources", f"{len(cfg.sources)}: {_list([s.name for s in cfg.sources])}",
             "the Dashboard (Channel sources), or " + ENV + "KIA_SOURCES_FILE"),
            ("Profiles file", _relative(cfg, cfg.profiles_file), ENV + "KIA_PROFILES_FILE"),
            ("Left-out categories", _list(cfg.exclude_groups), ENV + "KIA_EXCLUDE_GROUPS"),
            ("Left-out name tags", _list(cfg.exclude_labels), TOML + "[filters] exclude_labels"),
            ("Channel numbers", _yes(cfg.channel_numbers), TOML + "[output] channel_numbers"),
            ("Reuse numbers of channels gone for", f"{cfg.recycle_numbers_after_days} days" if cfg.recycle_numbers_after_days
             else "never", TOML + "[output] recycle_numbers_after_days"),
            ("Join duplicate channels", _yes(cfg.merge_duplicates), TOML + "[check] merge_duplicates"),
            ("Pluto TV through kia", _yes(cfg.pluto_relay), ENV + "KIA_PLUTO_RELAY"),
        ]),
        ("Stream check", [
            ("Streams tested at once", str(cfg.concurrency), ENV + "KIA_CHECK_CONCURRENCY"),
            ("Seconds per stream", f"{cfg.timeout:g}", TOML + "[check] timeout"),
            ("TV needs video", _yes(cfg.require_video), TOML + "[check] require_video"),
            ("Failed checks before a stream is dropped", str(cfg.grace_failures + 1), TOML + "[check] grace_failures"),
            ("Identifies as", cfg.user_agent or "ffmpeg", TOML + "[check] user_agent"),
        ]),
        ("Guide", [
            ("Days of guide", str(e.days), ENV + "KIA_EPG_DAYS"),
            ("Sites downloaded at once", str(e.parallel_sites), ENV + "KIA_PARALLEL_SITES"),
            ("Skipped sites", _list(e.exclude_sites), ENV + "KIA_EXCLUDE_SITES"),
            ("Preferred sites", _list(e.prefer_sites), TOML + "[epg] prefer_sites"),
            ("Channel name where there's no guide", _yes(e.placeholders), ENV + "KIA_PLACEHOLDERS"),
            ("The same for radio", _yes(e.radio_placeholders), ENV + "KIA_RADIO_PLACEHOLDERS"),
            ("Match channels by name", _yes(e.name_matching), TOML + "[epg] name_matching"),
            ("Use playlists' own guides", _yes(e.playlist_guides), TOML + "[epg] playlist_guides"),
            ("Time limit per site", f"{e.site_time_limit} min" if e.site_time_limit else "none",
             TOML + "[epg] site_time_limit"),
            ("Guide from another kia server", _masked(cfg.upstream_url) if cfg.upstream_url else "no", ENV + "KIA_UPSTREAM_URL"),
            ("iptv-org/epg version", e.git_ref, ENV + "KIA_EPG_GIT_REF"),
        ]),
        ("Alerts", [
            ("ntfy address", _set(a.ntfy_url), ENV + "KIA_NTFY_URL"),
            ("Email", _set(a.email),
             ENV + "KIA_SMTP_HOST, KIA_EMAIL_TO"),
            ("Alert command", _set(a.command), ENV + "KIA_ALERT_COMMAND"),
            ("Events", _list(a.events), ENV + "KIA_ALERT_EVENTS"),
        ]),
        ("Stack", [
            ("Parts", _stack(), "the Dashboard (Stack page), or " + ENV + "KIA_STACK"),
        ]),
    ]  # fmt: skip
    if cfg.upstream_url:
        sections[4][1].append(("If that server is unreachable", cfg.upstream_fallback, ENV + "KIA_UPSTREAM_FALLBACK"))
    return [
        {"title": title, "rows": [{"name": n, "value": v, "change": c} for n, v, c in rows]}
        for title, rows in sections
    ]


def _stack() -> str:
    """KIA_STACK, as compose.yaml hands it to kia. Empty: COMPOSE_PROFILES chooses."""
    parts = [p for p in os.environ.get("KIA_STACK", "").replace(" ", ",").split(",") if p]
    if parts:
        return _list(parts)
    if "KIA_COMPOSE_PROFILES" not in os.environ:
        return "not running in Docker"
    profiles = [p.strip() for p in os.environ["KIA_COMPOSE_PROFILES"].split(",") if p.strip()]
    return f"not set (COMPOSE_PROFILES: {_list(profiles)})" if profiles else "kia only"


def _relative(cfg: Config, path) -> str:
    if path is None:
        return "none"
    try:
        return str(path.relative_to(cfg.output_dir.parent))
    except ValueError:
        return str(path)


def as_text(sections: list[dict]) -> str:
    """`kia settings`: the same, for a terminal."""
    width = max(len(r["name"]) for s in sections for r in s["rows"])
    lines = []
    for s in sections:
        lines.append(s["title"])
        lines += [f"  {r['name']:<{width}}  {r['value']}   ({r['change']})" for r in s["rows"]]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
