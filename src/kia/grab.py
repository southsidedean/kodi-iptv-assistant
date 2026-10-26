"""Run iptv-org/epg's grabber once per site, in parallel, via Docker or a local Node.js."""

from __future__ import annotations

import functools
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
from kia.epgmap import is_safe_site
from kia.native import NATIVE_GRABBERS, grab_epgshare01, grab_imjh

# iptv-org/epg is third-party code: its site configs change every day and come from many
# contributors. Without Docker's own container (the "local" runner, which kia's image
# uses), kia runs it in Node's sandbox: compiled to plain JavaScript first (tsx would need
# to start esbuild, a separate program), then run with `node --permission`, which lets it
# read only the checkout and the channel list, write only its output and a temp folder,
# and start no programs, threads, or add-ons. It also sees none of kia's settings: only
# the environment variables below.
SCRIPTS = {"grab": "scripts/commands/epg/grab.ts", "api-load": "scripts/commands/api/load.ts"}
# The compiler, when iptv-org's checkout doesn't bring one (it switched from tsx, which uses
# esbuild, to @swc-node/register in October 2026): kia's own, this version, kept next to the
# checkout in vendor/kia-tools.
ESBUILD = "0.28.2"
PASS_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy",
            "https_proxy", "no_proxy", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR")  # fmt: skip


def epg_env(cfg: EpgConfig, home: bool = False) -> dict[str, str]:
    """The environment iptv-org's code runs with. `home` keeps HOME (npm's cache)."""
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    tmp = str(temp_dir(cfg))
    env.update(TMPDIR=tmp, HOME=os.environ.get("HOME", tmp) if home else tmp)
    return env


def temp_dir(cfg: EpgConfig) -> Path:
    tmp = cfg.work_dir.resolve() / "epg-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


@functools.cache
def node_permission() -> tuple[str, ...] | None:
    """The flags that turn on Node's sandbox here, or None if this Node.js has none."""
    for flags in (("--permission", "--allow-net"), ("--permission",), ("--experimental-permission",)):
        try:
            if subprocess.run(["node", *flags, "-e", ""], capture_output=True, timeout=30, check=False).returncode == 0:
                return flags
        except (OSError, subprocess.TimeoutExpired):
            return None
    return None


def _esbuild(cfg: EpgConfig, log: Callable[[str], None]) -> Path | None:
    """kia's own esbuild, a fixed version installed once from npm without running any
    install scripts. None if it can't be installed.

    Never the checkout's own (node_modules/.bin/esbuild): that comes from iptv-org's
    dependencies, and compiling runs it outside Node's sandbox, with kia's access."""
    repo = cfg.repo_dir.resolve()
    tools = repo.parent / "kia-tools"
    ours = tools / "node_modules" / ".bin" / "esbuild"
    try:
        if ours.exists() and subprocess.run([str(ours), "--version"], capture_output=True, text=True,
                                            timeout=60, check=False).stdout.strip() == ESBUILD:  # fmt: skip
            return ours
        log(f"Installing kia's own esbuild ({ESBUILD}) to compile iptv-org/epg's scripts")
        tools.mkdir(parents=True, exist_ok=True)
        subprocess.run(["npm", "install", "--prefix", str(tools), "--no-save", "--no-audit", "--no-fund", "--ignore-scripts",
                        f"esbuild@{ESBUILD}"], env=epg_env(cfg, home=True), check=True, capture_output=True, timeout=600)  # fmt: skip
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        log(f"Couldn't install esbuild ({exc})")
        return None
    return ours if ours.exists() else None


def build_scripts(cfg: EpgConfig, log: Callable[[str], None]) -> bool:
    """Compile the grabber's scripts into .kia/ in the checkout. False if that can't be done."""
    repo = cfg.repo_dir.resolve()
    # Never the ones from an older checkout: they'd run against the new code's dependencies.
    for name in SCRIPTS:
        (repo / ".kia" / f"{name}.cjs").unlink(missing_ok=True)
    if not all((repo / src).exists() for src in SCRIPTS.values()):
        log("Can't compile iptv-org/epg's scripts (they're not where kia expects them); its grabber runs without Node's sandbox")
        return False
    esbuild = _esbuild(cfg, log)
    if esbuild is None:
        log("Can't compile iptv-org/epg's scripts (no esbuild); its grabber runs without Node's sandbox")
        return False
    try:
        for name, src in SCRIPTS.items():
            subprocess.run([str(esbuild), src, "--bundle", "--platform=node", "--format=cjs", "--packages=external",
                            f"--outfile=.kia/{name}.cjs", "--log-level=error"],
                           cwd=repo, env=epg_env(cfg), check=True, capture_output=True, timeout=300)  # fmt: skip
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        for name in SCRIPTS:  # a half-done set is no good either
            (repo / ".kia" / f"{name}.cjs").unlink(missing_ok=True)
        log(f"Couldn't compile iptv-org/epg's scripts ({exc}); its grabber runs without Node's sandbox")
        return False
    return True


