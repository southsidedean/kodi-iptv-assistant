#!/usr/bin/env python3
"""Record what happens while you watch Live TV through Plex, so a hiccup can be traced to the
hop that fell behind: the channel's source, Plex, or the player.

Every few seconds it writes down, side by side:

- Plex: each Live TV session's player, whether Plex converts the video, how fast its
  converter runs (1.0 is real time), and how many seconds it has ready ahead of the player.
- Rokus (optional): whether each one is playing or buffering, and how full its buffer is.
  This needs the Roku's "Control by mobile apps" set to Permissive; in Limited mode a Roku
  says only which app is open. Nothing is ever sent to a Roku but questions.
- A channel's source (optional): how often new pieces of the stream arrive, and whether they
  add up to real time.

    python3 tools/playback/record.py --env ~/kodi-iptv-assistant/.env --find-rokus
    python3 tools/playback/record.py --env .env --roku http://192.0.2.30:8060 \\
        --watch "Battlestar Galactica" --kia ~/kodi-iptv-assistant --minutes 20

It prints one line every few seconds and a summary at the end (or on Ctrl-C), and keeps every
reading in a JSON Lines file (playback-<date>.jsonl unless --out names one). The file names your
devices, so keep it to yourself; git ignores playback-*.jsonl. The Plex token is read from .env
(KIA_PLEX_TOKEN) or PLEX_TOKEN and is never printed or saved, and nor are stream addresses.
Standard library only, like kia.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

# Seconds a Plex session has ready ahead of the player, below which a stall is near.
LOW_AHEAD = 10
# A source counts as keeping up when, over the last minute, new video arrives at least this
# many seconds per second. A live channel can only come as fast as it's made, so a healthy one
# reads about 1.0, a little either side; well under it, the player is bound to run dry.
KEEPING_UP = 0.95
SOURCE_WINDOW = 60


def read_env(path: Path) -> dict[str, str]:
    """KEY=value lines of a .env file, one pair of quotes taken off a value (as Compose does)."""
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = re.sub(r"""^(["'])(.*)\1$""", r"\2", value.strip())
    return out


def fetch(url: str, headers: dict[str, str] | None = None, timeout: float = 5) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as r:
        return r.read()


# Plex


def plex_sessions(container: dict) -> list[dict]:
    """The Live TV sessions in Plex's /status/sessions answer, as the few numbers that matter.
    Other sessions (movies, shows) are left out: they don't come through kia."""
    out = []
    for s in container.get("Metadata") or []:
        if not isinstance(s, dict) or not s.get("live"):
            continue
        player = s.get("Player") if isinstance(s.get("Player"), dict) else {}
        tc = s.get("TranscodeSession") if isinstance(s.get("TranscodeSession"), dict) else {}
        media = s["Media"][0] if isinstance(s.get("Media"), list) and s["Media"] and isinstance(s["Media"][0], dict) else {}
        played = _number(s.get("viewOffset"))  # milliseconds into the session
        ready = _number(tc.get("maxOffsetAvailable"))  # seconds the converter has made
        out.append({
            "title": s.get("title"),
            "player": player.get("title") or player.get("product"),
            "product": player.get("product"), "platform": player.get("platform"), "state": player.get("state"),
            # No TranscodeSession: Plex sends the stream as it is (direct play).
            "video": tc.get("videoDecision", "direct play" if not tc else None), "audio": tc.get("audioDecision"),
            "from": tc.get("sourceVideoCodec") or media.get("videoCodec"), "to": tc.get("videoCodec"),
            "resolution": media.get("videoResolution"), "hw": tc.get("transcodeHwEncoding"),
            "speed": _number(tc.get("speed")), "throttled": tc.get("throttled"),
            "ahead": round(ready - played / 1000, 1) if ready is not None and played is not None else None,
        })  # fmt: skip
    return out


def _number(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# Rokus (External Control Protocol, port 8060)


def find_rokus(wait: float = 4) -> list[str]:
    """The Rokus that answer the discovery message phone remote apps send (SSDP, roku:ecp)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.5)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    ask = b'M-SEARCH * HTTP/1.1\r\nHost: 239.255.255.250:1900\r\nMan: "ssdp:discover"\r\nST: roku:ecp\r\nMX: 2\r\n\r\n'
    for _ in range(2):
        s.sendto(ask, ("239.255.255.250", 1900))
    found, end = set(), time.time() + wait
    while time.time() < end:
        try:
            data, _ = s.recvfrom(2048)
        except TimeoutError:
            continue
        m = re.search(rb"(?im)^location:\s*(http://[\d.]+:\d+)/?\s*$", data)
        if m:
            found.add(m.group(1).decode())
    return sorted(found)


def _tag(name: str, xml: str) -> str | None:
    m = re.search(rf"<{name}>([^<]*)</{name}>", xml)
    return m.group(1).strip() if m else None


def _attrs(name: str, xml: str) -> dict[str, str]:
    m = re.search(rf"<{name}\b([^>]*)>", xml)
    return dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(1))) if m else {}


def roku_player(xml: str) -> dict:
    """A Roku's /query/media-player answer: playing or buffering, and its buffer."""
    player, buffering, plugin = _attrs("player", xml), _attrs("buffering", xml), _attrs("plugin", xml)
    position = _number((_tag("position", xml) or "").removesuffix("ms").strip())  # "12345 ms"
    return {"state": player.get("state"), "app": plugin.get("name"),
            "buffer": _number(buffering.get("current")), "buffer_max": _number(buffering.get("max")),
            "position_s": position / 1000 if position is not None else None,
            "live": _tag("is_live", xml) == "true", "format": _attrs("format", xml).get("video")}  # fmt: skip


def roku_reading(base: str) -> dict:
    """One Roku's state now. In Limited mode the player answer is refused (403): then only the
    app that's open."""
    out: dict = {}
    try:
        app = fetch(base + "/query/active-app").decode("utf-8", "replace")
        out["app"] = (re.search(r"<app[^>]*>([^<]*)</app>", app) or [None, None])[1]
        out.update({k: v for k, v in roku_player(fetch(base + "/query/media-player").decode("utf-8", "replace")).items() if v is not None})
    except urllib.error.HTTPError as exc:
        out["limited"] = exc.code == 403
    except OSError as exc:
        out["error"] = type(exc).__name__
    return out


def roku_name(base: str) -> str:
    try:
        return _tag("user-device-name", fetch(base + "/query/device-info").decode("utf-8", "replace")) or base
    except OSError:
        return base


# A channel's source


class Source:
    """Watches one HLS playlist in a thread: each new piece's length and when it showed up."""

    def __init__(self, url: str, every: float = 2):
        self.url, self.every = url, every
        self.arrivals: list[tuple[float, float]] = []  # (time it was first listed, seconds long)
        self.errors = 0
        self.lock = threading.Lock()
        self.began = time.time()
        self.seen: set[str] = set()
        self.first = True

    def run(self, stop: threading.Event) -> None:
        while not stop.wait(0 if self.first else self.every):
            try:
                self.poll(fetch(self.url, timeout=10).decode("utf-8", "replace"))
            except (OSError, ValueError):
                with self.lock:
                    self.errors += 1

    def poll(self, text: str) -> None:
        if "#EXT-X-STREAM-INF" in text:  # a list of qualities: follow the first one
            self.url = urljoin(self.url, next(line for line in text.splitlines() if line and not line.startswith("#")))
            return
        now, length = time.time(), None
        for line in text.splitlines():
            if line.startswith("#EXTINF:"):
                length = _number(line[8:].split(",")[0])
            elif line and not line.startswith("#"):
                if line not in self.seen:
                    self.seen.add(line)
                    # The pieces already listed the first time were made before watching began:
                    # they say nothing about how fast new ones come.
                    if not self.first and length:
                        with self.lock:
                            self.arrivals.append((now, length))
                length = None
        self.first = False

    def reading(self, now: float | None = None) -> dict:
        """How fast new video arrives: seconds of it per second, over the pieces that showed up
        in the last minute. Measured from the first of them to the last, so the wait before the
        first one (which only says when watching began) doesn't count. It needs three pieces."""
        now = now or time.time()
        with self.lock:
            recent = [(at, length) for at, length in self.arrivals if at > now - SOURCE_WINDOW]
            errors = self.errors
        ratio = None
        if len(recent) >= 3 and recent[-1][0] > recent[0][0]:
            # The first piece's video arrived before the span began; each later one within it.
            ratio = round(sum(length for _, length in recent[1:]) / (recent[-1][0] - recent[0][0]), 2)
        # A whole minute with nothing new: the source has stopped, which no ratio can show.
        silent = not recent and now - self.began >= SOURCE_WINDOW
        return {"pieces": len(recent), "ratio": ratio, "errors": errors, "silent": silent}


def channel_url(kia: Path, name: str) -> str | None:
    """A channel's stream address from kia's playlist (output/curated.m3u), by its name."""
    lines = (kia / "output" / "curated.m3u").read_text().splitlines()
    for i, line in enumerate(lines[:-1]):
        if line.startswith("#EXTINF") and line.rsplit(",", 1)[-1].strip().lower() == name.lower():
            return lines[i + 1].strip()
    return None


# Recording

# The Roku's own words for what its player is doing, in plain ones.
ROKU_STATES = {"play": "playing", "buffer": "buffering", "pause": "paused", "startup": "starting",
               "stop": "stopped", "close": "closed", "none": "idle"}  # fmt: skip


def line_for(reading: dict) -> str:
    parts = [reading["t"][11:19]]
    for s in reading["plex"]:
        speed = f"{s['speed']:.1f}x" if s["speed"] is not None else "-"
        ahead = f"{s['ahead']:.0f}s ahead" if s["ahead"] is not None else "-"
        parts.append(f"Plex {s['player']}: {s['title']} ({s['video']}, {speed}, {ahead})")
    if not reading["plex"]:
        parts.append("Plex: no Live TV")
    for name, r in reading["roku"].items():
        if "state" in r:
            full = f" ({r['buffer'] / r['buffer_max']:.0%} of its buffer full)" if r.get("buffer") is not None and r.get("buffer_max") else ""
            parts.append(f"{name}: {ROKU_STATES.get(r['state'], r['state'])}{full}")
        else:
            parts.append(f"{name}: {r.get('app') or r.get('error') or '?'}" + (" (limited)" if r.get("limited") else ""))
    if reading.get("source"):
        src = reading["source"]
        if src.get("silent"):
            parts.append("source: nothing new for a minute")
        else:
            parts.append(f"source {src['ratio']}x" if src["ratio"] is not None else "source: measuring")
    return " | ".join(parts)


def summary(readings: list[dict]) -> list[str]:
    """What the recording shows, in a few plain lines."""
    out = [f"{len(readings)} readings, {readings[0]['t'][11:19]} to {readings[-1]['t'][11:19]}" if readings else "No readings."]
    sessions: dict[str, list[dict]] = {}
    for r in readings:
        for s in r["plex"]:
            sessions.setdefault(f"{s['player']}: {s['title']}", []).append(s)
    for name, seen in sessions.items():
        speeds = [s["speed"] for s in seen if s["speed"] is not None]
        aheads = [s["ahead"] for s in seen if s["ahead"] is not None]
        low = sum(1 for a in aheads if a < LOW_AHEAD)
        out.append(f"Plex {name}: {seen[-1]['video']}"
                   + (f", converter {sum(speeds) / len(speeds):.1f}x on average" if speeds else "")
                   + (f", {min(aheads):.0f}s ahead at the lowest ({low} readings under {LOW_AHEAD}s)" if aheads else ""))  # fmt: skip
    rokus: dict[str, list[dict]] = {}
    for r in readings:
        for name, state in r["roku"].items():
            rokus.setdefault(name, []).append(state)
    for name, seen in rokus.items():
        states = [s["state"] for s in seen if "state" in s]
        if states:
            out.append(f"{name}: buffering in {states.count('buffer')} of {len(states)} readings")
        elif any(s.get("limited") for s in seen):
            out.append(f"{name}: ECP is Limited, so only the open app was seen (set it to Permissive for the buffer)")
    ratios = [r["source"]["ratio"] for r in readings if r.get("source") and r["source"]["ratio"] is not None]
    silent = sum(1 for r in readings if r.get("source", {}).get("silent"))
    if ratios:
        slow = sum(1 for x in ratios if x < KEEPING_UP)
        out.append(f"Source: {min(ratios):.2f}x at the lowest, under real time in {slow} of {len(ratios)} readings")
    if silent:
        out.append(f"Source: nothing new for a minute or more in {silent} readings")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env", type=Path, help="kia's .env, for KIA_PLEX_URL and KIA_PLEX_TOKEN")
    ap.add_argument("--plex", help="Plex's address (default: KIA_PLEX_URL, else http://127.0.0.1:32400)")
    ap.add_argument("--roku", action="append", default=[], help="a Roku's address, e.g. http://192.0.2.30:8060 (again for more)")
    ap.add_argument("--find-rokus", action="store_true", help="find the Rokus on this network and watch them all")
    ap.add_argument("--watch", help="a channel's name in kia's playlist (with --kia), or an HLS address, to time its source")
    ap.add_argument("--kia", type=Path, help="the kia folder, to look up --watch by name")
    ap.add_argument("--every", type=float, default=5, help="seconds between readings (default 5)")
    ap.add_argument("--minutes", type=float, default=30, help="how long to record (default 30; 0 until Ctrl-C)")
    ap.add_argument("--out", type=Path, help="the JSON Lines file (default playback-<date>.jsonl)")
    args = ap.parse_args(argv)

    env = read_env(args.env) if args.env else {}
    token = os.environ.get("PLEX_TOKEN") or env.get("KIA_PLEX_TOKEN", "")
    plex = (args.plex or env.get("KIA_PLEX_URL") or "http://127.0.0.1:32400").rstrip("/")
    rokus = list(args.roku) + (find_rokus() if args.find_rokus else [])
    names = {base: roku_name(base.rstrip("/")) for base in rokus}
    source = None
    if args.watch:
        url = args.watch if re.match(r"https?://", args.watch) else (channel_url(args.kia, args.watch) if args.kia else None)
        if not url:
            print(f"{args.watch}: not in {args.kia}/output/curated.m3u (give --kia, or the stream's address)", file=sys.stderr)
            return 2
        source = Source(url)
    out = args.out or Path(f"playback-{datetime.now():%Y%m%d-%H%M}.jsonl")
    stop = threading.Event()
    if source:
        threading.Thread(target=source.run, args=(stop,), daemon=True).start()
    print(f"Recording to {out}" + (f", with {', '.join(names.values())}" if names else "") + ". Ctrl-C stops.")
    readings: list[dict] = []
    end = time.time() + args.minutes * 60 if args.minutes else None
    try:
        with out.open("a") as f:
            while end is None or time.time() < end:
                started = time.time()
                reading: dict = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plex": [], "roku": {}}
                try:
                    answer = json.loads(fetch(plex + "/status/sessions", {"Accept": "application/json", "X-Plex-Token": token}))
                    reading["plex"] = plex_sessions(answer.get("MediaContainer") or {})
                except (OSError, ValueError, AttributeError) as exc:
                    reading["plex_error"] = type(exc).__name__  # never the URL: it could carry the token
                for base, name in names.items():
                    reading["roku"][name] = roku_reading(base.rstrip("/"))
                if source:
                    reading["source"] = source.reading()
                readings.append(reading)
                f.write(json.dumps(reading) + "\n")
                f.flush()
                print(line_for(reading), flush=True)
                time.sleep(max(0.0, args.every - (time.time() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    print("\n".join(summary(readings)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
