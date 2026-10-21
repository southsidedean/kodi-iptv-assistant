"""kodi-iptv-assistant: curate working IPTV streams and build a matching EPG for Kodi."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import tarfile
import socket
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import xml.etree.ElementTree as ET

from kia import __version__, dnscheck, epgmap, grab, htmlpage, m3u, playlistguide, profiles, radio, serve, status, upstream, xmltv
from kia import alerts, auth, channels, pluto, settings, tls
from kia.alerts import EXIT_ALERT
from kia.config import DEFAULT_USER_AGENT, Config, env_bool, load
from kia.curate import Checked, classify, is_excluded, render_markdown, select
from kia.history import History, now_iso
from kia.numbers import ChannelNumbers
from kia.probe import ProbeResult, probe
import contextlib


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def fetch(url: str) -> str:
    path = Path(url)
    if path.exists():
        return decode_text(path.read_bytes())
    req = urllib.request.Request(url, headers={"User-Agent": f"kodi-iptv-assistant/{__version__}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return decode_text(resp.read())


def decode_text(data: bytes) -> str:
    """UTF-8 when the playlist is valid UTF-8, else Windows-1252: some playlists are saved
    that way (apsattv.com's), and read as UTF-8 their accented names turn into "\ufffd"."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def load_entries(cfg: Config, only: list[str] | None) -> list[m3u.Entry]:
    entries: list[m3u.Entry] = []
    for src in cfg.sources:
        if only and src.name not in only:
            continue
        try:
            if src.is_radio_browser:
                found = radio.load(src.params, src.name)
            else:
                text = fetch(src.url)
                found = m3u.parse(text, source=src.name)
                if src.radio:
                    for e in found:
                        e.attrs["radio"] = "true"
                        e.attrs.setdefault("group-title", src.name)
                if cfg.epg.playlist_guides:
                    guides = tuple(m3u.header_guide_urls(text))
                    for e in found:
                        e.guide_urls = guides
        except (OSError, ValueError, radio.RadioError) as exc:
            # Skip it; if that loses many channels, the channels_drop alert says so.
            log(f"{src.name}: could not load ({exc}); skipping")
            continue
        kept = [
            e
            for e in found
            if not is_excluded(e, cfg.exclude_groups, cfg.exclude_labels, cfg.exclude_name_patterns)
        ]
        for e in kept:
            if src.is_local_file:
                e.pinned_number = channel_number(e.attrs.get("tvg-chno"))
            elif cfg.channel_numbers:
                # Only your own files choose channel numbers; a list from the internet could
                # otherwise take numbers you've set or reshuffle the ones kia keeps steady.
                e.attrs.pop("tvg-chno", None)
        log(f"{src.name}: {len(found)} entries, {len(found) - len(kept)} excluded by filters")
        entries.extend(kept)
    return entries


def channel_number(value: str | None) -> int | None:
    try:
        n = int(value or "")
    except ValueError:
        return None
    return n if n > 0 else None


async def probe_all(entries: list[m3u.Entry], cfg: Config, verbose: bool) -> dict[str, ProbeResult]:
    """Probe each unique URL once, `cfg.concurrency` at a time."""
    unique: dict[str, m3u.Entry] = {}
    for e in entries:
        unique.setdefault(e.url, e)
    sem = asyncio.Semaphore(cfg.concurrency)
    # Many servers (radio especially) refuse more than a couple of connections per visitor,
    # so limit tests per server as well as overall.
    per_host: dict[str, asyncio.Semaphore] = {}
    results: dict[str, ProbeResult] = {}
    total = len(unique)
    ok = 0

    def host_limit(e: m3u.Entry) -> asyncio.Semaphore:
        url = urlparse(e.url)
        limit = cfg.per_host_limits.get(url.hostname or "", cfg.per_host)
        return per_host.setdefault(url.netloc, asyncio.Semaphore(limit))

    # Tests on one server start at least host_delay_ms apart: a burst of requests can set
    # off a site's firewall even within the per-server limit. Other servers aren't held up.
    gap = cfg.host_delay_ms / 1000
    last_start: dict[str, float] = {}
    host_locks: dict[str, asyncio.Lock] = {}

    async def spaced(e: m3u.Entry) -> None:
        if gap <= 0:
            return
        host = urlparse(e.url).netloc
        async with host_locks.setdefault(host, asyncio.Lock()):
            loop = asyncio.get_running_loop()
            wait = last_start.get(host, -gap) + gap - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            last_start[host] = loop.time()

    async def test(e: m3u.Entry) -> ProbeResult:
        if not is_stream_url(e.url):
            return ProbeResult(ok=False, error=f"not a network stream (only {', '.join(STREAM_SCHEMES)} are checked)")
        await spaced(e)
        return await probe(
            e.url,
            timeout=cfg.timeout,
            user_agent=e.user_agent or cfg.user_agent,
            referrer=e.referrer,
            require_video=cfg.require_video and not e.is_radio,
            ffprobe=cfg.ffprobe,
        )

    async def one(e: m3u.Entry) -> None:
        nonlocal ok
        async with host_limit(e), sem:
            r = await test(e)
        results[e.url] = r
        ok += r.ok
        done = len(results)
        if verbose:
            detail = (f"{r.height}p {r.video_codec}" if r.height else f"audio {r.audio_codec}") if r.ok else r.error
            log(f"[{done:>{len(str(total))}}/{total}] {'OK  ' if r.ok else 'FAIL'} {e.name}: {detail}")
        elif done % 50 == 0 or done == total:
            log(f"  probed {done}/{total} ({ok} ok)")

    log(f"Probing {total} unique streams, {cfg.concurrency} at a time...")
    await asyncio.gather(*(one(e) for e in unique.values()))

    # A burst of lookups can trip up a home router's or Docker's DNS, and busy servers time
    # out. Give those failures a second, slower try before counting them.
    retry = [unique[url] for url, r in results.items() if not r.ok and is_transient(r.error)]
    if retry:
        log(f"Retrying {len(retry)} streams that failed a name lookup or timed out, more slowly...")
        slow = asyncio.Semaphore(4)

        async def again(e: m3u.Entry) -> None:
            async with host_limit(e), slow:
                results[e.url] = await test(e)

        await asyncio.gather(*(again(e) for e in retry))
        log(f"  {sum(results[e.url].ok for e in retry)} of {len(retry)} worked on the second try")
    return results


# Playlists are third-party content. Anything else (file:, pipe:, a bare path, or a line
# starting with "-" that ffprobe would read as an option) is never handed to ffprobe.
STREAM_SCHEMES = ("http", "https", "rtmp", "rtmps", "rtsp", "rtsps")


def is_stream_url(url: str) -> bool:
    return urlparse(url).scheme.lower() in STREAM_SCHEMES and bool(urlparse(url).netloc)


def play_as_tested(entries: list[m3u.Entry], user_agent: str | None) -> None:
    """Have Kodi introduce itself the way kia did when it tested each stream.

    Kodi sends its own user agent unless the playlist names one, and some servers refuse
    it: jmp2.uk (Pluto TV, Samsung TV Plus, Plex) answers Kodi with 403 but VLC with the
    stream. IPTV Simple Client turns #EXTVLCOPT:http-user-agent into the header it sends.
    """
    if not user_agent:
        return
    for e in entries:
        if not e.user_agent:
            e.vlcopts["http-user-agent"] = user_agent


def write_profiles(cfg: Config, chosen: list[Checked] | None = None) -> None:
    """Write the profile playlists and guides (kia.profiles). After a check, `chosen` notes
    which playlists each kept channel came from; after a guide build it's left out."""
    from kia import api

    with api.LOCK:  # not while the status page is saving a profile
        if chosen is not None:
            profiles.save_sources(cfg.state_dir, [(c.entry, {c.entry.source} | {e.source for e in c.also}) for c in chosen])
        profiles.rebuild(cfg.output_dir, cfg.state_dir, cfg.profiles_file, cfg.epg_url, log)


def partial_run(args: argparse.Namespace) -> bool:
    """A test check of part of the list, which mustn't change what players use."""
    return bool(getattr(args, "limit", None) or getattr(args, "source", None) or getattr(args, "url", None))


def is_transient(error: str | None) -> bool:
    return dnscheck.is_resolve_failure(error) or "timed out" in (error or "").lower()


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file so a web server never serves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def write_report(path: Path, markdown: str) -> None:
    """Write a Markdown report and an .html copy of it for reading in a browser."""
    write_atomic(path, markdown)
    write_atomic(path.with_suffix(".html"), htmlpage.report_page(markdown, path.name))


def lookup_all(hosts: list[str], resolvers: tuple[str, ...]) -> dict[str, dnscheck.PublicLookup]:
    with ThreadPoolExecutor(16) as pool:
        return dict(zip(hosts, pool.map(lambda h: dnscheck.check_public(h, resolvers), hosts), strict=True))


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
    dnscheck.blame_local_dns_carefully(lookups)
    for url, host in failed.items():
        lookup = lookups.get(host)
        if lookup and lookup.verdict == "resolves":
            results[url].error = "LOCAL DNS FAILURE: hostname resolves on public DNS"
        elif lookup and lookup.verdict == "broken-ipv6":
            results[url].error = "the site's own DNS is broken (bad IPv6 answers)"
        elif lookup and lookup.verdict != "unreachable":
            results[url].error = "hostname gone (not on public DNS either)"
    return lookups


def alert_on_local_dns(lookups: dict[str, dnscheck.PublicLookup], context: str, cfg: Config) -> bool:
    """Alert when hostnames failed locally but the backup resolvers can resolve them.

    When the backup resolvers couldn't be reached either, nothing shows that this machine's
    DNS is at fault (it's usually a moment without internet), so those are only logged."""
    local = sorted(h for h, lk in lookups.items() if lk.verdict == "resolves")
    unverified = sorted(h for h, lk in lookups.items() if lk.verdict == "unreachable")
    if not local:
        if unverified:
            log(f"{len(unverified)} hostnames failed to look up, and the public DNS servers couldn't be reached "
                "to double-check (a moment without internet?): " + ", ".join(unverified[:10]))  # fmt: skip
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
    return alerts.notify(
        "dns_local", f"kia {context}: local DNS problem", "\n".join(lines), cfg.state_dir, cfg.alerts, log
    )


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
    chosen = select(checked, {s.name: i for i, s in enumerate(cfg.sources)}, cfg.merge_duplicates)
    # Favorites and numbers chosen on the status page (kia.channels).
    choices = channels.load(cfg.state_dir)
    if choices["removed"] and not partial_run(args):
        back = [c.entry.name for c in chosen if c.entry.key in choices["removed"] and c.status == "ok"]
        chosen = [c for c in chosen if c.entry.key not in choices["removed"] or c.status == "ok"]
        for c in chosen:
            choices["removed"].pop(c.entry.key, None)
        channels.save(cfg.state_dir, choices)
        if back:
            log(f"Back in the playlists, because their streams work again: {', '.join(back)}")
    place = {id(e): i for i, e in enumerate(channels.apply([c.entry for c in chosen], choices))}
    chosen.sort(key=lambda c: place[id(c.entry)])

    # A test run (part of the list) mustn't replace the playlist Kodi is using.
    partial = bool(args.limit or args.source or args.url)
    out = cfg.epg.work_dir / "check-test" if partial else cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)
    if cfg.channel_numbers and not partial:
        numbers = ChannelNumbers(cfg.state_dir / "channel-numbers.json", cfg.recycle_numbers_after_days)
        numbers.assign([c.entry for c in chosen], generated, log)
        numbers.forget_older_than(cfg.history_days)
        numbers.save()
    play_as_tested([c.entry for c in chosen], cfg.user_agent)
    players = [c.entry for c in chosen]
    if cfg.pluto_relay:
        players = pluto.for_players(players, cfg.public_url)
    write_atomic(out / "curated.m3u", m3u.write(players, cfg.epg_url))
    if not partial:
        write_profiles(cfg, chosen)
    write_atomic(
        out / "report.json",
        json.dumps(
            {"generated": generated, "streams": [c.to_dict() for c in checked]},
            indent=1,
        ),
    )
    write_report(out / "report.md", render_markdown(checked, chosen, generated, cfg.dns_resolvers))

    if not partial:
        history.prune(cfg.history_days)
        # For `kia map` and `kia grab`: the ids a merged channel has in other playlists, and
        # which kept channels have a guide of their own.
        also = {c.entry.tvg_id: c.also for c in chosen if c.also and c.entry.tvg_id}
        write_atomic(cfg.epg.work_dir / "aliases.json",
                     json.dumps({tid: [e.tvg_id for e in es] for tid, es in also.items()}, indent=1))  # fmt: skip
        aliases = {tid: [(e.source, e.tvg_id, e.guide_urls) for e in es] for tid, es in also.items()}
        playlistguide.save(
            cfg.epg.work_dir / "playlist-guides.json", playlistguide.plan([c.entry for c in chosen], aliases)
        )
    history.save()

    drop_alert = False if partial else check_count_drop(cfg, "channels", len(chosen), "channels_drop",
                                                          "working channels")  # fmt: skip

    n_ok = sum(c.status == "ok" for c in checked)
    n_flaky = sum(c.status == "flaky" for c in checked)
    log(
        f"\n{len(checked)} entries: {n_ok} ok, {n_flaky} flaky, {len(checked) - n_ok - n_flaky} dead "
        f"-> {len(chosen)} channels in {out / 'curated.m3u'}"
    )
    if partial:
        log(f"Test run: the live playlist in {cfg.output_dir} was left as it was.")
    else:
        update_status(cfg)
    return EXIT_ALERT if dns_alert or drop_alert else 0


