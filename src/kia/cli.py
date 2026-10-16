"""kodi-iptv-assistant: curate working IPTV streams and build a matching EPG for Kodi."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from kia import __version__, dnscheck, epgmap, grab, m3u, xmltv
from kia.alerts import EXIT_ALERT, raise_alert
from kia.config import Config, load
from kia.curate import Checked, classify, is_excluded, render_markdown, select
from kia.history import History, now_iso
from kia.probe import ProbeResult, probe


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def fetch(url: str) -> str:
    path = Path(url)
    if path.exists():
        return path.read_text()
    req = urllib.request.Request(url, headers={"User-Agent": f"kodi-iptv-assistant/{__version__}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def load_entries(cfg: Config, only: list[str] | None) -> list[m3u.Entry]:
    entries: list[m3u.Entry] = []
    for src in cfg.sources:
        if only and src.name not in only:
            continue
        found = m3u.parse(fetch(src.url), source=src.name)
        kept = [
            e
            for e in found
            if not is_excluded(e, cfg.exclude_groups, cfg.exclude_labels, cfg.exclude_name_patterns)
        ]
        log(f"{src.name}: {len(found)} entries, {len(found) - len(kept)} excluded by filters")
        entries.extend(kept)
    return entries


async def probe_all(entries: list[m3u.Entry], cfg: Config, verbose: bool) -> dict[str, ProbeResult]:
    """Probe each unique URL once, `cfg.concurrency` at a time."""
    unique: dict[str, m3u.Entry] = {}
    for e in entries:
        unique.setdefault(e.url, e)
    sem = asyncio.Semaphore(cfg.concurrency)
    results: dict[str, ProbeResult] = {}
    total = len(unique)
    ok = 0

    async def one(e: m3u.Entry) -> None:
        nonlocal ok
        async with sem:
            r = await probe(
                e.url,
                timeout=cfg.timeout,
                user_agent=e.user_agent or cfg.user_agent,
                referrer=e.referrer,
                require_video=cfg.require_video,
                ffprobe=cfg.ffprobe,
            )
        results[e.url] = r
        ok += r.ok
        done = len(results)
        if verbose:
            detail = f"{r.height}p {r.video_codec}" if r.ok else r.error
            log(f"[{done:>{len(str(total))}}/{total}] {'OK  ' if r.ok else 'FAIL'} {e.name}: {detail}")
        elif done % 50 == 0 or done == total:
            log(f"  probed {done}/{total} ({ok} ok)")

    log(f"Probing {total} unique streams, {cfg.concurrency} at a time...")
    await asyncio.gather(*(one(e) for e in unique.values()))
    return results


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file so a web server never serves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def lookup_all(hosts: list[str], resolvers: tuple[str, ...]) -> dict[str, dnscheck.PublicLookup]:
    with ThreadPoolExecutor(16) as pool:
        return dict(zip(hosts, pool.map(lambda h: dnscheck.check_public(h, resolvers), hosts)))


def double_check_dns(
    results: dict[str, ProbeResult], resolvers: tuple[str, ...]
) -> dict[str, dnscheck.PublicLookup]:
    """Re-resolve hostnames that failed locally against public DNS, and label the failures."""
    failed = {url: urlparse(url).hostname for url, r in results.items() if dnscheck.is_resolve_failure(r.error)}
    hosts = sorted({h for h in failed.values() if h})
    if not hosts:
        return {}
    log(f"Double-checking {len(hosts)} unresolvable hostnames against {', '.join(resolvers)}...")
    lookups = lookup_all(hosts, resolvers)
    for url, host in failed.items():
        lookup = lookups.get(host)
        if lookup and lookup.verdict == "resolves":
            results[url].error = "LOCAL DNS FAILURE: hostname resolves on public DNS"
        elif lookup and lookup.verdict != "unreachable":
            results[url].error = "hostname gone (not on public DNS either)"
    return lookups


def alert_on_local_dns(lookups: dict[str, dnscheck.PublicLookup], context: str, cfg: Config) -> bool:
    """Alert when hostnames failed locally but the backup resolvers can resolve them (or can't be reached)."""
    local = sorted(h for h, lk in lookups.items() if lk.verdict == "resolves")
    unverified = sorted(h for h, lk in lookups.items() if lk.verdict == "unreachable")
    if not local and not unverified:
        return False
    lines = []
    if local:
        lines.append(
            f"{len(local)} hostnames failed on local DNS but resolve on {', '.join(cfg.dns_resolvers)}: "
            + ", ".join(local[:10])
            + (" ..." if len(local) > 10 else "")
        )
    if unverified:
        lines.append(
            f"{len(unverified)} hostnames failed on local DNS and the backup resolvers were unreachable "
            "(network outage?): " + ", ".join(unverified[:10])
        )
    lines.append("Check this machine's DNS (resolv.conf / systemd-resolved, router, ad-blocker) and re-run.")
    raise_alert(f"kia {context}: local DNS problem", "\n".join(lines), cfg.state_dir, cfg.alert_command, log)
    return True


def cmd_check(args: argparse.Namespace) -> int:
    cfg = load(Path(args.config), Path(args.sources) if args.sources else None, args.url)
    if args.concurrency:
        cfg.concurrency = args.concurrency
    if args.timeout:
        cfg.timeout = args.timeout
    if not shutil.which(cfg.ffprobe):
        log(f"error: '{cfg.ffprobe}' not found. Install ffmpeg (brew install ffmpeg / apt install ffmpeg).")
        return 2

    entries = load_entries(cfg, args.source)
    if args.limit:
        entries = entries[: args.limit]
    if not entries:
        log("No entries to check.")
        return 1

    results = asyncio.run(probe_all(entries, cfg, args.verbose))
    dns_alert = alert_on_local_dns(double_check_dns(results, cfg.dns_resolvers), "check", cfg)

    generated = now_iso()
    history = History(cfg.state_dir / "history.json")
    for url, r in results.items():
        history.record(url, r, generated)

    checked = []
    for e in entries:
        r = results[e.url]
        rec = history.get(e.url)
        checked.append(Checked(e, r, classify(r, rec, cfg.grace_failures), r.height or rec.get("height")))
    chosen = select(checked)

    out = cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)
    write_atomic(out / "curated.m3u", m3u.write([c.entry for c in chosen], cfg.epg_url))
    (out / "report.json").write_text(
        json.dumps(
            {"generated": generated, "streams": [c.to_dict() for c in checked]},
            indent=1,
        )
    )
    (out / "report.md").write_text(render_markdown(checked, chosen, generated, cfg.dns_resolvers))

    if not args.limit:
        history.prune()
    history.save()

    n_ok = sum(c.status == "ok" for c in checked)
    n_flaky = sum(c.status == "flaky" for c in checked)
    log(
        f"\n{len(checked)} entries: {n_ok} ok, {n_flaky} flaky, {len(checked) - n_ok - n_flaky} dead "
        f"-> {len(chosen)} channels in {out / 'curated.m3u'}"
    )
    return EXIT_ALERT if dns_alert else 0


