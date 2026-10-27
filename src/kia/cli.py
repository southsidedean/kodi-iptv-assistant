"""kodi-iptv-assistant: curate working IPTV streams and build a matching EPG for Kodi."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import http.client
import json
import math
import os
import re
import shutil
import tarfile
import socket
import sys
import time
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

import xml.etree.ElementTree as ET

from kia import __version__, dnscheck, epgmap, epgshare, grab, htmlpage, m3u, metrics, playlistguide, profiles, radio, serve, status, upstream, xmltv
from kia import alerts, auth, channels, lan, locks, native, newchannels, pluto, settings, stack, tls
from kia import name as kia_name
from kia.alerts import EXIT_ALERT
from kia.config import DEFAULT_USER_AGENT, Config, env_bool, load
from kia.curate import Checked, classify, is_excluded, render_markdown, select
from kia.history import History, now_iso
from kia.numbers import ChannelNumbers
from kia.probe import GUARD_REFUSED, ProbeResult, probe, probe_guarded
from kia.files import read_object, temp_name
import contextlib


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# A download that fails is tried again after these waits (a server that answers "not
# found" or "forbidden" isn't: trying again won't change that).
FETCH_WAITS = (10, 30)
# When a channel source still can't be downloaded, kia uses its last good copy, up to a week
# old, rather than dropping all of its channels until the next night.
SAVED_COPY_DAYS = 7


# A playlist's size and download time: the biggest real ones (iptv-org's whole index) are
# about 10 MB and take seconds. A server that never stops, or trickles a byte at a time,
# can't fill kia's memory or hold the nightly refresh.
MAX_PLAYLIST = 64 * 1024 * 1024
PLAYLIST_TIME = 300


def fetch(url: str, guarded: bool = True) -> str:
    """A playlist: a file, or a download. guarded: it isn't yours (config.Source.is_yours),
    so it's downloaded through kia's outbound guard, and neither it nor a redirect can
    reach your network (kia.native.opened)."""
    path = Path(url)
    if path.exists():
        return decode_text(path.read_bytes())
    for wait in (*FETCH_WAITS, None):
        try:
            return decode_text(native.download(url, guarded, MAX_PLAYLIST, PLAYLIST_TIME))
        except urllib.error.HTTPError as exc:
            if (exc.code < 500 and exc.code != 429) or wait is None:
                raise
            exc.close()  # the error page, which isn't read
        except (OSError, http.client.HTTPException):
            # HTTPException: a server that cut its answer short, or sent no proper reply.
            if wait is None:
                raise
        time.sleep(wait)
    raise AssertionError("unreachable")


def fetch_source(cfg: Config, src) -> str:
    """A channel source's playlist; its last good copy if it can't be downloaded now."""
    if src.is_local_file:
        return fetch(src.url)
    saved = cfg.epg.work_dir / "sources" / f"{src.name}.m3u"
    try:
        text = fetch(src.url, guarded=not src.is_yours)
        # A server can answer 200 with an error page or an empty file. That's a failed
        # download too: saving it would lose the last good copy, and the source's channels.
        # (Any line a playlist doesn't mark is read as a channel, so look for a stream address.)
        if not any(is_stream_url(e.url) for e in m3u.parse(text, source=src.name)):
            raise ValueError("what came back has no channels in it")
    except (OSError, ValueError, http.client.HTTPException) as exc:
        age = time.time() - saved.stat().st_mtime if saved.exists() else None
        if age is None or age > SAVED_COPY_DAYS * 86400:
            raise
        log(f"{src.name}: could not download ({exc}); using the copy from {age / 3600:.0f} hours ago")
        return saved.read_text(encoding="utf-8")
    saved.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(saved)  # a check run by hand (kia check -u) can save it at the same time
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(saved)
    return text


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
        yours = src.is_yours  # looked up once per playlist, not per channel
        try:
            if src.is_stations:
                found = radio.load_stations(src.url, src.params, src.name)
            else:
                text = fetch_source(cfg, src)
                found = m3u.parse(text, source=src.name)
                if src.radio:
                    for e in found:
                        e.attrs["radio"] = "true"
                        e.attrs.setdefault("group-title", src.name)
                if cfg.epg.playlist_guides:
                    guides = tuple(m3u.header_guide_urls(text))
                    if not yours:
                        # The guides a playlist from the internet names are downloaded too, so
                        # the same rule as for its streams (kia.lan): none on your network.
                        # (An address Python can't read counts as local: it's left out too.)
                        local = [u for u in guides if not lan.host_of(u) or lan.is_local(lan.host_of(u))]
                        if local:
                            log(f"{src.name}: left out {len(local)} of its guides, on your own network: {local[0][:80]}")
                            guides = tuple(u for u in guides if u not in local)
                    for e in found:
                        e.guide_urls = guides
        except (OSError, ValueError, http.client.HTTPException, radio.RadioError) as exc:
            # Skip it; if that loses many channels, the channels_drop alert says so.
            log(f"{src.name}: could not load ({exc}); skipping")
            continue
        kept = [
            e
            for e in found
            if not is_excluded(e, cfg.exclude_groups, cfg.exclude_labels, cfg.exclude_name_patterns)
        ]
        for e in kept:
            e.yours = yours
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
        host = lan.host_of(e.url)  # "" for an address that isn't a stream: it isn't tested anyway
        return per_host.setdefault(host, asyncio.Semaphore(cfg.per_host_limits.get(host, cfg.per_host)))

    # Tests on one server start at least host_delay_ms apart: a burst of requests can set
    # off a site's firewall even within the per-server limit. Other servers aren't held up.
    gap = cfg.host_delay_ms / 1000
    last_start: dict[str, float] = {}
    host_locks: dict[str, asyncio.Lock] = {}

    async def spaced(e: m3u.Entry) -> None:
        if gap <= 0:
            return
        host = lan.host_of(e.url)
        async with host_locks.setdefault(host, asyncio.Lock()):
            loop = asyncio.get_running_loop()
            wait = last_start.get(host, -gap) + gap - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            last_start[host] = loop.time()

    # A stream kept from more than one playlist may be tested if any of them is yours.
    yours = {e.url for e in entries if e.yours}
    local_hosts: dict[str, bool] = {}

    async def test(e: m3u.Entry) -> ProbeResult:
        if not is_stream_url(e.url):
            return ProbeResult(ok=False, error=f"not a network stream (only {', '.join(STREAM_SCHEMES)} are checked)")
        if e.url not in yours:
            host = lan.host_of(e.url)
            if host not in local_hosts:
                local_hosts[host] = await asyncio.to_thread(lan.is_local, host)
            if local_hosts[host]:
                return ProbeResult(ok=False, error=LAN_REFUSED)
        await spaced(e)
        how = {"timeout": cfg.timeout, "user_agent": e.user_agent or cfg.user_agent, "referrer": e.referrer,
               "require_video": cfg.require_video and not e.is_radio, "ffprobe": cfg.ffprobe}  # fmt: skip
        if e.url in yours:
            return await probe(e.url, **how)
        # From a playlist that isn't yours, through kia's outbound guard (kia.guard), which
        # checks every connection ffprobe makes: redirects and HLS pieces included.
        return await probe_guarded(e.url, run=probe, **how)

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
        slow = asyncio.Semaphore(min(4, cfg.concurrency))

        async def again(e: m3u.Entry) -> None:
            async with host_limit(e), slow:
                results[e.url] = await test(e)

        await asyncio.gather(*(again(e) for e in retry))
        log(f"  {sum(results[e.url].ok for e in retry)} of {len(retry)} worked on the second try")
    return results


# kia.lan: a playlist from the internet can't send kia's tests onto your network.
LAN_REFUSED = ("on your own network, and the playlist comes from the internet, so kia doesn't test it "
               "(add the word lan to the playlist's line in the sources file to allow it)")  # fmt: skip

# Playlists are third-party content. Anything else (file:, pipe:, a bare path, or a line
# starting with "-" that ffprobe would read as an option) is never handed to ffprobe.
STREAM_SCHEMES = ("http", "https", "rtmp", "rtmps", "rtsp", "rtsps")


def is_stream_url(url: str) -> bool:
    """A network stream kia may hand to ffprobe: one of STREAM_SCHEMES, with a host. An
    empty host ("http://:9090/x") would make ffmpeg connect to this computer itself, past
    kia.lan's guard, and an address Python can't read isn't one either."""
    try:
        return urlparse(url).scheme.lower() in STREAM_SCHEMES and bool(urlparse(url).hostname)
    except ValueError:
        return False


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

    api.LOCK.configure(cfg.state_dir)
    with api.LOCK:  # not while the Dashboard is saving a profile
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
    tmp = temp_name(path)
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
    lines = [
        f"{len(local)} hostnames failed on local DNS but resolve on {', '.join(cfg.dns_resolvers)}: "
        + ", ".join(local[:10])
        + (" ..." if len(local) > 10 else "")
    ]
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
    # Favorites and numbers chosen on the Dashboard (kia.channels).
    # Choices, numbers, and the playlist are written together, never while the Dashboard
    # is saving a change (kia.api.LOCK), so neither undoes the other.
    from kia import api as api_module

    # Pluto's own links, looked up first: they take a request each, and the Dashboard's
    # changes wait while the lock below is held.
    partners = {}
    if cfg.pluto_relay and cfg.public_url and not partial_run(args):
        partners = pluto.partner_channels([c.entry for c in chosen], log)
    api_module.LOCK.configure(cfg.state_dir)
    with api_module.LOCK:
        choices = channels.load(cfg.state_dir)
        if choices["removed"] and not partial_run(args):
            back = channels.count_good_nights(choices, {c.entry.key: c.status for c in chosen})
            chosen = [c for c in chosen if c.entry.key not in choices["removed"] or c.entry.key in back]
            names = [choices["removed"].pop(key)["name"] for key in back]
            channels.save(cfg.state_dir, choices)
            if names:
                log(f"Back in the playlists, because their streams worked {channels.BACK_AFTER_NIGHTS} nights "
                    f"in a row: {', '.join(names)}")  # fmt: skip
        place = {id(e): i for i, e in enumerate(channels.apply([c.entry for c in chosen], choices))}
        chosen.sort(key=lambda c: place[id(c.entry)])

        # A test run (part of the list) mustn't replace the playlist Kodi is using.
        partial = partial_run(args)
        out = cfg.epg.work_dir / "check-test" if partial else cfg.output_dir
        out.mkdir(parents=True, exist_ok=True)
        if cfg.channel_numbers and not partial:
            numbers = ChannelNumbers(cfg.state_dir / "channel-numbers.json", cfg.recycle_numbers_after_days)
            numbers.assign([c.entry for c in chosen], generated, log)
            numbers.forget_older_than(cfg.history_days)
            numbers.save()
        play_as_tested([c.entry for c in chosen], cfg.user_agent)
        players = [c.entry for c in chosen]
        if cfg.pluto_relay and cfg.public_url and not partial:
            players = [dataclasses.replace(e) for e in players]  # the report keeps the original links
            pluto.adopt_partner_channels(players, log, found=partners)
        if cfg.pluto_relay:
            players = pluto.for_players(players, cfg.public_url)
        write_atomic(out / "curated.m3u", m3u.write(players, cfg.epg_url))
        if not partial:
            write_profiles(cfg, chosen)
            # What's new and gone tonight; the nightly run sends it once the guide is built.
            newchannels.track(cfg.state_dir, [c.entry for c in chosen], set(choices["removed"]), generated, cfg.history_days)
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
    if not partial:  # a test check ("check now") mustn't overwrite the nightly history
        history.save()

    drop_alert = False if partial else check_count_drop(cfg, "channels", len(chosen), "channels_drop",
                                                          "working channels")  # fmt: skip
    lan_alert = False if partial else track_playlists_on_your_network(cfg, entries, results)

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
    return EXIT_ALERT if dns_alert or drop_alert or lan_alert else 0


