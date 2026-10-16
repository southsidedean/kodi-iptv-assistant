"""Notify a person about problems that need one (and, optionally, a nightly summary)."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kia import __version__
from kia.history import now_iso

# Exit code for runs that completed but raised an alert, so schedulers can react.
EXIT_ALERT = 3

EVENTS = {
    "dns_local": "local DNS is failing (names resolve on public DNS but not here)",
    "run_failed": "a nightly run failed or crashed",
    "channels_drop": "working channels dropped sharply since the last run",
    "guide_drop": "channels with guide data dropped sharply since the last run",
    "site_failing": "a guide site failed several runs in a row",
    "daily_summary": "a short summary after every successful run (not an alert)",
}
DEFAULT_EVENTS = ("dns_local", "run_failed", "channels_drop", "guide_drop", "site_failing")
# Notices, not alerts: sent only when turned on, and never logged to alerts.log or
# counted in exit codes.
NOTICES = {"daily_summary"}
URGENT = {"dns_local", "run_failed"}


@dataclass
class AlertConfig:
    command: str | None = None
    ntfy_url: str | None = None
    events: tuple[str, ...] = DEFAULT_EVENTS
    drop_percent: int = 25
    site_fail_runs: int = 3


def sharp_drop(previous: int | None, current: int, percent: int) -> bool:
    """True when current is at least `percent` % below a previous, non-zero count."""
    return bool(previous) and current <= previous * (1 - percent / 100)


def load_state(state_dir: Path) -> dict:
    path = state_dir / "alert-state.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state_dir: Path, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / "alert-state.json.tmp"
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(state_dir / "alert-state.json")


def notify(
    event: str,
    title: str,
    message: str,
    state_dir: Path,
    cfg: AlertConfig,
    log: Callable[[str], None],
) -> bool:
    """Send an alert (or notice) through every configured channel.

    Returns True if this was an alert that's turned on, i.e. something a person should act on.
    """
    if event not in cfg.events:
        if event not in NOTICES:
            log(f"(alert '{event}' is turned off) {title}")
        return False
    notice = event in NOTICES
    log(f"{'NOTICE' if notice else 'ALERT'}: {title}\n{message}")
    if not notice:
        state_dir.mkdir(parents=True, exist_ok=True)
        with open(state_dir / "alerts.log", "a") as f:
            f.write(f"{now_iso()}\t{event}\t{title}\t{message.replace(chr(10), ' | ')}\n")
    if cfg.ntfy_url:
        send_ntfy(cfg.ntfy_url, event, title, message, log)
    if cfg.command:
        run_command(cfg.command, event, title, message, log)
    return not notice


def send_ntfy(url: str, event: str, title: str, message: str, log: Callable[[str], None]) -> None:
    headers = {
        "User-Agent": f"kodi-iptv-assistant/{__version__}",
        # HTTP headers are Latin-1; keep titles plain.
        "Title": title.encode("latin-1", "replace").decode("latin-1"),
        "Tags": "white_check_mark" if event in NOTICES else "warning",
        "Priority": "high" if event in URGENT else "default",
    }
    req = urllib.request.Request(url, data=message.encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except (urllib.error.URLError, OSError) as exc:
        log(f"ntfy notification failed: {getattr(exc, 'reason', exc)}")


def run_command(command: str, event: str, title: str, message: str, log: Callable[[str], None]) -> None:
    env = {**os.environ, "KIA_ALERT_EVENT": event, "KIA_ALERT_TITLE": title, "KIA_ALERT_MESSAGE": message}
    try:
        proc = subprocess.run(command, shell=True, env=env, timeout=60, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        log("alert command timed out after 60s")
        return
    if proc.returncode != 0:
        log(f"alert command failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}")