def load_config(args: argparse.Namespace) -> Config:
    return load(Path(args.config))


def load_curated(cfg: Config) -> list[m3u.Entry]:
    path = cfg.output_dir / "curated.m3u"
    if not path.exists():
        raise SystemExit(f"error: {path} not found; run `kia check` first")
    return [e for e in m3u.parse(path.read_text()) if e.tvg_id]


def cmd_map(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    epg = cfg.epg
    grab.ensure_repo(epg, args.update, log)
    entries = load_curated(cfg)
    site_channels = epgmap.load_site_channels(epg.repo_dir)
    broken = epgmap.broken_sites(epg.repo_dir)
    log(f"{len(site_channels)} mapped channels across the epg repo's sites; {len(broken)} sites marked broken")

    candidates = epgmap.find_candidates([e.tvg_id for e in entries], site_channels, epg, broken)
    per_site = epgmap.write_channel_files(candidates, epg.work_dir / "channels")
    (epg.work_dir / "mapping.json").write_text(
        json.dumps({tid: [c.channel.site for c in cands] for tid, cands in candidates.items()}, indent=1)
    )
    report = epgmap.render_report(
        [(e.tvg_id, xmltv.display_name(e)) for e in entries], candidates, per_site, broken | set(epg.exclude_sites)
    )
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    (cfg.output_dir / "mapping-report.md").write_text(report)
    log(
        f"{len(candidates)} of {len(entries)} channels have guide candidates "
        f"({sum(per_site.values())} site channels across {len(per_site)} sites) "
        f"-> {epg.work_dir / 'channels'}, report in {cfg.output_dir / 'mapping-report.md'}"
    )
    return 0


def cmd_grab(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    if args.days:
        cfg.epg.days = args.days
    grab.ensure_repo(cfg.epg, args.update, log)
    results = grab.grab_all(cfg.epg, args.site, log)
    failed = [r for r in results if r.returncode != 0 or not r.output.exists()]
    dns_alert = alert_on_local_dns(report_grab_dns(results, cfg.dns_resolvers), "grab", cfg)
    log(f"{len(results) - len(failed)} of {len(results)} sites grabbed into {cfg.epg.work_dir / 'guides'}")
    if failed and len(failed) == len(results):
        return 1
    return EXIT_ALERT if dns_alert else 0


def report_grab_dns(
    results: list[grab.GrabResult], resolvers: tuple[str, ...]
) -> dict[str, dnscheck.PublicLookup]:
    """Double-check hostnames that sites' grabbers couldn't resolve against public DNS."""
    site_hosts = {r.site: dnscheck.hosts_in_log(r.log.read_text(errors="replace")) for r in results if r.log.exists()}
    hosts = sorted({h for hs in site_hosts.values() for h in hs})
    if not hosts:
        return {}
    log(f"Double-checking {len(hosts)} hostnames the grabbers couldn't resolve against {', '.join(resolvers)}:")
    lookups = lookup_all(hosts, resolvers)
    for site, hs in sorted(site_hosts.items()):
        for h in sorted(hs):
            lk = lookups[h]
            hint = " (site grabber is probably outdated)" if lk.verdict in ("nxdomain", "no-records") else ""
            log(f"  {site}: {lk.summary}{hint}")
    return lookups


def cmd_guide(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    epg = cfg.epg
    entries = load_curated(cfg)
    mapping_path = epg.work_dir / "mapping.json"
    site_order = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
    site_files = {
        p.name.removesuffix(".gz").removesuffix(".xml"): p
        for p in sorted((epg.work_dir / "guides").glob("*.xml*"))
    }
    log(f"Scanning {len(site_files)} site guides...")
    site_slots = {site: xmltv.scan(path) for site, path in site_files.items()}

    now = datetime.now(timezone.utc)
    choices = xmltv.choose_sources([e.tvg_id for e in entries], site_slots, site_order, now)
    out = cfg.output_dir / "guide.xml"
    xmltv.write_guide(out, entries, choices, site_files, epg.placeholders, epg.days, now)
    (cfg.output_dir / "guide-report.md").write_text(xmltv.render_report(entries, choices, now_iso()))
    with_data = sum(1 for c in choices.values() if c.site)
    log(
        f"{with_data} of {len(entries)} channels have guide data "
        f"-> {out} (+ .gz), report in {cfg.output_dir / 'guide-report.md'}"
    )
    return 0


PIPELINE = ("check", "map", "grab", "guide")


def run_pipeline(config: str, update_epg: bool) -> int:
    """check -> map -> grab -> guide. Returns 0, EXIT_ALERT, or the first fatal step's code."""
    worst = 0
    for step in PIPELINE:
        log(f"=== kia {step} ===")
        argv = [step, "-c", config] + (["--update"] if step == "map" and update_epg else [])
        rc = main(argv)
        if rc == EXIT_ALERT:
            worst = EXIT_ALERT
        elif rc != 0 and step == "grab":
            # Sites that failed keep their previous guide, so still build one.
            worst = worst or rc
        elif rc != 0:
            log(f"kia {step} failed with exit code {rc}; stopping")
            return rc
    return worst


def cmd_run(args: argparse.Namespace) -> int:
    return run_pipeline(args.config, args.update)


def next_run(now: datetime, at: str) -> datetime:
    hour, minute = (int(x) for x in at.split(":"))
    nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return nxt if nxt > now else nxt + timedelta(days=1)


def cmd_daemon(args: argparse.Namespace) -> int:
    """Run the pipeline daily at --at (local time; set TZ), forever."""
    next_run(datetime.now(), args.at)  # validate --at before sleeping
    cfg = load_config(args)
    run_now = args.run_now or not (cfg.output_dir / "guide.xml.gz").exists()
    while True:
        if not run_now:
            now = datetime.now().astimezone()
            nxt = next_run(now, args.at)
            log(f"Next run at {nxt:%Y-%m-%d %H:%M %Z}")
            time.sleep((nxt - now).total_seconds())
        run_now = False
        start = time.monotonic()
        try:
            rc = run_pipeline(args.config, update_epg=True)
        except (Exception, SystemExit) as exc:  # keep the daemon alive; tell a human
            rc = None
            raise_alert("kia run crashed", f"{type(exc).__name__}: {exc}", cfg.state_dir, cfg.alert_command, log)
        minutes = (time.monotonic() - start) / 60
        if rc is not None:
            log(f"Run finished in {minutes:.0f} min with exit code {rc}")
            if rc not in (0, EXIT_ALERT):
                raise_alert("kia run failed", f"Pipeline exited with code {rc}; see the kia container logs.",
                            cfg.state_dir, cfg.alert_command, log)  # fmt: skip


def cmd_probe(args: argparse.Namespace) -> int:
    r = asyncio.run(probe(args.url, timeout=args.timeout, user_agent=args.user_agent, referrer=args.referrer))
    print(json.dumps(r.to_dict(), indent=2))
    return 0 if r.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kia", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="probe every stream in the configured playlists and write curated.m3u")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--sources", help="playlist sources file (default: sources_file in kia.toml, else sources.txt)")
    p.add_argument("-s", "--source", action="append", help="only check the source with this name (repeatable)")
    p.add_argument(
        "-u", "--url", action="append", help="check this playlist URL or file instead of the sources file (repeatable)"
    )
    p.add_argument("--limit", type=int, help="only check the first N entries (for testing; history isn't pruned)")
    p.add_argument("--concurrency", type=int)
    p.add_argument("--timeout", type=float, help="seconds per stream")
    p.add_argument("-v", "--verbose", action="store_true", help="log every stream result")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("probe", help="probe a single stream URL")
    p.add_argument("url")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--user-agent")
    p.add_argument("--referrer")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("map", help="match curated channels to iptv-org/epg guide sites")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--update", action="store_true", help="git pull the epg checkout first")
    p.set_defaults(func=cmd_map)

    p = sub.add_parser("grab", help="download guide data from each mapped site")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--site", action="append", help="only grab this site (repeatable)")
    p.add_argument("--days", type=int, help="override [epg] days")
    p.add_argument("--update", action="store_true", help="git pull the epg checkout first")
    p.set_defaults(func=cmd_grab)

    p = sub.add_parser("guide", help="merge grabbed guides into output/guide.xml for the curated playlist")
    p.add_argument("-c", "--config", default="kia.toml")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("run", help="full pipeline: check, map, grab, guide")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--update", action="store_true", help="git pull the epg checkout first")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("daemon", help="run the full pipeline every day at a set time")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--at", default="04:00", help="local time of day, HH:MM (default 04:00)")
    p.add_argument("--run-now", action="store_true", help="also run once at startup")
    p.set_defaults(func=cmd_daemon)

    args = parser.parse_args(argv)
    return args.func(args)