def sandboxed(cfg: EpgConfig, name: str, reads: list[Path], writes: list[Path], args: list[str]) -> list[str] | None:
    """The command that runs compiled script `name` in Node's sandbox, or None if it can't."""
    repo = cfg.repo_dir.resolve()
    script = repo / ".kia" / f"{name}.cjs"
    if cfg.runner != "local" or not script.exists() or (flags := node_permission()) is None:
        return None
    allow = [f"--allow-fs-read={p.resolve()}" for p in (repo, *reads)]
    allow += [f"--allow-fs-write={p.resolve()}" for p in (temp_dir(cfg), *writes)]
    return ["node", "--no-warnings", *flags, *allow, str(script), *args]


@dataclass
class GrabResult:
    site: str
    returncode: int
    elapsed: float
    log: Path
    output: Path


def runner_prefix(cfg: EpgConfig, name: str | None = None, reads: tuple[Path, ...] = (),
                  writes: tuple[Path, ...] = ()) -> list[str]:  # fmt: skip
    """Command prefix that runs a process inside the epg checkout. `name` names the Docker
    container, so a download that runs too long can be stopped.

    The container sees the checkout and only the files a run needs: reads (read-only) and
    writes (one folder for this site's guide). Never all of work/: kia reads what's there
    on the host (logs, other sites' guides, the plan of playlists' guides), so code running
    in the container must not be able to plant files or links in it."""
    if cfg.runner == "local":
        return []
    if cfg.runner != "docker":
        raise ValueError(f"unknown epg runner {cfg.runner!r} (use 'docker' or 'local')")
    repo = cfg.repo_dir.resolve()
    # If a folder doesn't exist, Docker creates it owned by root. Create it first so it
    # belongs to the user.
    for folder in writes:
        folder.mkdir(parents=True, exist_ok=True)
    # Run as the calling user so the files it writes belong to them (os.getuid doesn't
    # exist on Windows).
    user = ["-u", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
    mounts = [f"{p.resolve()}:{p.resolve()}:ro" for p in reads] + [f"{p.resolve()}:{p.resolve()}" for p in writes]
    return [
        "docker", "run", "--rm", *user, *(["--name", name] if name else []),
        "-e", "HOME=/tmp",
        "-v", f"{repo}:{repo}",
        *(a for m in mounts for a in ("-v", m)),
        "-w", str(repo),
        cfg.docker_image,
    ]  # fmt: skip


def _git(repo: Path, *args: str, url: str = "") -> None:
    """Run git on the checkout, which third-party code (npm packages) can write to.

    Hooks and fsmonitor are off, so nothing planted in .git/hooks or named in the config
    runs (_reset_git_config rewrites the config). GIT_COMMON_DIR pins git's settings to
    this checkout's own .git: a planted .git/commondir would otherwise point git at a config
    kia never rewrote. Only kia's own settings count (none from this computer's or your
    git config), and only https and the kind of address kia's git_url uses, so no
    "ext::" address can run a command."""
    scheme = url.split("://", 1)[0].lower() if "://" in url else "file"
    allow = ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", f"protocol.{scheme}.allow=always"]
    env = {**os.environ, "GIT_COMMON_DIR": str((repo / ".git").resolve()), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}  # fmt: skip
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *allow, "-C", str(repo), *args],
                   check=True, env=env)  # fmt: skip


def _reset_git_config(repo: Path, url: str) -> None:
    """Rewrite the checkout's git settings from scratch, and drop .git/info/attributes.

    With the Docker runner, iptv-org's code runs with the checkout mounted read-write, so it
    could write settings that make git run a command when kia next updates it: a "filter"
    for files that checkout passes through it, say, set in .git/config and switched on in
    .git/info/attributes. kia needs none of those settings, so it writes only its own."""
    if any(c in url for c in '\n\r"\\'):
        raise OSError(f"the epg git address can't hold line breaks or quotes: {url[:80]!r}")
    git_dir = repo / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        raise OSError(f"{git_dir} isn't a folder")
    # Files that make git read settings, or objects, from somewhere else: commondir (also
    # pinned by GIT_COMMON_DIR in _git), a worktree config, and alternates. And no links:
    # a linked objects/, refs/, or info/ could lead anywhere.
    for name in ("commondir", "config.worktree", "info/attributes", "objects/info/alternates"):
        (git_dir / name).unlink(missing_ok=True)
    for part in ("objects", "refs", "info", "HEAD"):
        if (git_dir / part).is_symlink():
            raise OSError(f"{git_dir / part} is a link")
    config = git_dir / "config"
    config.unlink(missing_ok=True)  # a link there is replaced, never written through
    config.write_text(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n"
        f'[remote "origin"]\n\turl = {url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
    )


