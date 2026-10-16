"""Run iptv-org/epg's grabber once per site, in parallel, via Docker or a local Node.js."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from kia.config import EpgConfig

EPG_GIT_URL = "https://github.com/iptv-org/epg.git"


@dataclass
class GrabResult:
    site: str
    returncode: int
    elapsed: float
    log: Path
    output: Path


def runner_prefix(cfg: EpgConfig) -> list[str]:
    """Command prefix that runs a process inside the epg checkout."""
    if cfg.runner == "local":
        return []
    if cfg.runner != "docker":
        raise ValueError(f"unknown epg runner {cfg.runner!r} (use 'docker' or 'local')")
    repo, work = cfg.repo_dir.resolve(), cfg.work_dir.resolve()
    # Docker creates missing bind-mount sources as root; create it first so it stays ours.
    work.mkdir(parents=True, exist_ok=True)
    return [
        "docker", "run", "--rm",
        "-u", f"{os.getuid()}:{os.getgid()}",
        "-e", "HOME=/tmp",
        # Some sites' grabbers (e.g. epg.iptvx.one) outgrow Node's default heap.
        "-e", "NODE_OPTIONS=--max-old-space-size=4096",
        "-v", f"{repo}:{repo}",
        "-v", f"{work}:{work}",
        "-w", str(repo),
        cfg.docker_image,
    ]  # fmt: skip


def ensure_repo(cfg: EpgConfig, update: bool, log: Callable[[str], None]) -> None:
    repo = cfg.repo_dir
    if not (repo / ".git").exists():
        log(f"Cloning {EPG_GIT_URL} into {repo}...")
        repo.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", EPG_GIT_URL, str(repo)], check=True)
    elif update:
        log("Updating the epg checkout...")
        subprocess.run(["git", "-C", str(repo), "pull", "--ff-only", "--quiet"], check=True)
    else:
        return
    install_deps(cfg, log)


def install_deps(cfg: EpgConfig, log: Callable[[str], None]) -> None:
    log("Installing epg dependencies (npm ci)...")
    prefix = runner_prefix(cfg)
    # --ignore-scripts skips husky (needs git, which the node image lacks); the postinstall
    # step it also skips downloads iptv-org API data, so run that explicitly.
    subprocess.run(prefix + ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=cfg.repo_dir, check=True)
    subprocess.run(prefix + ["npm", "run", "api:load"], cwd=cfg.repo_dir, check=True)


def grab_site(cfg: EpgConfig, site: str, channels_file: Path) -> GrabResult:
    work = cfg.work_dir.resolve()
    output = work / "guides" / f"{site}.xml"
    # Grab into a staging dir and only replace the previous guide if this grab succeeds.
    staged = work / "guides" / ".staging" / f"{site}.xml"
    log_path = work / "logs" / f"{site}.log"
    staged.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    staged.unlink(missing_ok=True)
    cmd = runner_prefix(cfg) + [
        "npm", "run", "grab", "--",
        f"--channels={channels_file.resolve()}",
        f"--output={staged}",
        f"--days={cfg.days}",
        f"--maxConnections={cfg.max_connections}",
    ]  # fmt: skip
    start = time.monotonic()
    with open(log_path, "w") as logf:
        proc = subprocess.run(cmd, cwd=cfg.repo_dir, stdout=logf, stderr=subprocess.STDOUT)
    returncode = proc.returncode
    if returncode == 0 and staged.exists():
        staged.replace(output)
    elif returncode == 0:
        returncode = 1  # exited cleanly but wrote nothing
    return GrabResult(site, returncode, time.monotonic() - start, log_path, output)


def grab_all(cfg: EpgConfig, sites: list[str] | None, log: Callable[[str], None]) -> list[GrabResult]:
    channel_files = {p.name.removesuffix(".channels.xml"): p for p in (cfg.work_dir / "channels").glob("*.channels.xml")}
    if sites:
        channel_files = {s: p for s, p in channel_files.items() if s in sites}
    if not channel_files:
        raise FileNotFoundError(f"no channel lists in {cfg.work_dir / 'channels'}; run `kia map` first")
    if not (cfg.repo_dir / "node_modules").exists():
        install_deps(cfg, log)

    log(f"Grabbing {cfg.days} days from {len(channel_files)} sites, {cfg.parallel_sites} at a time...")
    results = []
    with ThreadPoolExecutor(cfg.parallel_sites) as pool:
        futures = [pool.submit(grab_site, cfg, site, path) for site, path in sorted(channel_files.items())]
        for fut in as_completed(futures):
            r = fut.result()
            status = "ok" if r.returncode == 0 and r.output.exists() else f"FAILED (see {r.log})"
            log(f"  {r.site}: {status} in {r.elapsed:.0f}s")
            results.append(r)
    return results
