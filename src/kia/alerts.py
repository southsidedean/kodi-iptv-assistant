"""Raise alerts for problems that need a human, e.g. local DNS failing."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from kia.history import now_iso

# Exit code for runs that completed but raised an alert, so schedulers can react.
EXIT_ALERT = 3


def raise_alert(
    title: str,
    message: str,
    state_dir: Path,
    command: str | None,
    log: Callable[[str], None],
) -> None:
    """Log the alert, append it to state/alerts.log, and run the configured alert command."""
    log(f"ALERT: {title}\n{message}")
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "alerts.log", "a") as f:
        f.write(f"{now_iso()}\t{title}\t{message.replace(chr(10), ' | ')}\n")
    if not command:
        return
    env = {**os.environ, "KIA_ALERT_TITLE": title, "KIA_ALERT_MESSAGE": message}
    try:
        proc = subprocess.run(command, shell=True, env=env, timeout=60, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        log("alert command timed out after 60s")
        return
    if proc.returncode != 0:
        log(f"alert command failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}")