def _never_fetched(repo: Path) -> bool:
    """git init ran, but the first fetch didn't finish (no internet at the first start, say),
    so there's nothing checked out. HEAD then names a branch that doesn't exist yet."""
    git_dir = repo / ".git"
    try:
        head = (git_dir / "HEAD").read_text().strip()
    except OSError:
        return False
    if not head.startswith("ref:"):
        return False  # a commit: kia checks out FETCH_HEAD detached
    ref = head.removeprefix("ref:").strip()
    packed = git_dir / "packed-refs"
    return not (git_dir / ref).exists() and not (packed.exists() and ref in packed.read_text())


def ensure_repo(cfg: EpgConfig, update: bool, log: Callable[[str], None]) -> None:
    """Check out cfg.git_ref (branch, tag, or commit) of cfg.git_url into cfg.repo_dir.

    Fetches only on first use or when update is set; reinstalls npm dependencies only when
    package-lock.json changed. If an update fails (GitHub or the network is down), the
    existing checkout is used as it is.
    """
    repo = cfg.repo_dir
    fresh = not (repo / ".git").exists() or _never_fetched(repo)
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
        if (repo / ".git").exists():
            _reset_git_config(repo, cfg.git_url)  # a first fetch that failed: try it again
        else:
            _git(repo, "init", "-q", url=cfg.git_url)
            _git(repo, "remote", "add", "origin", cfg.git_url, url=cfg.git_url)
    else:
        log(f"Updating the epg checkout to {cfg.git_ref}...")
        _reset_git_config(repo, cfg.git_url)
    # Fetching by name works the same for branches, tags, and (on GitHub) commit hashes.
    # "latest" is the remote's HEAD: its default branch, whatever it's called.
    ref = "HEAD" if cfg.git_ref in ("", "latest") else cfg.git_ref
    _git(repo, "fetch", "-q", "--depth", "1", "origin", ref, url=cfg.git_url)
    _git(repo, "checkout", "-q", "--force", "--detach", "FETCH_HEAD", url=cfg.git_url)
    if fresh or lock_before != lock.read_bytes() or not (repo / "node_modules").exists():
        install_deps(cfg, log)
    else:
        # Same dependencies; just refresh the iptv-org API data (channel logos, feeds).
        api_load(cfg, log)


def _env(cfg: EpgConfig, home: bool = False) -> dict:
    """subprocess arguments: the small environment, except for Docker (which runs it apart)."""
    return {"env": epg_env(cfg, home)} if cfg.runner == "local" else {}


def install_deps(cfg: EpgConfig, log: Callable[[str], None]) -> None:
    log("Installing epg dependencies (npm ci)...")
    # --ignore-scripts skips husky, a git hook installer that fails without git (the node
    # image has none), and every package's install scripts, so no third-party code runs
    # here. It also skips the postinstall step, which downloads iptv-org's channel data,
    # so run that separately.
    # On the command line, --git=git beats a git= line in the checkout's own .npmrc, which
    # would name the program npm runs to fetch a git dependency.
    subprocess.run([*runner_prefix(cfg), "npm", "ci", "--ignore-scripts", "--git=git", "--no-audit", "--no-fund"],
                   cwd=cfg.repo_dir, check=True, **_env(cfg, home=True))  # fmt: skip
    api_load(cfg, log)


def api_load(cfg: EpgConfig, log: Callable[[str], None]) -> None:
    """Download iptv-org's API data into the checkout's temp/ folder (in the sandbox)."""
    if cfg.runner == "local":
        build_scripts(cfg, log)
    data = cfg.repo_dir.resolve() / "temp"
    cmd = sandboxed(cfg, "api-load", [], [data], [])
    if cmd:
        data.mkdir(exist_ok=True)
    subprocess.run(cmd or [*runner_prefix(cfg), "npm", "run", "api:load"], cwd=cfg.repo_dir, check=True, **_env(cfg))


def _deadline(cfg: EpgConfig, start: float) -> float | None:
    """When site_time_limit runs out for a site started at start (time.monotonic())."""
    return start + cfg.site_time_limit * 60 if cfg.site_time_limit else None


