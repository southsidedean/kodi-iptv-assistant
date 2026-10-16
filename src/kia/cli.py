"""Command-line entry point: `kia check` and `kia probe`."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import urllib.request
from pathlib import Path

from kia import __version__, m3u
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


def cmd_check(args: argparse.Namespace) -> int:
    cfg = load(Path(args.config))
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
    (out / "curated.m3u").write_text(m3u.write([c.entry for c in chosen], cfg.epg_url))
    (out / "report.json").write_text(
        json.dumps(
            {"generated": generated, "streams": [c.to_dict() for c in checked]},
            indent=1,
        )
    )
    (out / "report.md").write_text(render_markdown(checked, chosen, generated))

    if not args.limit:
        history.prune()
    history.save()

    n_ok = sum(c.status == "ok" for c in checked)
    n_flaky = sum(c.status == "flaky" for c in checked)
    log(
        f"\n{len(checked)} entries: {n_ok} ok, {n_flaky} flaky, {len(checked) - n_ok - n_flaky} dead "
        f"-> {len(chosen)} channels in {out / 'curated.m3u'}"
    )
    return 0


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
    p.add_argument("-s", "--source", action="append", help="only check this source (repeatable)")
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

    args = parser.parse_args(argv)
    return args.func(args)
