"""The parts of the stack that run next to kia (COMPOSE_PROFILES), and whether each one
answers: for the Stack tiles on the Dashboard (output/stack.json) and /metrics.

kia has no Docker socket, by design, so it can't ask Docker about containers. It knocks on
each one's port on Compose's network instead, and asks Prometheus about node-exporter, which
runs on the computer itself rather than on that network."""

from __future__ import annotations

import json
import os
import secrets
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
    # Where a browser opens it: the .env setting for the port it's published on, that
    # port's default, and the path. No setting: it has no page that other devices can open.
    link_env: str | None = None
    link_port: int = 0
    link_path: str = "/"


SERVICES = (
    Service("plex", "threadfin", "Threadfin", 34400, "KIA_THREADFIN_PORT", 34400, "/web/"),
    Service("plex-2", "threadfin-2", "Threadfin 2", 34400, "KIA_THREADFIN_2_PORT", 34401, "/web/"),
    Service("plex-server", "plex", "Plex", 32400, "KIA_PLEX_PORT", 32400, "/web"),
    Service("jellyfin-server", "jellyfin", "Jellyfin", 8096, "KIA_JELLYFIN_PORT", 8096, "/"),
    Service("monitoring", "grafana", "Grafana", 3000),  # links to the same address as the Observability button
    Service("monitoring", "prometheus", "Prometheus", 9090),  # no link: its port is open to this computer only
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
    """The programs these Compose profiles start, in the order the Dashboard shows them."""
    return [s for s in SERVICES if s.profile in running]


def answers(host: str, port: int, timeout: float = 2) -> bool:
    """Whether something accepts a connection on host:port. That's enough to tell a program
    that's running from one that has stopped, without knowing how to talk to it."""
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


# --- The Stack page: asking the kia-stack service for a change ----------------------
#
# kia can't run Docker, so it asks the kia-stack service on the computer
# (scripts/stack.sh watch, docs/stack.md). The two talk through files:
#
#   state/stack-request           kia writes it: id, action (check or apply), and stack
#                                 (the parts). The service renames it into its own folder.
#   stack-service/request         the request the service is working on
#   stack-service/result          the service's answer: id, action, state, exit, finished,
#                                 then a blank line and what scripts/stack.sh said
#   stack-service/containers.tsv  the service's list of containers, every 30 seconds
#
# stack-service/ is in the kia folder, which kia sees read-only: kia reads the answers
# there but can't write anything the service reads back or writes through. The service
# checks every request again before acting on it, but kia checks too, so a mistake is
# caught here with a message the Dashboard can show.

# The parts you can choose, as scripts/stack.sh's PARTS lists them (a test keeps the two
# lists the same), with what each one is, for the page.
PARTS = (
    ("plex", "Plex in the stack, with Threadfin as its tuner"),
    ("plex-tuner", "Threadfin as the tuner for a Plex on another computer"),
    ("plex-tuner-2", "A second Threadfin, for more than 480 channels in Plex"),
    ("jellyfin", "Jellyfin in the stack"),
    ("observability", "Prometheus and Grafana, and node-exporter on Linux"),
    ("observability-gpu", "The graphics card on the Hardware dashboard: NVIDIA, Linux"),
    ("observability-disks", "Each disk's health (SMART) on the Hardware dashboard: Linux"),
)
SERVICE_DIR = "stack-service"  # in the kia folder
REQUEST, TAKEN, RESULT, CONTAINERS = "stack-request", "request", "result", "containers.tsv"
SERVICE_SILENT_AFTER = 90  # seconds without a new container list: the service isn't running
# The service refuses a request older than this, and the page stops waiting after it too:
# an apply nobody is watching for any more shouldn't start later.
REQUEST_EXPIRES = 300
RUNNING_UP_TO = 1800  # an apply still under way after this long counts as stuck
MAX_OUTPUT = 20_000  # characters of the script's output the page gets


class StackError(ValueError):
    """A choice of parts kia won't ask for, or a request already waiting."""


def parts_from(value) -> list[str]:
    """The parts in a request from the Dashboard: known names only, each once, in the
    order they're listed above."""
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise StackError("Choose the parts as a list of names.")
    known = [name for name, _ in PARTS]
    unknown = [p for p in value if p not in known]
    if unknown:
        raise StackError(f"kia doesn't know {', '.join(sorted(set(unknown))[:3])[:80]}.")
    return [name for name in known if name in value]


def presets(project_dir: Path) -> list[dict]:
    """The presets in stacks/: each one's name, what it's for (its first line), and parts."""
    out = []
    for path in sorted((project_dir / "stacks").glob("*.env")):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        what = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else ""
        stack = next((line.split("=", 1)[1] for line in lines if line.startswith("KIA_STACK=")), "")
        names = [p for p in stack.replace(" ", ",").split(",") if p]
        out.append({"name": path.stem, "what": what, "parts": names})
    return out


def _age(path: Path) -> float | None:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def containers(service_dir: Path) -> list[dict]:
    """The service's last list of containers. It comes from Docker on the computer, but the
    page shows it as text only, and long values are cut short."""
    try:
        text = (service_dir / CONTAINERS).read_text(errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines()[:100]:
        cells = (line.split("\t") + ["", "", "", ""])[:4]
        if cells[0]:
            out.append(dict(zip(("service", "state", "health", "status"), (c[:80] for c in cells), strict=True)))
    return out


def result(service_dir: Path) -> dict | None:
    """The service's answer to the last request, or None before the first one."""
    try:
        text = (service_dir / RESULT).read_text(errors="replace")
    except OSError:
        return None
    head, _, said = text.partition("\n\n")
    fields = {}
    for line in head.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in ("id", "action", "state", "exit", "finished"):
            fields[key] = value[:64]
    fields["output"] = said[-MAX_OUTPUT:]
    return fields


def _request_id(path: Path) -> str:
    try:
        for line in path.read_text(errors="replace").splitlines()[:10]:
            if line.startswith("id="):
                return line[3:][:64]
    except OSError:
        pass
    return ""


def waiting(state_dir: Path, service_dir: Path) -> str | None:
    """The id of a request the service hasn't finished yet, if there's one still current:
    one kia left that the service hasn't taken (for up to 5 minutes), or one it's working
    on (an apply can take a while, downloading images)."""
    left = _age(state_dir / REQUEST)
    if left is not None and left < REQUEST_EXPIRES:
        return _request_id(state_dir / REQUEST)
    taken = _age(service_dir / TAKEN)
    if taken is not None and taken < RUNNING_UP_TO:
        return _request_id(service_dir / TAKEN)
    return None


def service_running(service_dir: Path) -> bool:
    """Whether the kia-stack service is there: its container list is fresh, or it's in the
    middle of a request (it writes the list only between requests)."""
    seen = _age(service_dir / CONTAINERS)
    if seen is not None and seen < SERVICE_SILENT_AFTER:
        return True
    working = _age(service_dir / RESULT)
    last = result(service_dir) or {}
    return last.get("state") == "running" and working is not None and working < RUNNING_UP_TO


def page_state(state_dir: Path, project_dir: Path) -> dict:
    """Everything the Stack page shows: what's chosen and running, whether the service is
    there to change it, and its last answer."""
    service_dir = project_dir / SERVICE_DIR
    return {
        "docker": profiles() is not None,
        "chosen": [p for p in os.environ.get("KIA_STACK", "").replace(" ", ",").split(",") if p],
        "profiles": profiles() or [],
        "parts": [{"name": n, "what": w} for n, w in PARTS],
        "presets": presets(project_dir),
        "service": service_running(service_dir),
        "containers": containers(service_dir),
        "waiting": waiting(state_dir, service_dir),
        "result": result(service_dir),
    }


def ask(state_dir: Path, service_dir: Path, action: str, parts: list[str]) -> str:
    """Leave a request for the kia-stack service. Returns its id, which the service's answer
    carries, so the page knows the answer is to this request."""
    if action not in ("check", "apply"):
        raise StackError("The action is check or apply.")
    if waiting(state_dir, service_dir) is not None:
        raise StackError("The service is still working on the last request. Wait for its answer first.")
    rid = secrets.token_hex(8)
    path = state_dir / REQUEST
    tmp = temp_name(path)
    tmp.write_text(f"id={rid}\naction={action}\nstack={','.join(parts)}\n")
    tmp.replace(path)  # whole, at once: the service never reads half a request
    return rid