def track_playlists_on_your_network(cfg: Config, entries: list[m3u.Entry], results: dict[str, ProbeResult]) -> bool:
    """Alert when a playlist from the internet points kia at your own network: a stream's
    address (kia.lan) or where it led (kia.guard). Once a playlist, until a night it doesn't,
    so one that always does isn't an alert every night."""
    counts: dict[str, int] = {}
    for e in entries:
        r = results.get(e.url)
        if r is not None and r.error in (LAN_REFUSED, GUARD_REFUSED):
            counts[e.source] = counts.get(e.source, 0) + 1
    state = alerts.load_state(cfg.state_dir)
    before = set(state["lan_playlists"])
    state["lan_playlists"] = sorted(counts)
    alerts.save_state(cfg.state_dir, state)
    new = [s for s in sorted(counts) if s not in before]
    if not new:
        return False
    found = "; ".join(f"{s}: {counts[s]} stream{'' if counts[s] == 1 else 's'}" for s in new)
    return alerts.notify(
        "playlist_lan",
        f"kia: a playlist from the internet pointed at your network: {', '.join(new)}",
        f"{found}. kia didn't test those streams: their addresses, or where they led (a redirect, an HLS piece, "
        "or a name that looked up differently), were on your own network, and the playlist comes from the "
        "internet. Usually that's a mistake in the playlist, but it can be one trying to reach your router or "
        "other devices. If the playlist is yours and its streams really are on your network, add the word lan "
        "to its line in the sources file. The report lists each stream.",
        cfg.state_dir,
        cfg.alerts,
        log,
    )


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
    if epg.native_grabbers and epgshare.SITE not in broken | set(epg.exclude_sites):
        # epgshare01's own lists, for the countries in the playlists, in place of iptv-org's
        # (whose feed names are out of date); kia reads its guides itself (kia.epgshare).
        countries = {epgmap.country(e.tvg_id) for e in entries if not e.is_radio} - {""}
        lists = epgshare.load_lists(epg.work_dir, countries, log)
        if lists:
            site_channels = [sc for sc in site_channels if sc.site != epgshare.SITE] + epgshare.site_channels(lists)

    tvg_ids = [e.tvg_id for e in entries]
    names = {e.tvg_id: xmltv.display_name(e) for e in entries if not e.is_radio}
    aliases_path = epg.work_dir / "aliases.json"
    aliases = json.loads(aliases_path.read_text()) if aliases_path.exists() else {}
    candidates = epgmap.find_candidates(tvg_ids, site_channels, epg, broken, names, aliases)
    if epg.native_grabbers:
        radio = {e.tvg_id: xmltv.display_name(e) for e in entries if e.is_radio}
        candidates.update(epgmap.radio_schedule_candidates(radio, broken | set(epg.exclude_sites)))
    # Pluto TV channels carry Pluto's channel id in their address: i.mjh.nz's Pluto guide by
    # that id, whatever name or id the playlist gives the channel.
    pluto_ids = {e.tvg_id: (cid, xmltv.display_name(e)) for e in entries
                 if not e.is_radio and (cid := pluto.any_channel_id(e.url, cfg.public_url))}  # fmt: skip
    epgmap.pluto_candidates(pluto_ids, candidates, broken | set(epg.exclude_sites))
    suggestions = (
        epgmap.suggest_by_name(tvg_ids, names, epgmap.usable_channels(site_channels, epg, broken), set(candidates),
                               set(epg.ignore_name_matches))
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
    # Which source each channel got, for kia doctor ("why does this channel show only its name?").
    write_atomic(epg.work_dir / "guide-choices.json", json.dumps(
        {c.tvg_id: {"site": c.site, "hours_24h": c.hours_24h, "programs": c.programmes,
                    "ends": c.ends.isoformat() if c.ends else None} for c in choices.values()}, indent=1))  # fmt: skip
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
    xmltv.write_preview(out, cfg.output_dir / "guide-preview.json", now)  # "On now" on the Dashboard
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
        if epg.native_grabbers and (site := p.name.removesuffix(".channels.xml")) in native.QUICK_GRABBERS
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
            worst = worst or EXIT_ALERT  # a failure already noted (a grab) stays the result
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
            # Sites that failed keep their previous guide, so still build one. A failure counts
            # over an alert: the night's result is "failed", not "working, with alerts".
            worst = rc if worst in (0, EXIT_ALERT) else worst
        elif rc:
            log(f"kia {name} failed with exit code {rc}; stopping")
            return rc
    return worst


def update_status(cfg: Config, **run: object) -> None:
    """Rewrite the Dashboard; a failure here never stops a run."""
    try:
        status.update(cfg.output_dir, cfg.state_dir, cfg.public_url, **run)
    except Exception as exc:  # any: the run's work is done, and the Dashboard is only its report
        log(f"Couldn't update the Dashboard: {type(exc).__name__}: {exc}")


def run_and_report(config: str, update_epg: bool) -> int:
    """Run the pipeline, alert if it fails or crashes, and send the summary and the night's
    new and gone channels if they're turned on."""
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
        # Which channels found guide data tonight, for [alerts] new_channels_with_guide.
        guided = {k: bool(v.get("site")) for k, v in read_object(cfg.epg.work_dir / "guide-choices.json").items() if isinstance(v, dict)}
        newchannels.notify(cfg.state_dir, guided, lambda event, title, message: alerts.notify(
            event, title, message, cfg.state_dir, cfg.alerts, log), cfg.alerts.new_with_guide, cfg.alerts.new_categories)  # fmt: skip
    return rc


def cmd_run(args: argparse.Namespace) -> int:
    return run_and_report(args.config, args.update)


def next_run(now: datetime, at: str) -> datetime:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", at.strip())
    if not m:
        raise ValueError(f"--at {at!r} (KIA_RUN_AT in .env): use the 24-hour time HH:MM, such as 04:00")
    hour, minute = int(m[1]), int(m[2])
    nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return nxt if nxt > now else nxt + timedelta(days=1)


def next_run_local(at: str, now: datetime | None = None) -> datetime:
    """The next --at time in local time, with the UTC offset in force on that day.

    Working it out from an aware "now" would keep today's offset, so the run after a
    daylight-saving change would happen an hour early or late.
    """
    return next_run(now or datetime.now(), at).astimezone()


def missed_run(cfg: Config, at: str, now: datetime | None = None) -> bool:
    """Whether the last scheduled run didn't happen, because kia wasn't running then (a
    computer that's off at night). kia then refreshes when it starts, instead of a day later.
    now: local time, without a time zone (for tests)."""
    last_due = (next_run(now or datetime.now(), at) - timedelta(days=1)).astimezone()
    try:
        started = json.loads((cfg.state_dir / "run.json").read_text())["started"]
        return datetime.fromisoformat(started) < last_due - timedelta(minutes=5)
    except (OSError, ValueError, KeyError, TypeError):
        return False  # no record of a run: a new install, which runs anyway when it has no guide


def interrupted_run(cfg: Config) -> bool:
    """Whether a refresh was cut short: still marked as running when kia starts (the
    computer was shut down or kia stopped part way). kia has only just started, so none of
    its refreshes can be running yet, and one started by hand inside its container
    (docker compose exec) stopped when kia did."""
    try:
        return json.loads((cfg.state_dir / "run.json").read_text()).get("result") == "running"
    except (OSError, ValueError, AttributeError):
        return False


def wait_until(when: datetime, poll: float = 60.0) -> None:
    """Sleep until a wall-clock time. Polling (rather than one long sleep) means a laptop
    that was asleep or suspended at the scheduled time catches up within a minute of waking."""
    while (remaining := (when - datetime.now().astimezone()).total_seconds()) > 0:
        time.sleep(min(poll, remaining))


def api_config(cfg: Config):
    """The Dashboard API's settings (kia.api)."""
    from kia import api

    return api.ApiConfig(
        output_dir=cfg.output_dir,
        state_dir=cfg.state_dir,
        profiles_file=cfg.profiles_file,
        epg_url=cfg.epg_url,
        auth=auth.from_env(cfg.state_dir),
        https_port=cfg.https_port,
        on_change=lambda: refresh_settings_and_status(_reloaded(cfg)),
        sources_file=cfg.sources_file,
        catalog_file=cfg.sources_file.parent / "sources.txt" if cfg.sources_file else None,
        config_file=cfg.config_file,
        test_report=cfg.epg.work_dir / "check-test" / "report.json",
        recycle_numbers_after_days=cfg.recycle_numbers_after_days,
        channel_numbers=cfg.channel_numbers,
        require_video=cfg.require_video,
        user_agent=cfg.user_agent,
        ffprobe=cfg.ffprobe,
        alert=lambda event, title, message: alerts.notify(event, title, message, cfg.state_dir, cfg.alerts, log),
    )


def secure_setup(cfg: Config) -> dict | None:
    """Make the admin password and the certificate if needed, and note the certificate and
    the secure page's address in state/secure.json for the Dashboard."""
    auth.from_env(cfg.state_dir).ensure(log)
    names = tls.names(cfg.public_url, cfg.tls_names)
    info = tls.ensure(cfg.tls_dir, names, log)
    secure = {"url": f"https://{tls.in_url(names[0])}:{cfg.https_port}/" if info else None, "tls": info}
    write_atomic(cfg.state_dir / "secure.json", json.dumps(secure, indent=1))
    return info


def env_private(path: Path) -> bool | None:
    """Whether .env (kia's password, alert and Plex settings) is readable only by its owner.
    None when there's no .env (kia without Docker may not use one)."""
    try:
        return path.stat().st_mode & 0o077 == 0
    except OSError:
        return None


def write_settings(cfg: Config, run_at: str | None, guide_every: float | None) -> None:
    """The settings kia is running with, for the Dashboard's System page (kia.settings)."""
    shown = {"run_at": run_at, "guide_every": guide_every, "sections": settings.describe(cfg, run_at, guide_every),
             "env_private": env_private(cfg.state_dir.parent / ".env")}  # fmt: skip
    try:
        write_atomic(cfg.state_dir / "settings.json", json.dumps(shown, indent=1))
    except OSError as exc:
        log(f"Couldn't save the settings for the Dashboard: {exc}")


def _reloaded(cfg: Config) -> Config:
    """kia.toml as it is now (it may have changed since kia started), or as it was if it
    can't be read: the System page shows what the next refresh uses."""
    try:
        return load(cfg.config_file) if cfg.config_file else cfg
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return cfg


def refresh_settings_and_status(cfg: Config) -> None:
    """After a change on the Dashboard (a new password, say): the settings shown on
    the System page and the Getting started checklist read them, so rewrite both."""
    saved = read_object(cfg.state_dir / "settings.json")
    write_settings(cfg, saved.get("run_at"), saved.get("guide_every"))
    update_status(cfg)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Plain answers to "why?" from the files kia keeps (kia.doctor). 1 when something needs you."""
    from kia import doctor

    cfg = load_config(args)
    topic, words = (args.what[0], args.what[1:]) if args.what else ("", [])
    if topic == "channel":
        reports = doctor.channel(cfg, " ".join(words))
    elif topic == "guide":
        reports = [doctor.guide(cfg)]
    elif topic == "pluto":
        reports = [doctor.pluto(cfg)]
    elif topic == "setup":
        reports = [doctor.setup(cfg)]
    elif topic == "plex":
        reports = [doctor.plex(cfg)]
    elif topic == "hardware":
        reports = [doctor.hardware(cfg)]
    elif topic == "jellyfin":
        reports = [doctor.jellyfin(cfg)]
    elif not topic:
        reports = [doctor.overview(cfg)]
    else:
        log(f"kia doctor doesn't know {topic!r}. Try: kia doctor, kia doctor channel <name>, kia doctor guide, kia doctor pluto, kia doctor setup, kia doctor plex, kia doctor hardware, or kia doctor jellyfin")
        return 2
    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=1))
    else:
        print(doctor.render(reports), end="")
    return 1 if any(r.worst == doctor.PROBLEM for r in reports) else 0


def cmd_find(args: argparse.Namespace) -> int:
    """Shows in the guides (kia.shows): named for the words first, then soonest. 1 when nothing matches."""
    from kia import shows

    if not args.words and not args.category:
        log("kia find needs some words, a --category, or both. Example: kia find NFL")
        return 2
    found = shows.search(load_config(args), " ".join(args.words), args.category, args.hours)
    if args.json:
        print(json.dumps(found, indent=1))
    else:
        for s in found["shows"]:
            number = f" {s['number']}" if s["number"] else ""
            episode = f": {s['episode']}" if s.get("episode") else ""
            print(f"{s['start'][:16].replace('T', ' ')} to {s['end'][11:16]}  {s['title']}{episode}  ({s['channel']}{number}, {s['where']})")
        for note in found.get("notes", []):
            print(note)
    return 0 if found["shows"] else 1


def cmd_settings(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    saved = read_object(cfg.state_dir / "settings.json")  # the running daemon's schedule, which this command can't see otherwise
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
    log(f"Serving the secure Dashboard at https://{tls.in_url(bind)}:{port}/")
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
    print(f"  Open:      https://{tls.in_url(names[0])}:{cfg.https_port}/")
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
    except (OSError, ValueError, EOFError, tarfile.TarError) as exc:  # EOFError: a cut-short archive
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
    if not cfg.channel_numbers:
        log("Channel numbers are off (channel_numbers = false in kia.toml), so there's nothing to renumber.")
        return 1
    if not (cfg.output_dir / "curated.m3u").exists():
        log("There's no playlist yet; wait for the first refresh.")
        return 1
    from kia import api

    api.LOCK.configure(cfg.state_dir)
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


def cmd_mcp(args: argparse.Namespace) -> int:
    """kia's MCP server over standard input and output (MCP's stdio transport, kia.mcp): for an
    assistant on this computer, which starts it as a program. No certificate or token: whoever
    can start kia here can read its files anyway. Standard output carries only MCP messages,
    one per line, so kia's own lines go to standard error."""
    from kia import mcp

    path = Path(args.config)

    def note(text: str) -> None:
        print(text, file=sys.stderr, flush=True)

    def settings() -> object | None:
        try:
            return api_config(load(path))
        except (OSError, ValueError) as exc:  # the tools say so when they're used; connecting still works
            note(f"kia's settings couldn't be read: {exc}")
            return None

    api_cfg = settings()
    # Bytes in and out, as MCP's stdio is UTF-8 whatever this computer's own setting: a byte
    # that isn't UTF-8 gets a parse error back instead of ending the server, and a channel
    # name in any script can be written.
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        if api_cfg is None:
            api_cfg = settings()  # fixed since: the tools that need it work again
        _status, answer = mcp.handle(lambda: load(path), api_cfg, {}, line, note, "stdio")
        if answer is not None:
            sys.stdout.buffer.write((json.dumps(answer, ensure_ascii=False) + "\n").encode("utf-8"))  # one line each
            sys.stdout.buffer.flush()
    return 0