def grab_site(cfg: EpgConfig, site: str, channels_file: Path) -> GrabResult:
    work = cfg.work_dir.resolve()
    output = work / "guides" / f"{site}.xml"
    # Grab into a staging folder of its own, and only replace the previous guide if this grab
    # succeeds. Its own, so one site's grabber can't write another site's guide.
    staged = work / "guides" / ".staging" / site / f"{site}.xml"
    log_path = work / "logs" / f"{site}.log"
    staged.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    staged.unlink(missing_ok=True)
    start = time.monotonic()
    native = NATIVE_GRABBERS.get(site) if cfg.native_grabbers else None
    if native:
        extra = {}
        if native in (grab_imjh, grab_epgshare01):  # several files: last night's guide to fall back on, and the time limit
            extra = {"previous": output, "deadline": _deadline(cfg, start)}
        with open(log_path, "w") as logf:
            try:
                native(channels_file, staged, cfg.days, lambda msg: print(msg, file=logf, flush=True), **extra)
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
    args = [
        f"--channels={channels_file.resolve()}",
        f"--output={staged}",
        f"--days={cfg.days}",
        f"--maxConnections={cfg.site_connections.get(site, cfg.max_connections)}",
        *([f"--delay={cfg.site_delay_ms[site]}"] if cfg.site_delay_ms.get(site) else []),
    ]  # fmt: skip
    cmd = sandboxed(cfg, "grab", [channels_file], [staged.parent], args)
    if cmd is None:
        cmd = runner_prefix(cfg, container if cfg.runner == "docker" else None, reads=(channels_file,), writes=(staged.parent,))
        cmd += ["npm", "run", "grab", "--", *args]
    with open(log_path, "w") as logf:
        # Its own process group, so a stop reaches npm's Node.js children too.
        proc = subprocess.Popen(cmd, cwd=cfg.repo_dir, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True, **_env(cfg))
        try:
            return proc.wait(timeout=cfg.site_time_limit * 60 if cfg.site_time_limit else None)
        except subprocess.TimeoutExpired:
            stop(proc, container if cfg.runner == "docker" else None)
            print(f"ERR: stopped after {cfg.site_time_limit} minutes (site_time_limit); kept the previous guide", file=logf)
            return 124


def stop(proc: subprocess.Popen, container: str | None) -> None:
    """Stop a download and everything it started."""
    if container:
        subprocess.run(["docker", "kill", container], capture_output=True, check=False)
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
    elif needs_node and cfg.runner == "local":
        build_scripts(cfg, log)  # the checkout may be from before kia compiled them
    if needs_node and cfg.runner == "local" and not sandboxed(cfg, "grab", [], [], []):
        log("iptv-org's grabber runs without Node's sandbox (Node.js 22 or newer and the compiled scripts are needed), so it can read kia's files")

    log(f"Grabbing {cfg.days} days from {len(channel_files)} sites, {cfg.parallel_sites} at a time...")
    results = []
    with ThreadPoolExecutor(cfg.parallel_sites) as pool:
        futures = {pool.submit(grab_site, cfg, site, path): site for site, path in sorted(channel_files.items())}
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
                # Such as docker or npx missing: this site fails, and the others still run.
                site, work = futures[fut], cfg.work_dir.resolve()
                log(f"  {site}: couldn't start ({type(exc).__name__}: {exc})")
                r = GrabResult(site, 1, 0.0, work / "logs" / f"{site}.log", work / "guides" / f"{site}.xml")
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
        if not site.startswith("playlist-") or not is_safe_site(site.removeprefix("playlist-")):
            log(f"  {site[:80]!r}: not a name kia gives a playlist's guide; skipped")
            continue  # the plan is kia's own, but a name only becomes a file name once checked
        output = work / "guides" / f"{site}.xml"
        staged = work / "guides" / ".staging" / site / f"{site}.xml"
        log_path = work / "logs" / f"{site}.log"
        staged.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        with open(log_path, "w") as logf:
            try:
                counts = playlistguide.download(info, staged, cfg.days, lambda m: print(m, file=logf, flush=True),
                                                deadline=_deadline(cfg, start), previous=output)  # fmt: skip
                staged.replace(output)  # only a good download replaces the previous guide
                if not quick:
                    # An address that failed tonight but had our programmes before stays on
                    # the list, so the quick refreshes keep trying it.
                    before = set(useful.get(site, []))
                    useful[site] = [u for u in info["urls"] if counts.get(u) or (u not in counts and u in before)]
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