def check_count_drop(cfg: Config, key: str, current: int, event: str, what: str) -> bool:
    """Alert when a count fell sharply since the last run; remember the new count either way."""
    state = alerts.load_state(cfg.state_dir)
    previous = state.get(key)
    state[key] = current
    alerts.save_state(cfg.state_dir, state)
    if not alerts.sharp_drop(previous, current, cfg.alerts.drop_percent):
        return False
    return alerts.notify(
        event,
        f"kia: {what} dropped from {previous} to {current}",
        f"{what.capitalize()} fell by {100 - round(100 * current / previous)}% since the last run. "
        "A playlist or guide source may have broken upstream, or this machine's internet "
        "connection had trouble during the run. If you just removed playlists or excluded guide "
        "sites, that explains it and you can ignore this. See the reports for details.",
        cfg.state_dir,
        cfg.alerts,
        log,
    )


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
    log(f"{len(site_channels)} channels across the epg repo's sites; {len(broken)} sites marked broken")

    tvg_ids = [e.tvg_id for e in entries]
    names = {e.tvg_id: xmltv.display_name(e) for e in entries if not e.is_radio}
    aliases_path = epg.work_dir / "aliases.json"
    aliases = json.loads(aliases_path.read_text()) if aliases_path.exists() else {}
    candidates = epgmap.find_candidates(tvg_ids, site_channels, epg, broken, names, aliases)
    if epg.native_grabbers:
        radio = {e.tvg_id: xmltv.display_name(e) for e in entries if e.is_radio}
        candidates.update(epgmap.radio_schedule_candidates(radio, broken | set(epg.exclude_sites)))
    suggestions = (
        epgmap.suggest_by_name(tvg_ids, names, epgmap.usable_channels(site_channels, epg, broken), set(candidates))
        if epg.name_matching
        else []
    )
    # Looser matches you've checked and accepted are used like name matches.
    for s in [s for s in suggestions if s.tvg_id in epg.accept_name_matches and s.channel]:
        candidates[s.tvg_id] = [epgmap.Candidate(s.tvg_id, s.channel, True, "name")]
    suggestions = [s for s in suggestions if s.tvg_id not in candidates]
    per_site = epgmap.write_channel_files(candidates, epg.work_dir / "channels")
    # A playlist's own guide goes first for its channels; kia guide still keeps whichever
    # source has the most listings.
    own = playlistguide.sites_by_channel(playlist_guides(cfg)) if epg.playlist_guides else {}
    order = {tid: [c.channel.site for c in cands] for tid, cands in candidates.items()}
    for tid, sites in own.items():
        order[tid] = sites + order.get(tid, [])
    write_atomic(epg.work_dir / "mapping.json", json.dumps(order, indent=1))
    report = epgmap.render_report(
        [(e.tvg_id, xmltv.display_name(e)) for e in entries],
        candidates,
        per_site,
        broken | set(epg.exclude_sites),
        suggestions,
        own,
    )
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    write_report(cfg.output_dir / "mapping-report.md", report)
    by_name = sum(1 for c in candidates.values() if c[0].how == "name")
    log(
        f"{len(order)} of {len(entries)} channels have guide candidates, {by_name} of them matched by name "
        f"and {len(own)} with their playlist's own guide "
        f"({sum(per_site.values())} site channels across {len(per_site)} sites) "
        f"-> {epg.work_dir / 'channels'}, report in {cfg.output_dir / 'mapping-report.md'}"
    )
    return 0