def cmd_mcp_token(args: argparse.Namespace) -> int:
    """Turn on kia's MCP server by making its token, show it, replace it, or turn it off (kia.mcp)."""
    from kia import mcp

    cfg = load_config(args)
    if args.gateway:
        return _gateway_setup(cfg, args.for_)
    path = cfg.state_dir / mcp.TOKEN_FILE
    if args.off:
        path.unlink(missing_ok=True)
        log("kia's MCP server is off: its token is gone, so no assistant can use it.")
        return 0
    value = mcp.token(cfg.state_dir)
    if args.reset or not value:
        value = mcp.make_token(cfg.state_dir)
        log("Made a new token" + (": the old one no longer works." if args.reset else "."))
    # The name the secure Dashboard's certificate covers first, as secure_setup uses: another
    # name (or a bare address) would fail even with the certificate trusted.
    host = tls.names(cfg.public_url, cfg.tls_names)[0]
    address = f"https://{tls.in_url(host)}:{cfg.https_port}/api/mcp"
    print(f"kia's MCP server is on, at {address}")
    if host in ("localhost", "127.0.0.1"):
        print("That address works only on this computer. For others, set KIA_PUBLIC_URL (or KIA_TLS_NAMES) in .env, restart kia, and run this again.")
    # Where it is in the kia folder: inside Docker, the full path would be the container's (/data/...).
    try:
        where = f"{path.resolve().relative_to(Path(args.config).resolve().parent)} in the kia folder"
    except ValueError:  # a state folder outside the kia folder
        where = str(path)
    print(f"Its token (kept in {where}): {value}")
    print()
    print(mcp.setup_for(args.for_, address, value, kia_name.current(cfg.state_dir)[0]), end="")
    print()
    print("Other computers need a copy of kia's certificate, tls/fullchain.pem, saved as ~/kia-cert.pem:")
    print(f"see docs/mcp.md. For another assistant: kia mcp-token --for {{{','.join(mcp.ASSISTANTS)}}}")
    return 0


