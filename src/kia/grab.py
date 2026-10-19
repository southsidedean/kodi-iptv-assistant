"""Run iptv-org/epg's grabber once per site, in parallel, via Docker or a local Node.js."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from kia.config import EpgConfig
from kia import playlistguide
from kia.native import NATIVE_GRABBERS

@dataclass
class GrabResult:
    site: str
    returncode: int
    elapsed: float
    log: Path
    output: Path


def runner_prefix(cfg: EpgConfig, name: str | None = None) -> list[str]:
    """Command prefix that runs a process inside the epg checkout. `name` names the Docker
    container, so a download that runs too long can be stopped."""
    if cfg.runner == "local":
        return []
    if cfg.runner != "docker":
        raise ValueError(f"unknown epg runner {cfg.runner!r} (use 'docker' or 'local')")
    repo, work = cfg.repo_dir.resolve(), cfg.work_dir.resolve()
    # If the folder doesn't exist, Docker creates it owned by root. Create it first so it
    # belongs to the user.
    work.mkdir(parents=True, exist_ok=True)
    # Run as the calling user so the files it writes belong to them (os.getuid doesn't
    # exist on Windows).
    user = ["-u", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
    return [
        "docker", "run", "--rm", *user, *(["--name", name] if name else []),
        "-e", "HOME=/tmp",
        "-v", f"{repo}:{repo}",
        "-v", f"{work}:{work}",
        "-w", str(repo),
        cfg.docker_image,
    ]  # fmt: skip


def _git(repo: Path, *args: str) -> None:
    # The checkout runs third-party code (npm packages) that can write to it. Turning hooks
    # off means nothing planted in .git/hooks runs when kia updates it.
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args], check=True)


def ensure_repo(cfg: EpgConfig, update: bool, log: Callable[[str], None]) -> None:
    """Check out cfg.git_ref (branch, tag, or commit) of cfg.git_url into cfg.repo_dir.

    Fetches only on first use or when update is set; reinstalls npm dependencies only when
    package-lock.json changed. If an update fails (GitHub or the network is down), the
    existing checkout is used as it is.
    """
    repo = cfg.repo_dir
    fresh = not (repo / ".git").exists()
    if not fresh and not update:
        return
    if fresh:
        _update_repo(cfg, fresh, log)
        return
    try:
        _update_repo(cfg, fresh, log)
    except (subprocess.CalledProcessError, OSError) as exc:
        log(f"Couldn't update the epg checkout ({exc}); using the copy from the last successful update")


def _update_repo(cfg: EpgConfig, fresh: bool, log: Callable[[str], None]) -> None:
    repo = cfg.repo_dir
    lock = repo / "package-lock.json"
    lock_before = lock.read_bytes() if lock.exists() else None
    if fresh:
        log(f"Fetching {cfg.git_url} ({cfg.git_ref}) into {repo}...")
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, "init", "-q")
        _git(repo, "remote", "add", "origin", cfg.git_url)
    else:
        log(f"Updating the epg checkout to {cfg.git_ref}...")
        _git(repo, "remote", "set-url", "origin", cfg.git_url)
    # Fetching by name works the same for branches, tags, and (on GitHub) commit hashes.
    # "latest" is the remote's HEAD: its default branch, whatever it's called.
    ref = "HEAD" if cfg.git_ref in ("", "latest") else cfg.git_ref
    _git(repo, "fetch", "-q", "--depth", "1", "origin", ref)
    _git(repo, "checkout", "-q", "--force", "--detach", "FETCH_HEAD")
    if fresh or lock_before != lock.read_bytes() or not (repo / "node_modules").exists():
        install_deps(cfg, log)
    else:
        # Same dependencies; just refresh the iptv-org API data (channel logos, feeds).
        subprocess.run(runner_prefix(cfg) + ["npm", "run", "api:load"], cwd=repo, check=True)


def install_deps(cfg: EpgConfig, log: Callable[[str], None]) -> None:
    log("Installing epg dependencies (npm ci)...")
    prefix = runner_prefix(cfg)
    # --ignore-scripts skips husky, a git hook installer that fails without git (the node
    # image has none). It also skips the postinstall step, which downloads iptv-org's
    # channel data, so run that separately.
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
    start = time.monotonic()
    native = NATIVE_GRABBERS.get(site) if cfg.native_grabbers else None
    if native:
        with open(log_path, "w") as logf:
            try:
                native(channels_file, staged, cfg.days, lambda msg: print(msg, file=logf, flush=True))
                returncode = 0
            except Exception as exc:  # report like a failed grabber process
                print(f"ERR: {type(exc).__name__}: {exc}", file=logf)
                returncode = 1
    else:
        returncode = _run_epg_grab(cfg, channels_file, staged, log_path)
    if returncode == 0 and staged.exists():
        staged.replace(output)
    elif returncode == 0:
        returncode = 1  # exited cleanly but wrote nothing
    return GrabResult(site, returncode, time.monotonic() - start, log_path, output)


def _run_epg_grab(cfg: EpgConfig, channels_file: Path, staged: Path, log_path: Path) -> int:
    site = channels_file.name.removesuffix(".channels.xml")
    container = f"kia-grab-{os.getpid()}-{site}"[:63]
    cmd = runner_prefix(cfg, container if cfg.runner == "docker" else None) + [
        "npm", "run", "grab", "--",
        f"--channels={channels_file.resolve()}",
        f"--output={staged}",
        f"--days={cfg.days}",
        f"--maxConnections={cfg.site_connections.get(site, cfg.max_connections)}",
        *([f"--delay={cfg.site_delay_ms[site]}"] if cfg.site_delay_ms.get(site) else []),
    ]  # fmt: skip
    with open(log_path, "w") as logf:
        # Its own process group, so a stop reaches npm's Node.js children too.
        proc = subprocess.Popen(cmd, cwd=cfg.repo_dir, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return proc.wait(timeout=cfg.site_time_limit * 60 if cfg.site_time_limit else None)
        except subprocess.TimeoutExpired:
            stop(proc, container if cfg.runner == "docker" else None)
            print(f"ERR: stopped after {cfg.site_time_limit} minutes (site_time_limit); kept the previous guide", file=logf)
            return 124


def stop(proc: subprocess.Popen, container: str | None) -> None:
    """Stop a download and everything it started."""
    if container:
        subprocess.run(["docker", "kill", container], capture_output=True)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass


def grab_all(cfg: EpgConfig, sites: list[str] | None, log: Callable[[str], None]) -> list[GrabResult]:
    channel_files = {p.name.removesuffix(".channels.xml"): p for p in (cfg.work_dir / "channels").glob("*.channels.xml")}
    if sites:
        channel_files = {s: p for s, p in channel_files.items() if s in sites}
    if not channel_files:
        return []
    needs_node = any(not (cfg.native_grabbers and s in NATIVE_GRABBERS) for s in channel_files)
    if needs_node and not (cfg.repo_dir / "node_modules").exists():
        try:
            install_deps(cfg, log)
        except (subprocess.CalledProcessError, OSError) as exc:
            # Those sites fail below and keep their previous guide; i.mjh.nz still works.
            log(f"Couldn't install the epg dependencies ({exc})")

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


def grab_playlist_guides(
    cfg: EpgConfig, spec: dict, log: Callable[[str], None], quick: bool = False
) -> list[GrabResult]:
    """Download the guides playlists name for themselves (see kia.playlistguide).

    A playlist can name many guides (Free-TV names about 100, over 300 MB), of which only a
    few have our channels. A full download notes which addresses had any of our programmes;
    a quick one (kia refresh-guide) fetches only those.
    """
    if spec:
        log(f"Downloading {len(spec)} guides named by playlists...")
    results = []
    work = cfg.work_dir.resolve()
    useful_path = work / "playlist-guides-used.json"
    useful = playlistguide.load(useful_path)
    for site, info in sorted(spec.items()):
        if quick and useful.get(site):
            info = {**info, "urls": [u for u in info["urls"] if u in useful[site]] or info["urls"]}
        output = work / "guides" / f"{site}.xml"
        staged = work / "guides" / ".staging" / f"{site}.xml"
        log_path = work / "logs" / f"{site}.log"
        staged.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        with open(log_path, "w") as logf:
            try:
                counts = playlistguide.download(info, staged, cfg.days, lambda m: print(m, file=logf, flush=True))
                staged.replace(output)  # only a good download replaces the previous guide
                if not quick:
                    useful[site] = [u for u, n in counts.items() if n]
                returncode = 0
            except (RuntimeError, OSError) as exc:
                print(f"ERR: {exc}", file=logf)
                returncode = 1
        r = GrabResult(site, returncode, time.monotonic() - start, log_path, output)
        log(f"  {site}: {'ok' if returncode == 0 else f'FAILED (see {log_path})'} in {r.elapsed:.0f}s")
        results.append(r)
    if not quick:
        playlistguide.save(useful_path, useful)
    return results