def playlist_guides(cfg: Config) -> dict:
    return playlistguide.load(cfg.epg.work_dir / "playlist-guides.json")


def cmd_grab(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    if args.days:
        cfg.epg.days = args.days
    grab.ensure_repo(cfg.epg, args.update, log)
    results = grab.grab_all(cfg.epg, args.site, log)
    if cfg.epg.playlist_guides:
        spec = {s: info for s, info in playlist_guides(cfg).items() if not args.site or s in args.site}
        results += grab.grab_playlist_guides(cfg.epg, spec, log)
    if not results:
        if args.site:
            log(f"No channel lists for {', '.join(args.site)}; run `kia map` first, or check the names.")
            return 2
        log("No channel matched a guide site, so there's nothing to download.")
        return 0
    failed = [r for r in results if r.returncode != 0 or not r.output.exists()]
    dns_alert = alert_on_local_dns(report_grab_dns(results, cfg.dns_resolvers), "grab", cfg)
    site_alert = track_site_failures(cfg, results)
    log(f"{len(results) - len(failed)} of {len(results)} sites grabbed into {cfg.epg.work_dir / 'guides'}")
    if failed and len(failed) == len(results):
        return 1
    return EXIT_ALERT if dns_alert or site_alert else 0


def track_site_failures(cfg: Config, results: list[grab.GrabResult]) -> bool:
    """Count consecutive failed grabs per site; alert once when a streak reaches the threshold."""
    state = alerts.load_state(cfg.state_dir)
    streaks: dict[str, int] = state.setdefault("site_failures", {})
    newly_failing = []
    for r in results:
        if r.returncode == 0 and r.output.exists():
            streaks.pop(r.site, None)
            continue
        streaks[r.site] = streaks.get(r.site, 0) + 1
        if streaks[r.site] == cfg.alerts.site_fail_runs:
            newly_failing.append(r)
    alerts.save_state(cfg.state_dir, state)
    if not newly_failing:
        return False
    names = ", ".join(r.site for r in newly_failing)
    return alerts.notify(
        "site_failing",
        f"kia: guide site failing: {names}",
        f"{names} failed {cfg.alerts.site_fail_runs} runs in a row; their channels are using older "
        f"guide data or other sites. Logs: {', '.join(str(r.log) for r in newly_failing)}. "
        "If a site stays broken, add it to KIA_EXCLUDE_SITES.",
        cfg.state_dir,
        cfg.alerts,
        log,
    )


def resolves_locally(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except (OSError, UnicodeError):
        return False


def report_grab_dns(
    results: list[grab.GrabResult], resolvers: tuple[str, ...]
) -> dict[str, dnscheck.PublicLookup]:
    """Double-check hostnames that sites' grabbers couldn't resolve against public DNS."""
    site_hosts = {r.site: dnscheck.hosts_in_log(r.log.read_text(errors="replace")) for r in results if r.log.exists()}
    hosts = sorted({h for hs in site_hosts.values() for h in hs})
    # Grabbers don't retry a failed lookup the way the stream check does, so try each name
    # once more here. Only names that still fail locally can point to a DNS problem.
    with ThreadPoolExecutor(16) as pool:
        still_failing = [h for h, ok in zip(hosts, pool.map(resolves_locally, hosts), strict=True) if not ok]
    if len(still_failing) < len(hosts):
        log(f"{len(hosts) - len(still_failing)} hostnames the grabbers couldn't look up resolve now; ignoring those")
    hosts = still_failing
    site_hosts = {site: hs & set(hosts) for site, hs in site_hosts.items()}
    if not hosts:
        return {}
    log(f"Double-checking {len(hosts)} hostnames the grabbers couldn't resolve against {', '.join(resolvers)}:")
    lookups = lookup_all(hosts, resolvers)
    dnscheck.blame_local_dns_carefully(lookups)
    for site, hs in sorted(site_hosts.items()):
        for h in sorted(hs):
            lk = lookups[h]
            hint = " (site grabber is probably outdated)" if lk.verdict in ("nxdomain", "no-records") else ""
            log(f"  {site}: {lk.summary}{hint}")
    return lookups


def build_guide(
    cfg: Config, entries: list[m3u.Entry], site_files: dict[str, Path], site_order: dict[str, list[str]]
) -> int:
    """Pick the best source per channel from site_files and write guide.xml(.gz) + report."""
    log(f"Scanning {len(site_files)} guide files...")
    site_slots = {}
    for site, path in list(site_files.items()):
        try:
            site_slots[site] = xmltv.scan(path)
        except (ET.ParseError, OSError, EOFError) as exc:
            log(f"  {site}: guide file is damaged ({exc}); skipping it until the next download")
            del site_files[site]
    now = datetime.now(timezone.utc)
    choices = xmltv.choose_sources([e.tvg_id for e in entries], site_slots, site_order, now)
    epg = cfg.epg
    out = cfg.output_dir / "guide.xml"
    xmltv.write_guide(
        out, entries, choices, site_files, epg.placeholders, epg.days, now, epg.placeholder_hours, epg.radio_placeholders
    )
    write_report(cfg.output_dir / "guide-report.md", xmltv.render_report(entries, choices, now_iso()))
    with_data = sum(1 for c in choices.values() if c.site)
    log(
        f"{with_data} of {len(entries)} channels have guide data "
        f"-> {out} (+ .gz), report in {cfg.output_dir / 'guide-report.md'}"
    )
    write_profiles(cfg)  # the profiles' own guides, from the new one
    xmltv.write_preview(out, cfg.output_dir / "guide-preview.json", now)  # "On now" on the status page
    drop_alert = check_count_drop(cfg, "guide_channels", with_data, "guide_drop", "channels with guide data")
    update_status(cfg, guide_updated=now_iso(), coverage=xmltv.coverage(entries, choices))
    return EXIT_ALERT if drop_alert else 0


def cmd_guide(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    epg = cfg.epg
    mapping_path = epg.work_dir / "mapping.json"
    site_order = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
    site_files = {
        p.name.removesuffix(".gz").removesuffix(".xml"): p
        for p in sorted((epg.work_dir / "guides").glob("*.xml*"))
    }
    return build_guide(cfg, load_curated(cfg), site_files, site_order)


# Exit code for `kia sync` when the upstream server can't be reached (and no usable cache).
EXIT_UPSTREAM_UNAVAILABLE = 4


def cmd_sync(args: argparse.Namespace) -> int:
    """Take the guide from another kia server, keeping only this instance's curated channels."""
    cfg = load_config(args)
    if not cfg.upstream_url:
        log("error: no upstream configured (set KIA_UPSTREAM_URL or [upstream] url)")
        return 2
    cached = cfg.epg.work_dir / "upstream" / "guide.xml.gz"
    try:
        upstream.download_guide(cfg.upstream_url, cached)
        log(f"Downloaded the guide from {cfg.upstream_url}")
    except upstream.UpstreamError as exc:
        if cfg.upstream_fallback == "cached" and cached.exists():
            log(f"Upstream unavailable ({exc}); using the guide downloaded {upstream.age(cached)} ago")
        else:
            log(f"Upstream unavailable: {exc}")
            return EXIT_UPSTREAM_UNAVAILABLE
    entries = load_curated(cfg)
    return build_guide(cfg, entries, {"upstream": cached}, {e.tvg_id: ["upstream"] for e in entries})


def cmd_refresh_guide(args: argparse.Namespace) -> int:
    """Refresh the guide between nightly runs, using only the quick sources.

    Some guides reach only hours ahead (i.mjh.nz's Samsung TV Plus guide, about 11), so a
    nightly refresh alone leaves evenings empty. This re-downloads the guides kia reads
    itself (i.mjh.nz, kink.nl, the playlists' own guides), which takes seconds, and rebuilds
    the guide from those and the sites downloaded last night. With an upstream server, it
    copies that server's guide again instead. Streams aren't checked.
    """
    cfg = load_config(args)
    if cfg.upstream_url:
        rc = cmd_sync(args)
        return 0 if rc == EXIT_UPSTREAM_UNAVAILABLE else rc  # the last copy stays in place
    epg = cfg.epg
    quick = sorted(
        site
        for p in (epg.work_dir / "channels").glob("*.channels.xml")
        if epg.native_grabbers and (site := p.name.removesuffix(".channels.xml")) in grab.NATIVE_GRABBERS
    )
    results = grab.grab_all(epg, quick, log) if quick else []
    if epg.playlist_guides:
        results += grab.grab_playlist_guides(epg, playlist_guides(cfg), log, quick=True)
    ok = sum(1 for r in results if r.returncode == 0)
    log(f"Refreshed {ok} of {len(results)} quick guide sources")
    return cmd_guide(args)


def run_pipeline(config: str, update_epg: bool) -> int:
    """check, then the guide: from the upstream server if one is configured and reachable,
    otherwise map -> grab -> guide. Returns 0, EXIT_ALERT, or the first fatal step's code."""
    cfg = load(Path(config))
    worst = 0

    def step(name: str, *extra: str) -> int:
        nonlocal worst
        log(f"=== kia {name} ===")
        rc = main([name, "-c", config, *extra])
        if rc == EXIT_ALERT:
            worst = EXIT_ALERT
            return 0
        return rc

    if rc := step("check"):
        log(f"kia check failed with exit code {rc}; stopping")
        return rc
    if cfg.upstream_url:
        if step("sync") == 0:
            return worst
        log("Falling back to grabbing the guide locally")
    for name in ("map", "grab", "guide"):
        rc = step(name, *(["--update"] if name == "map" and update_epg else []))
        if rc and name == "grab":
            worst = worst or rc  # sites that failed keep their previous guide, so still build one
        elif rc:
            log(f"kia {name} failed with exit code {rc}; stopping")
            return rc
    return worst


def update_status(cfg: Config, **run: object) -> None:
    """Rewrite the status page; a failure here never stops a run."""
    try:
        status.update(cfg.output_dir, cfg.state_dir, cfg.public_url, **run)
    except (OSError, ValueError) as exc:
        log(f"Couldn't update the status page: {exc}")


def run_and_report(config: str, update_epg: bool) -> int:
    """Run the pipeline, alert if it fails or crashes, and send the summary if it's turned on."""
    cfg = load(Path(config))
    start = time.monotonic()
    started = now_iso()
    update_status(cfg, result="running", started=started, finished=None, minutes=None, exit_code=None, next_run=None)
    try:
        rc = run_pipeline(config, update_epg)
    except (Exception, SystemExit) as exc:  # tell a human; the daemon keeps going
        alerts.notify("run_failed", "kia: nightly run crashed", f"{type(exc).__name__}: {exc}",
                      cfg.state_dir, cfg.alerts, log)  # fmt: skip
        update_status(cfg, result="failed", finished=now_iso(), minutes=round((time.monotonic() - start) / 60),
                      exit_code=1)  # fmt: skip
        return 1
    minutes = (time.monotonic() - start) / 60
    result = "ok" if rc == 0 else "alerts" if rc == EXIT_ALERT else "failed"
    update_status(cfg, result=result, finished=now_iso(), minutes=round(minutes), exit_code=rc)
    log(f"Run finished in {minutes:.0f} min with exit code {rc}")
    if rc not in (0, EXIT_ALERT):
        alerts.notify("run_failed", "kia: nightly run failed", f"The run stopped with exit code {rc}; see the kia logs.",
                      cfg.state_dir, cfg.alerts, log)  # fmt: skip
    else:
        state = alerts.load_state(cfg.state_dir)
        alerts.notify(
            "daily_summary",
            "kia: refresh complete",
            f"{state.get('channels', '?')} working channels, {state.get('guide_channels', '?')} with guide data "
            f"({minutes:.0f} min){'; alerts were raised, see the log' if rc == EXIT_ALERT else ''}.",
            cfg.state_dir,
            cfg.alerts,
            log,
        )
    return rc


def cmd_run(args: argparse.Namespace) -> int:
    return run_and_report(args.config, args.update)


def next_run(now: datetime, at: str) -> datetime:
    hour, minute = (int(x) for x in at.split(":"))
    nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return nxt if nxt > now else nxt + timedelta(days=1)


def next_run_local(at: str, now: datetime | None = None) -> datetime:
    """The next --at time in local time, with the UTC offset in force on that day.

    Working it out from an aware "now" would keep today's offset, so the run after a
    daylight-saving change would happen an hour early or late.
    """
    return next_run(now or datetime.now(), at).astimezone()


def wait_until(when: datetime, poll: float = 60.0) -> None:
    """Sleep until a wall-clock time. Polling (rather than one long sleep) means a laptop
    that was asleep or suspended at the scheduled time catches up within a minute of waking."""
    while (remaining := (when - datetime.now().astimezone()).total_seconds()) > 0:
        time.sleep(min(poll, remaining))


def api_config(cfg: Config):
    """The profile editor's settings (kia.api)."""
    from kia import api

    return api.ApiConfig(
        output_dir=cfg.output_dir,
        state_dir=cfg.state_dir,
        profiles_file=cfg.profiles_file,
        epg_url=cfg.epg_url,
        auth=auth.from_env(cfg.state_dir),
        https_port=cfg.https_port,
        on_change=lambda: update_status(cfg),
        sources_file=cfg.sources_file,
        catalog_file=cfg.sources_file.parent / "sources.txt" if cfg.sources_file else None,
        config_file=cfg.config_file,
        test_report=cfg.epg.work_dir / "check-test" / "report.json",
        recycle_numbers_after_days=cfg.recycle_numbers_after_days,
    )


def secure_setup(cfg: Config) -> dict | None:
    """Make the admin password and the certificate if needed, and note the certificate and
    the secure page's address in state/secure.json for the status page."""
    auth.from_env(cfg.state_dir).ensure(log)
    names = tls.names(cfg.public_url, cfg.tls_names)
    info = tls.ensure(cfg.tls_dir, names, log)
    secure = {"url": f"https://{names[0]}:{cfg.https_port}/" if info else None, "tls": info}
    write_atomic(cfg.state_dir / "secure.json", json.dumps(secure, indent=1))
    return info


def write_settings(cfg: Config, run_at: str | None, guide_every: float | None) -> None:
    """The settings kia is running with, for the status page's Details (kia.settings)."""
    shown = {"run_at": run_at, "guide_every": guide_every, "sections": settings.describe(cfg, run_at, guide_every)}
    try:
        write_atomic(cfg.state_dir / "settings.json", json.dumps(shown, indent=1))
    except OSError as exc:
        log(f"Couldn't save the settings for the status page: {exc}")


def cmd_settings(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    try:  # the schedule is the running daemon's, which this command can't see otherwise
        saved = json.loads((cfg.state_dir / "settings.json").read_text())
    except (OSError, ValueError):
        saved = {}
    run_at = saved.get("run_at") or os.environ.get("KIA_RUN_AT")
    every = saved.get("guide_every")
    print(settings.as_text(settings.describe(cfg, run_at, every)), end="")
    return 0


_RELAY: pluto.Relay | None = None


def pluto_relay(cfg: Config) -> pluto.Relay | None:
    """The Pluto TV relay every server in this process shares (None when it's off)."""
    global _RELAY  # noqa: PLW0603
    if not cfg.pluto_relay:
        return None
    if _RELAY is None:
        def fallback_alert(channel: str, minutes: int) -> None:
            alerts.notify("pluto_fallback", "kia: Pluto channel playing without kia's fix",
                          f"Pluto channel {channel} has been on the plain jmp2.uk link for {minutes} min, because "
                          "kia couldn't open a Pluto session of its own. It may freeze at ad breaks until kia can.",
                          cfg.state_dir, cfg.alerts, log)  # fmt: skip

        curated = cfg.output_dir / "curated.m3u"
        known: dict = {"stamp": None, "ids": set()}

        def allowed() -> set[str]:
            """The Pluto channels kia's playlists have, re-read when the playlist changes."""
            try:
                stamp = curated.stat().st_mtime_ns
            except OSError:
                return set()
            if stamp != known["stamp"]:
                known["ids"] = set(re.findall(r"/pluto/([0-9a-f]{24})\.m3u8", curated.read_text()))
                known["stamp"] = stamp
            return known["ids"]

        _RELAY = pluto.Relay(cfg.output_dir / "pluto.json", log, on_fallback=fallback_alert, allowed=allowed)
        _RELAY.save_status(force=True)
        _RELAY.keep_status_fresh()
    return _RELAY


def serve_secure(cfg: Config, bind: str, port: int | None):
    """kia's own HTTPS server (installs without Docker), when there's a certificate."""
    if not port:
        return None
    if not secure_setup(cfg):
        log("Not serving HTTPS: there's no usable certificate (see above)")
        return None
    server = serve.serve_in_background(cfg.output_dir, bind, port, api_config(cfg), tls_dir=cfg.tls_dir,
                                       relay=pluto_relay(cfg))  # fmt: skip
    log(f"Serving the secure status page at https://{bind}:{port}/")
    return server


def cmd_tls(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    names = tls.names(cfg.public_url, cfg.tls_names)
    try:
        if args.self_signed:
            for name in (tls.CERT, tls.KEY):
                if (cfg.tls_dir / name).exists():
                    shutil.copy2(cfg.tls_dir / name, cfg.tls_dir / (name + ".bak"))
            tls.make_self_signed(cfg.tls_dir, names)
            log(f"Made a self-signed certificate for {', '.join(names)} (the previous files are *.bak)")
        info = tls.info(cfg.tls_dir)
    except tls.TlsError as exc:
        log(f"error: {exc}")
        return 1
    secure_setup(cfg)
    update_status(cfg)
    if not info:
        log(f"No certificate in {cfg.tls_dir}. kia makes one when it starts, or run: kia tls --self-signed")
        return 1
    print(f"Certificate: {cfg.tls_dir / tls.CERT}")
    print(f"  Made by:   {'kia (self-signed; renewed automatically)' if info['generated'] else info['issuer']}")
    print(f"  Covers:    {', '.join(info['names']) or info['subject']}")
    print(f"  Expires:   {info['expires']}")
    print(f"  Key:       {'matches' if info['key_matches'] else 'MISSING OR DOES NOT MATCH ' + str(cfg.tls_dir / tls.KEY)}")
    print(f"  Open:      https://{names[0]}:{cfg.https_port}/")
    return 0 if info["key_matches"] else 1


def cmd_backup(args: argparse.Namespace) -> int:
    from kia import backup

    cfg_path = Path(args.config).resolve()
    base = cfg_path.parent
    sources_file = None
    try:
        sources_file = load_config(args).sources_file
    except (OSError, ValueError) as exc:
        log(f"(kia.toml or the sources file has a problem, {exc}; backing up the files anyway)")
    out = Path(args.output) if args.output else backup.default_name(base / "work" / "backups")
    names = backup.make(base, out, sources_file)
    log(f"Backed up {len(names)} files to {out}")
    log("It holds your password and the secure page's certificate key, so keep it somewhere private.")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    from kia import backup

    base = Path(args.to).resolve() if args.to else Path(args.config).resolve().parent
    try:
        restored, _ = backup.restore(Path(args.archive), base, base / "work" / "backups", log)
    except (OSError, ValueError, tarfile.TarError) as exc:
        log(f"error: {exc}")
        return 1
    if not restored:
        log("The backup had nothing to restore.")
        return 1
    log("Start kia again to use them (docker compose up -d).")
    return 0


def cmd_renumber(args: argparse.Namespace) -> int:
    """Number every channel 1, 2, 3 in playlist order, once, keeping chosen numbers."""
    cfg = load_config(args)
    if not (cfg.output_dir / "curated.m3u").exists():
        log("There's no playlist yet; wait for the first refresh.")
        return 1
    from kia import api

    with api.LOCK:
        entries = channels.rebuild(cfg.output_dir, cfg.state_dir, cfg.epg_url, log, renumber=True,
                                   recycle_after_days=cfg.recycle_numbers_after_days)  # fmt: skip
    write_profiles(cfg)
    update_status(cfg)
    radio = sum(1 for e in entries if e.is_radio)
    log(f"Renumbered {len(entries) - radio} TV channels and {radio} radio stations from 1. "
        "In Kodi, clear the TV data on each device so it picks up the new numbers "
        "(Settings > PVR & Live TV > General > Clear data).")  # fmt: skip
    return 0


def cmd_password(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    a = auth.from_env(cfg.state_dir)
    if a.source == "off":
        log("Editing is off (KIA_EDITING=false), so there's no password.")
        return 1
    if a.source == "env":
        log("The password is set by KIA_ADMIN_PASSWORD in .env; change it there and restart kia.")
        return 1
    if not args.reset:
        shown = "is in " + str(a.shown) if a.shown.exists() else "was changed on the status page"
        log(f"The password {shown}. To replace it: kia password --reset")
        return 0
    a.reset(log)
    update_status(cfg)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    secure_setup(cfg)
    serve_secure(cfg, args.bind, args.https_port)
    server = serve.make_server(cfg.output_dir, args.bind, args.port, api_config(cfg), relay=pluto_relay(cfg))
    log(f"Serving {cfg.output_dir} at http://{args.bind}:{args.port}/ (Ctrl-C to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Run the pipeline daily at --at (local time; set TZ), forever."""
    next_run(datetime.now(), args.at)  # validate --at before sleeping
    cfg = load_config(args)
    secure_setup(cfg)
    write_settings(cfg, args.at, args.guide_every)
    update_status(cfg)
    if args.serve:
        serve_secure(cfg, args.bind, args.https_port)
        serve.serve_in_background(cfg.output_dir, args.bind, args.port, api_config(cfg), relay=pluto_relay(cfg))
        log(f"Serving {cfg.output_dir} at http://{args.bind}:{args.port}/")
    elif args.api_port:
        # With Docker, nginx serves the files (HTTP and HTTPS) and forwards /api/ here; this
        # port isn't published, and nginx tells it which requests came over HTTPS.
        serve.serve_in_background(cfg.output_dir, "0.0.0.0", args.api_port, api_config(cfg), trust_proxy=True,  # noqa: S104  # nosec B104
                                  relay=pluto_relay(cfg))  # fmt: skip
        log(f"Profile editor API and Pluto relay on port {args.api_port}")
    run_now = (
        args.run_now
        or env_bool("KIA_RUN_ON_START", False)
        or not (cfg.output_dir / "guide.xml.gz").exists()
    )
    every = timedelta(hours=args.guide_every) if args.guide_every > 0 else None
    while True:
        if not run_now:
            nxt = next_run_local(args.at)
            log(f"Next run at {nxt:%Y-%m-%d %H:%M %Z}")
            update_status(cfg, next_run=nxt.isoformat(timespec="seconds"))
            # Quick guide refreshes until the next full run (which rebuilds the guide anyway).
            while every and (due := guide_age_due(cfg, every)) < nxt - timedelta(minutes=30):
                wait_until(due)
                try:
                    main(["refresh-guide", "-c", args.config])
                except Exception as exc:  # keep the daemon alive
                    log(f"Guide refresh failed: {type(exc).__name__}: {exc}")
                touch_guide(cfg)  # a failed refresh waits a full interval, too, instead of looping
            wait_until(nxt)
        run_now = False
        try:
            secure_setup(cfg)  # renews kia's own certificate before it expires
            write_settings(load_config(args), args.at, args.guide_every)  # kia.toml may have changed
            run_and_report(args.config, update_epg=True)
        except Exception as exc:  # e.g. the config became invalid; keep the daemon alive
            log(f"Run could not start: {type(exc).__name__}: {exc}")


def guide_age_due(cfg: Config, every: timedelta) -> datetime:
    """When the guide is next due a quick refresh: `every` after it was last built."""
    path = cfg.output_dir / "guide.xml.gz"
    built = datetime.fromtimestamp(path.stat().st_mtime).astimezone() if path.exists() else datetime.now().astimezone()
    return built + every


def touch_guide(cfg: Config) -> None:
    path = cfg.output_dir / "guide.xml.gz"
    if path.exists():
        path.touch()


def cmd_test_alert(args: argparse.Namespace) -> int:
    """Send a test notification through every configured channel, ignoring the event list."""
    cfg = load_config(args)
    a = cfg.alerts
    if not a.ntfy_url and not a.command and not a.email:
        log("No notifications are set up. Set KIA_NTFY_URL, KIA_SMTP_HOST and KIA_EMAIL_TO, or KIA_ALERT_COMMAND "
            "(or [alerts] in kia.toml).")  # fmt: skip
        return 1
    messages = []
    test_cfg = alerts.AlertConfig(command=a.command, ntfy_url=a.ntfy_url, email=a.email, events=("daily_summary",))
    alerts.notify("daily_summary", "kia: test notification",
                  "If you can read this, kia's notifications work.", cfg.state_dir, test_cfg, messages.append)  # fmt: skip
    failures = [m for m in messages if "failed" in m or "timed out" in m]
    for m in failures:
        log(m)
    if not failures:
        ways = [n for n, on in (("ntfy", a.ntfy_url), ("email", a.email), ("your command", a.command)) if on]
        log(f"Test notification sent via {' and '.join(ways)}.")
    log(f"Alerts turned on: {', '.join(a.events) or 'none'}")
    return 1 if failures else 0


def cmd_probe(args: argparse.Namespace) -> int:
    user_agent = args.user_agent or DEFAULT_USER_AGENT
    r = asyncio.run(probe(args.url, timeout=args.timeout, user_agent=user_agent, referrer=args.referrer))
    print(json.dumps(r.to_dict(), indent=2))
    return 0 if r.ok else 1


def add_serve_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--bind",
        default=os.environ.get("KIA_BIND") or "127.0.0.1",
        help="address to listen on (default: KIA_BIND or 127.0.0.1; 0.0.0.0 for your whole network)",
    )
    p.add_argument("--port", type=int, default=int(os.environ.get("KIA_PORT") or 8780), help="default: KIA_PORT or 8780")
    p.add_argument("--https-port", type=int, default=int(os.environ.get("KIA_HTTPS_PORT") or 8743),
                   help="the secure status page's port (default: KIA_HTTPS_PORT or 8743; 0 = none)")  # fmt: skip


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

    p = sub.add_parser("tls", help="show the secure status page's certificate, or make a new self-signed one")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--self-signed", action="store_true", help="replace the certificate with a new self-signed one")
    p.set_defaults(func=cmd_tls)

    p = sub.add_parser("password", help="make a new password for editing on the status page")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--reset", action="store_true", help="replace the password with a new one and print it")
    p.set_defaults(func=cmd_password)

    p = sub.add_parser("backup", help="save your settings, playlists, profiles, and choices in one file")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("-o", "--output", help="where to write it (default: work/backups/kia-backup-<date>.tar.gz)")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="put a backup's files back (it backs up the current ones first)")
    p.add_argument("archive", help="the backup file (.tar.gz)")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--to", help="the kia folder to restore into (default: the one kia.toml is in)")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("renumber", help="number every channel 1, 2, 3 in list order, once, keeping chosen numbers")
    p.add_argument("-c", "--config", default="kia.toml")
    p.set_defaults(func=cmd_renumber)

    p = sub.add_parser("settings", help="show the settings kia is running with, and where each is changed")
    p.add_argument("-c", "--config", default="kia.toml")
    p.set_defaults(func=cmd_settings)

    p = sub.add_parser("test-alert", help="send a test notification (ntfy and/or your alert command)")
    p.add_argument("-c", "--config", default="kia.toml")
    p.set_defaults(func=cmd_test_alert)

    p = sub.add_parser("refresh-guide", help="refresh the guide from the quick sources only (between nightly runs)")
    p.add_argument("-c", "--config", default="kia.toml")
    p.set_defaults(func=cmd_refresh_guide)

    p = sub.add_parser("sync", help="take the guide from another kia server (KIA_UPSTREAM_URL)")
    p.add_argument("-c", "--config", default="kia.toml")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("serve", help="serve the output folder over HTTP (for installs without Docker)")
    p.add_argument("-c", "--config", default="kia.toml")
    add_serve_args(p)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("daemon", help="run the full pipeline every day at a set time")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--at", default=os.environ.get("KIA_RUN_AT") or "04:00",
                   help="local time of day, HH:MM (default: KIA_RUN_AT or 04:00)")  # fmt: skip
    p.add_argument(
        "--guide-every",
        type=float,
        default=float(os.environ.get("KIA_GUIDE_REFRESH_HOURS") or 4),
        help="hours between quick guide refreshes, 0 to turn off (default: KIA_GUIDE_REFRESH_HOURS or 4)",
    )
    p.add_argument("--run-now", action="store_true", help="also run once at startup (or set KIA_RUN_ON_START)")
    p.add_argument("--api-port", type=int, default=int(os.environ.get("KIA_API_PORT") or 0),
                   help="serve the status page's profile editor API on this port (Docker sets 8781)")  # fmt: skip
    p.add_argument("--serve", action="store_true", help="also serve the output folder (kia serve)")
    add_serve_args(p)
    p.set_defaults(func=cmd_daemon)

    args = parser.parse_args(argv)
    return args.func(args)