# A device's key from scripts/gateway.sh add: what its new_key makes (43 characters), or close.
_DEVICE_KEY = re.compile(r"[A-Za-z0-9_-]{32,100}")


def _gateway_setup(cfg: Config, assistant: str) -> int:
    """The setup for one device through the gateway part: its address and the device's key.
    scripts/gateway.sh add passes the key on stdin, so it never shows in a list of processes;
    without one, the setup says where it goes. kia's own token isn't made or shown."""
    from kia import mcp

    given = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.readline(200).strip()
    key = given if _DEVICE_KEY.fullmatch(given) else "YOUR-DEVICE-KEY"
    port = os.environ.get("KIA_GATEWAY_PORT", "").strip() or "8745"
    if not port.isdigit() or not 0 < int(port) < 65536:
        log(f"KIA_GATEWAY_PORT={port[:20]!r}: expected a port number, such as 8745")
        return 2
    host = tls.names(cfg.public_url, cfg.tls_names)[0]
    address = f"https://{tls.in_url(host)}:{port}/mcp"
    print(f"The gateway is at {address}")
    if host in ("localhost", "127.0.0.1"):
        print("That address works only on this computer. For others, set KIA_PUBLIC_URL (or KIA_TLS_NAMES) in .env, restart kia, and run this again.")
    print()
    print(mcp.setup_for(assistant, address, key, kia_name.current(cfg.state_dir)[0]), end="")
    print()
    print("The device needs a copy of kia's certificate, tls/fullchain.pem, saved as ~/kia-cert.pem: see docs/mcp.md.")
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
        shown = "is in " + str(a.shown) if a.shown.exists() else "was changed on the Dashboard"
        log(f"The password {shown}. To replace it: kia password --reset")
        return 0
    a.reset(log)
    update_status(cfg)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    from kia import api as api_module

    # Dashboard changes and a refresh in another process (kia run from cron) take turns.
    api_module.LOCK.configure(cfg.state_dir)
    if args.https_port:
        serve_secure(cfg, args.bind, args.https_port)  # sets up the password and certificate first
    else:
        secure_setup(cfg)
    server = serve.make_server(cfg.output_dir, args.bind, args.port, api_config(cfg), relay=pluto_relay(cfg))
    log(f"Serving {cfg.output_dir} at http://{args.bind}:{args.port}/ (Ctrl-C to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Run the pipeline daily at --at (local time; set TZ), forever."""
    next_run(datetime.now(), args.at)  # validate --at before sleeping
    cfg = load_config(args)
    from kia import api

    api.LOCK.configure(cfg.state_dir)  # the Dashboard's saves and the refreshes take turns
    cut_short = interrupted_run(cfg)  # before anything updates the run state
    secure_setup(cfg)
    write_settings(cfg, args.at, args.guide_every)
    update_status(cfg)
    relay = pluto_relay(cfg) if args.serve or args.api_port else None
    players = metrics.Players(cfg.state_dir)  # one pseudonym list for Plex's and Jellyfin's players
    watch = None
    if (running := stack.profiles()) is not None:  # kia in Docker: the Dashboard's Stack tiles
        def part_alert(title: str, message: str) -> None:
            alerts.notify("part_down", title, message, cfg.state_dir, cfg.alerts, log)

        watch = stack.Watch(cfg.output_dir, running, alert=part_alert)
        watch.start()
    numbers = partial(metrics.render, cfg.output_dir, relay, plex_sessions(args.config, players),
                      jellyfin_sessions(args.config, players), watch, cfg.state_dir)  # /metrics, for Prometheus
    if args.serve:
        serve_secure(cfg, args.bind, args.https_port)
        serve.serve_in_background(cfg.output_dir, args.bind, args.port, api_config(cfg), relay=relay, metrics=numbers)
        log(f"Serving {cfg.output_dir} at http://{args.bind}:{args.port}/")
    elif args.api_port:
        # With Docker, nginx serves the files (HTTP and HTTPS) and forwards /api/, /pluto/,
        # and /metrics here; this port isn't published, and nginx says which came over HTTPS.
        serve.serve_in_background(cfg.output_dir, "0.0.0.0", args.api_port, api_config(cfg), trust_proxy=True,  # noqa: S104  # nosec B104
                                  relay=relay, metrics=numbers)  # fmt: skip
        log(f"Profile editor API and Pluto relay on port {args.api_port}")
    run_now = (
        args.run_now
        or env_bool("KIA_RUN_ON_START", False)
        or not (cfg.output_dir / "guide.xml.gz").exists()
        or missed_run(cfg, args.at)
        or cut_short
    )
    if cut_short:
        log("The last refresh didn't finish (kia was stopped part way), so it runs again now.")
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
                except (Exception, SystemExit) as exc:  # keep the daemon alive
                    log(f"Guide refresh failed: {type(exc).__name__}: {exc}")
                touch_guide(cfg)  # a failed refresh waits a full interval, too, instead of looping
                keep_plex_in_step(args.config)
            wait_until(nxt)
        run_now = False
        try:
            secure_setup(cfg)  # renews kia's own certificate before it expires
            write_settings(load_config(args), args.at, args.guide_every)  # kia.toml may have changed
            with locks.refresh(cfg.state_dir, "the nightly refresh"):
                run_and_report(args.config, update_epg=True)
            keep_plex_in_step(args.config)
        except locks.Busy as busy:  # one started by hand is running; it does the same job
            log(f"Skipped this refresh: another one is running ({busy}).")
        except (Exception, SystemExit) as exc:  # e.g. the config became invalid; keep the daemon alive
            log(f"Run could not start: {type(exc).__name__}: {exc}")
            try:
                update_status(cfg, result="failed", finished=now_iso(), exit_code=1)
                alerts.notify("run_failed", "kia: nightly run couldn't start", f"{type(exc).__name__}: {exc}",
                              cfg.state_dir, cfg.alerts, log)  # fmt: skip
            except Exception as exc2:  # reporting must never stop the daemon
                log(f"Couldn't report it: {exc2}")


def plex_sessions(config: str, players=None):
    """Plex's sessions for /metrics, with KIA_PLEX_URL set (and a token, or Plex in the stack)."""
    from kia import plex

    url = os.environ.get("KIA_PLEX_URL", "")
    if not url:
        return None
    base = Path(config).resolve().parent
    own = not os.environ.get("KIA_PLEX_TOKEN") and plex.in_stack(url)
    token = os.environ.get("KIA_PLEX_TOKEN", "") or (plex.own_token(base) if own else None) or ""
    server = plex.Plex(url, token, timeout=5, startup_wait=0)

    def renew() -> None:  # Plex in the stack: its token appears once it's claimed
        if fresh := plex.own_token(base):
            server.headers["X-Plex-Token"] = fresh

    return metrics.MediaSessions(server, state_dir=load(Path(config)).state_dir, players=players, renew=renew if own else None)


def jellyfin_sessions(config: str, players=None):
    """Jellyfin's sessions for /metrics, with KIA_JELLYFIN_URL and KIA_JELLYFIN_TOKEN set."""
    from kia import jellyfin

    url, token = os.environ.get("KIA_JELLYFIN_URL", ""), os.environ.get("KIA_JELLYFIN_TOKEN", "")
    if not url or not token:
        return None
    return metrics.MediaSessions(jellyfin.Jellyfin(url, token), state_dir=load(Path(config)).state_dir, players=players)


def keep_plex_in_step(config: str) -> None:
    """With KIA_PLEX_URL set, bring Plex's Threadfin DVR up to date after a refresh, so
    channels the playlists gained reach Plex (kia plex-tuners). Never stops the daemon."""
    if not os.environ.get("KIA_PLEX_URL"):
        return
    try:
        main(["plex-tuners", "-c", config, "--after-refresh"])
    except (Exception, SystemExit) as exc:
        log(f"Couldn't update Plex: {type(exc).__name__}: {exc}")


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


def cmd_threadfin_setup(args: argparse.Namespace) -> int:
    """Write each Threadfin's first settings (kia.threadfin); never changes existing ones."""
    from kia import threadfin

    cfg = load_config(args)
    threadfin.set_up(Path(args.config).resolve().parent, cfg.output_dir, log)
    return 0


def cmd_check_dashboards(args: argparse.Namespace) -> int:
    """Which Grafana dashboard panels have data here (kia.dashcheck); scripts/stack.sh status."""
    from kia import dashcheck

    base = Path(args.config).resolve().parent
    try:
        panels = dashcheck.check(args.prometheus, base / dashcheck.DASHBOARDS)
    except OSError as exc:
        log(f"Prometheus didn't answer at {args.prometheus} ({exc}). Is the monitoring profile on?")
        return 1
    if not panels:
        log(f"No dashboards in {base / dashcheck.DASHBOARDS}.")
        return 1
    for line in dashcheck.report(panels):
        print(line)
    return 0


# What the last `kia plex-tuners` found, for kia doctor's Plex answers (kia.doctor), which work
# offline: this is the one step that talks to Plex, after every refresh.
PLEX_TUNERS_STATE = "plex-tuners.json"


def cmd_plex_tuners(args: argparse.Namespace) -> int:
    """Add kia's Threadfins to Plex as one DVR of their own (kia.plex), and save what it found
    in state/plex-tuners.json."""
    from kia import plex

    cfg = load_config(args)
    said: list[str] = []
    record: dict = {"when": now_iso(), "ok": False, "removing": bool(args.remove)}

    def note(text: str) -> None:
        log(text)
        said.append(text)

    try:
        return _plex_tuners(args, cfg, plex, note, record)
    finally:
        # The last lines it printed, as the log has them (kia's own words, and Plex's errors, which
        # doctor quotes through its safety filter).
        record["said"] = said[-20:]
        try:
            write_atomic(cfg.state_dir / PLEX_TUNERS_STATE, json.dumps(record, indent=1))
        except OSError as exc:
            log(f"Couldn't save what plex-tuners found: {exc}")


def _plex_tuners(args: argparse.Namespace, cfg: Config, plex, log, record: dict) -> int:
    url = args.plex or os.environ.get("KIA_PLEX_URL", "")
    base = Path(args.config).resolve().parent
    stack = bool(url) and plex.in_stack(url)
    record.update(plex_url_set=bool(url), plex_in_stack=stack)
    # Plex in the stack has its own token, which kia can read; another Plex needs one in .env.
    token = args.token or os.environ.get("KIA_PLEX_TOKEN", "") or (plex.own_token(base) if stack else None) or ""
    host = None if stack else args.address or (urlparse(cfg.public_url).hostname if cfg.public_url else None)
    missing = [n for n, v in (("KIA_PLEX_URL (Plex's address, e.g. http://192.168.1.20:32400)", url),
                              ("KIA_PLEX_TOKEN (see docs/plex.md)" if not stack else
                               "Plex's own token (Plex hasn't been claimed yet: see PLEX_CLAIM)", token),
                              ("KIA_PUBLIC_URL (this computer's address)", host or stack)) if not v]  # fmt: skip
    if missing:
        record["missing"] = missing
        log("Set these in .env first, then restart kia: " + "; ".join(missing))
        return 1
    if host in ("localhost", "127.0.0.1"):
        log("Note: Plex will look for Threadfin at localhost, which only works when Plex runs on this computer, "
            "outside a container. Use --address with this computer's network address otherwise.")  # fmt: skip
    try:
        server, tuners = plex.Plex(url, token), plex.find_tuners(base, cfg.output_dir, host, log)
        record["tuners"] = [{"title": t.title, "address": t.address, "channels": len(t.channels)} for t in tuners]
        if stack and not args.remove and not plex.has_codecs(base):
            # A warm-up can take 20 minutes while Plex scans its libraries. After a refresh
            # (every few hours) it's tried once a day; by hand, every time.
            tried = cfg.state_dir / "plex-warm-up-tried"
            if args.after_refresh and tried.exists() and time.time() - tried.stat().st_mtime < 86400:
                log("Plex still has no codecs. kia tries again tomorrow; playing any video in Plex fixes it sooner.")
            else:
                tried.parent.mkdir(parents=True, exist_ok=True)
                tried.touch()
                plex.warm_up(server, base, log)
        if args.remove:
            # Every Threadfin kia set up, not only those answering now: taking Live TV out is
            # often done with them stopped.
            plex.remove(server, plex.kia_addresses(base, host), log)
        else:
            dvr = plex.set_up(server, tuners, log)
            record["dvr"] = str(dvr.get("key")) if isinstance(dvr, dict) else None
    except plex.PlexError as exc:
        record["error"] = str(exc)
        log(str(exc))
        return 1
    record["ok"] = True
    return 0


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
    p.add_argument("--port", type=int, help="default: KIA_PORT or 8780")
    p.add_argument("--https-port", type=int,
                   help="the secure Dashboard's port (default: KIA_HTTPS_PORT or 8743; --https-port 0 for none)")  # fmt: skip


# Options whose default comes from .env: (option, setting, default). They're read after the
# command line, so a typo in one is reported by name, and only for the commands that use it.
# (option, .env name, default, highest): ports up to 65535, and a quick guide refresh at most weekly.
ENV_DEFAULTS = (("port", "KIA_PORT", 8780, 65535), ("https_port", "KIA_HTTPS_PORT", 8743, 65535),
                ("api_port", "KIA_API_PORT", 0, 65535), ("guide_every", "KIA_GUIDE_REFRESH_HOURS", 4.0, 168.0))  # fmt: skip


def env_default(args: argparse.Namespace) -> None:
    for option, name, fallback, highest in ENV_DEFAULTS:
        if getattr(args, option, False) is not None:
            continue  # given on the command line, or not an option of this command
        raw = os.environ.get(name, "").strip()
        try:
            value = type(fallback)(raw) if raw else fallback
        except ValueError:
            raise ValueError(f"{name}={raw!r}: expected a number") from None
        # inf and nan read as numbers too, and a huge one only fails later, with a traceback.
        if not math.isfinite(value) or not 0 <= value <= highest:
            raise ValueError(f"{name}={raw!r}: expected a number from 0 to {highest:g}")
        setattr(args, option, value)


# Commands that refresh kia's files; one runs at a time (kia.locks).
REFRESHES = {cmd_run, cmd_refresh_guide, cmd_check, cmd_map, cmd_grab, cmd_guide, cmd_sync}
EXIT_BUSY = 75  # another refresh is running (EX_TEMPFAIL)


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

    p = sub.add_parser("tls", help="show the secure Dashboard's certificate, or make a new self-signed one")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--self-signed", action="store_true", help="replace the certificate with a new self-signed one")
    p.set_defaults(func=cmd_tls)

    p = sub.add_parser("password", help="make a new password for editing on the Dashboard")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--reset", action="store_true", help="replace the password with a new one, saved in state/admin-password.txt")
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

    p = sub.add_parser("doctor", help="explain what's wrong, from what kia has written down: kia doctor, or kia doctor channel <name>")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("what", nargs="*", help="nothing for an overview, or a topic: channel <name or ID>, guide, pluto, setup, plex, jellyfin, hardware")
    p.add_argument("--json", action="store_true", help="the answer as JSON, for scripts")
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("find", help="find shows in the TV guides: kia's, and Plex's and Jellyfin's Live TV (docs/ask-kia.md)")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("words", nargs="*", help="words in the show's title or description, such as NFL")
    p.add_argument("--category", default="", help="a guide category, such as Sports")
    p.add_argument("--hours", type=float, default=24, help="how far ahead to look (0: on now; at most 168)")
    p.add_argument("--json", action="store_true", help="the answer as JSON, for scripts")
    p.set_defaults(func=cmd_find)
    p = sub.add_parser("mcp", help="kia's MCP server over standard input and output, for an AI assistant on this computer (docs/mcp.md)")
    p.add_argument("-c", "--config", default="kia.toml")
    p.set_defaults(func=cmd_mcp)
    p = sub.add_parser("mcp-token", help="turn on kia's MCP server for AI assistants, and show its token (docs/ask-kia.md)")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--reset", action="store_true", help="make a new token; the old one stops working")
    p.add_argument("--off", action="store_true", help="turn the MCP server off (deletes the token)")
    p.add_argument("--for", dest="for_", default="claude-code", choices=("claude-code", "claude-desktop", "gemini", "codex", "vscode", "cursor", "curl"),
                   help="which assistant to show the setup for (default: claude-code)")
    p.add_argument("--gateway", action="store_true",
                   help="the setup through the gateway part instead, with a device's key from scripts/gateway.sh add (on stdin)")
    p.set_defaults(func=cmd_mcp_token)
    p = sub.add_parser("settings", help="show the settings kia is running with, and where each is changed")
    p.add_argument("-c", "--config", default="kia.toml")
    p.set_defaults(func=cmd_settings)

    p = sub.add_parser("test-alert", help="send a test notification (ntfy and/or your alert command)")
    p.add_argument("-c", "--config", default="kia.toml")
    p.set_defaults(func=cmd_test_alert)

    p = sub.add_parser("threadfin-setup", help="give each Threadfin its settings before it first starts (compose runs this)")
    p.add_argument("-c", "--config", default="kia.toml")
    p.set_defaults(func=cmd_threadfin_setup)

    p = sub.add_parser("plex-tuners", help="add kia's Threadfins to Plex as a DVR of their own (docs/plex.md)")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--plex", help="Plex's address, e.g. http://192.168.1.20:32400 (default: KIA_PLEX_URL)")
    p.add_argument("--token", help="a Plex token (default: KIA_PLEX_TOKEN; .env keeps it out of your shell history)")
    p.add_argument("--address", help="this computer's address as Plex reaches it (default: from KIA_PUBLIC_URL)")
    p.add_argument("--remove", action="store_true", help="delete the DVR with kia's Threadfins (only if it has nothing else)")
    p.add_argument("--after-refresh", action="store_true", help=argparse.SUPPRESS)  # the daemon's own run
    p.set_defaults(func=cmd_plex_tuners)

    p = sub.add_parser("check-dashboards", help="show which Grafana dashboard panels have data on this computer")
    p.add_argument("-c", "--config", default="kia.toml")
    p.add_argument("--prometheus", default=os.environ.get("KIA_PROMETHEUS_URL") or "http://prometheus:9090",
                   help="Prometheus's address (default: http://prometheus:9090, as kia's container reaches it)")  # fmt: skip
    p.set_defaults(func=cmd_check_dashboards)

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
        help="hours between quick guide refreshes, 0 to turn off (default: KIA_GUIDE_REFRESH_HOURS or 4)",
    )
    p.add_argument("--run-now", action="store_true", help="also run once at startup (or set KIA_RUN_ON_START)")
    p.add_argument("--api-port", type=int,
                   help="serve the Dashboard's API on this port (Docker sets 8781)")  # fmt: skip
    p.add_argument("--serve", action="store_true", help="also serve the output folder (kia serve)")
    add_serve_args(p)
    p.set_defaults(func=cmd_daemon)

    args = parser.parse_args(argv)
    # A setting that can't be read (in .env or kia.toml): say which, without a traceback
    # (in a container that restarts over and over, the last line is what's seen).
    try:
        env_default(args)
        # backup and restore work without a readable kia.toml: they're how you get one back. And
        # kia mcp answers an assistant anyway: its tools say what's wrong when they're used.
        if hasattr(args, "config") and args.func not in (cmd_backup, cmd_restore, cmd_mcp):
            load_config(args)
        if getattr(args, "at", None):
            next_run(datetime.now(), args.at)
        if args.func is cmd_daemon:
            env_bool("KIA_RUN_ON_START", False)
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"kia: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"kia: {exc.filename} not found", file=sys.stderr)
        return 2
    # kia check --url (the Dashboard's "check now") writes its results to work/check-test,
    # away from the refresh's files, so it can run while a refresh does.
    if args.func in REFRESHES and not getattr(args, "url", None):
        try:
            with locks.refresh(load_config(args).state_dir, f"kia {args.command}"):
                return args.func(args)
        except locks.Busy as busy:
            log(f"A refresh is already running ({busy}). Try again when it's done; the Dashboard shows when that is.")
            return EXIT_BUSY
    return args.func(args)
