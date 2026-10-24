"""The parts of the stack that run next to kia (COMPOSE_PROFILES), and whether each one
answers: for the Stack tiles on the Dashboard (output/stack.json) and /metrics.

kia has no Docker socket, by design, so it can't ask Docker about containers. It knocks on
each one's port on Compose's network instead, and asks Prometheus about node-exporter, which
runs on the computer itself rather than on that network."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kia.files import temp_name

STACK_FILE = "stack.json"


@dataclass(frozen=True)
class Service:
    profile: str  # the Compose profile that starts it
    name: str  # its Compose service name, also its address on Compose's network
    title: str
    port: int  # the port it answers on there
    # Where a browser opens it: the setting with the port published on the computer, and
    # its default, and the path. None: no page of its own to open (or not from other devices).
    link_env: str | None = None
    link_port: int = 0
    link_path: str = "/"


SERVICES = (
    Service("plex", "threadfin", "Threadfin", 34400, "KIA_THREADFIN_PORT", 34400, "/web/"),
    Service("plex-2", "threadfin-2", "Threadfin 2", 34400, "KIA_THREADFIN_2_PORT", 34401, "/web/"),
    Service("plex-server", "plex", "Plex", 32400, "KIA_PLEX_PORT", 32400, "/web"),
    Service("jellyfin-server", "jellyfin", "Jellyfin", 8096, "KIA_JELLYFIN_PORT", 8096, "/"),
    Service("monitoring", "grafana", "Grafana", 3000),  # its link is the Observability button's
    Service("monitoring", "prometheus", "Prometheus", 9090),  # open to the computer itself only
    Service("monitoring", "cadvisor", "cAdvisor", 8080),
    # On Linux kia's own (monitoring-node), on a Mac Homebrew's: Prometheus reaches either.
    Service("monitoring", "node-exporter", "node-exporter", 9100),
    Service("monitoring-gpu", "dcgm-exporter", "Graphics card exporter", 9400),
    Service("monitoring-disks", "smartctl-exporter", "Disk health exporter", 9633),
)


def profiles() -> list[str] | None:
    """The Compose profiles kia's stack runs with, or None when kia doesn't run in it."""
    if "KIA_COMPOSE_PROFILES" not in os.environ:
        return None
    return [p.strip() for p in os.environ["KIA_COMPOSE_PROFILES"].split(",") if p.strip()]


def chosen(running: list[str]) -> list[Service]:
    return [s for s in SERVICES if s.profile in running]


def answers(host: str, port: int, timeout: float = 2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def node_exporter_up(prometheus: str = "http://prometheus:9090", timeout: float = 3) -> bool:
    """Whether Prometheus reached node-exporter at its last scrape."""
    query = urllib.parse.urlencode({"query": 'up{job="node"}'})
    try:
        with urllib.request.urlopen(f"{prometheus}/api/v1/query?{query}", timeout=timeout) as r:  # nosec B310
            result = json.load(r)["data"]["result"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return any(x.get("value", [0, "0"])[1] == "1" for x in result)


def check(running: list[str], knock: Callable[[str, int], bool] = answers,
          node: Callable[[], bool] = node_exporter_up) -> list[dict]:  # fmt: skip
    """Each chosen service, whether it answers, and where a browser opens it."""
    services = chosen(running)

    def one(s: Service) -> dict:
        ok = node() if s.name == "node-exporter" else knock(s.name, s.port)
        out = {"service": s.name, "title": s.title, "ok": ok}
        if s.link_env:
            out["link"] = {"port": int(os.environ.get(s.link_env) or s.link_port), "path": s.link_path}
        elif s.name == "grafana":
            from kia.htmlpage import grafana_url

            if url := grafana_url():
                out["link"] = {"url": url}
        return out

    if not services:
        return []
    with ThreadPoolExecutor(max_workers=len(services)) as pool:
        return list(pool.map(one, services))


class Watch:
    """Checks the stack every minute in the background, writes stack.json for the Dashboard,
    and keeps the last result for /metrics."""

    def __init__(self, output_dir: Path, running: list[str], every: float = 60, check=check) -> None:
        self.path = output_dir / STACK_FILE
        self.running, self.every, self._check = running, every, check
        self.last: list[dict] = []

    def once(self) -> list[dict]:
        services = self._check(self.running)
        body = {"checked": datetime.now().astimezone().isoformat(timespec="seconds"), "services": services}
        tmp = temp_name(self.path)
        tmp.write_text(json.dumps(body, indent=1) + "\n")
        tmp.replace(self.path)
        self.last = services
        return services

    def start(self) -> None:
        def loop():
            while True:
                try:
                    self.once()
                except OSError as exc:  # output/ not writable for a moment: try again next time
                    print(f"stack: couldn't write {self.path.name}: {exc}", file=sys.stderr)
                time.sleep(self.every)

        threading.Thread(target=loop, daemon=True, name="kia-stack").start()
