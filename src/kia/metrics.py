"""/metrics: kia's numbers for Prometheus (docs/metrics.md).

Read from status.json (the refresh, channels, guide, sources, playlists) and, live, from
the Pluto relay, Plex, and this process. Nothing here is secret: the same numbers are on
the Dashboard. Plex players appear by a pseudonym, such as player-3fa2c1, not by their
names (often a room or a person's); the secure System page shows which is which.
Prometheus's text format, version 0.0.4.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import resource
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from kia import __version__

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
STARTED = time.time()


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class _Out:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def metric(self, name: str, kind: str, help_text: str, samples: list[tuple[dict, float | int | None]]) -> None:
        samples = [(labels, v) for labels, v in samples if v is not None]
        if not samples:
            return
        self.lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
        for labels, value in samples:
            tags = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items())
            self.lines.append(f"{name}{{{tags}}} {value}" if tags else f"{name} {value}")

    def one(self, name: str, kind: str, help_text: str, value: float | int | None) -> None:
        self.metric(name, kind, help_text, [({}, value)])


def _ts(iso: object) -> float | None:
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except ValueError:
        return None


def _rss_bytes() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # the peak, where /proc isn't there
    return peak if sys.platform == "darwin" else peak * 1024


PLAYERS_FILE = "plex-players.json"
KEEP_PLAYERS_DAYS = 90


class Players:
    """Pseudonyms for Plex players: an HMAC of Plex's id for the device with a secret kept
    in state/ (metrics-key), so the same device always gets the same one and nobody can
    work back from it. Which pseudonym is which device is kept in state/plex-players.json,
    for the secure System page."""

    def __init__(self, state_dir: Path | None) -> None:
        self.state_dir = state_dir
        self.key: bytes | None = None
        self.known: dict[str, dict] | None = None

    def _key(self) -> bytes:
        if self.key is None:
            path = self.state_dir / "metrics-key" if self.state_dir else None
            try:
                self.key = bytes.fromhex(path.read_text().strip()) if path else b""
            except (OSError, ValueError):
                self.key = b""
            if not self.key:
                self.key = secrets.token_bytes(32)
                if path:
                    _write_private(path, self.key.hex() + "\n")
        return self.key

    def name(self, player_id: str) -> str:
        return "player-" + hmac.new(self._key(), player_id.encode(), hashlib.sha256).hexdigest()[:6]

    def note(self, sessions: list[dict]) -> None:
        """Remember who each pseudonym is, writing only when something new is seen today."""
        if not self.state_dir:
            return
        path = self.state_dir / PLAYERS_FILE
        if self.known is None:
            try:
                self.known = json.loads(path.read_text())
            except (OSError, ValueError):
                self.known = {}
        today, changed = datetime.now().astimezone().date().isoformat(), False
        for x in sessions:
            seen = {"name": x["player"], "product": x["product"], "seen": today}
            if self.known.get(x["pseudonym"]) != seen:
                self.known[x["pseudonym"]], changed = seen, True
        if changed:
            cutoff = (datetime.now().astimezone() - timedelta(days=KEEP_PLAYERS_DAYS)).date().isoformat()
            self.known = {k: v for k, v in self.known.items() if v.get("seen", "") >= cutoff}
            _write_private(path, json.dumps(self.known, indent=1, sort_keys=True) + "\n")


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    tmp.replace(path)


def known_players(state_dir: Path) -> list[dict]:
    """Which pseudonym is which Plex player, newest first (for the secure System page)."""
    try:
        known = json.loads((state_dir / PLAYERS_FILE).read_text())
    except (OSError, ValueError):
        return []
    return sorted(({"pseudonym": k, **v} for k, v in known.items()), key=lambda p: (p.get("seen", ""), p["pseudonym"]), reverse=True)


class PlexSessions:
    """A media server's sessions for /metrics (Plex's, or Jellyfin's), asked at most every 15
    seconds, with each player's pseudonym in place of its name. Plex and Jellyfin share one
    Players, so their pseudonyms go in one list."""

    def __init__(self, server, every: float = 15, state_dir: Path | None = None, players: Players | None = None) -> None:
        self.server, self.every = server, every
        self.at, self.value = -1e9, None
        self.lock = threading.Lock()
        self.players = players or Players(state_dir)

    def get(self) -> list[dict] | None:
        """The sessions, or None when Plex didn't answer."""
        with self.lock:
            if time.monotonic() - self.at >= self.every:
                try:
                    sessions = self.server.sessions()
                except Exception:  # the server is down, or a bad token: kia_plex_up/kia_jellyfin_up says so
                    sessions = None
                if sessions is not None:
                    try:
                        for x in sessions:
                            x["pseudonym"] = self.players.name(x.get("player_id") or x["player"])
                        self.players.note(sessions)
                    except (OSError, ValueError) as exc:
                        # kia's own files in state/, not the server: the sessions still count.
                        print(f"metrics: couldn't keep the players' pseudonyms: {exc}", file=sys.stderr)
                        for x in sessions:
                            x.setdefault("pseudonym", "player-unknown")
                self.value = sessions
                self.at = time.monotonic()
            return self.value


JellyfinSessions = PlexSessions  # the same, for a Jellyfin


def _plex(out: _Out, plex: PlexSessions, name: str = "plex", title: str = "Plex") -> None:
    sessions = plex.get()
    out.one(f"kia_{name}_up", "gauge", f"1 if {title} answered kia's last question.", int(sessions is not None))
    if sessions is None:
        return
    groups: dict[tuple, int] = {}
    for x in sessions:
        key = (x["kind"], x["video"], x.get("hw", "none"), x["location"])
        groups[key] = groups.get(key, 0) + 1
    out.metric(f"kia_{name}_sessions", "gauge", f"{title} sessions now, by kind (live is live TV), video (copy, transcode, "
               "directplay), hw (gpu or cpu when converting), and location.",
               [({"kind": k, "video": v, "hw": hw, "location": loc}, n) for (k, v, hw, loc), n in sorted(groups.items())]
               or [({"kind": "none", "video": "none", "hw": "none", "location": "none"}, 0)])  # fmt: skip
    out.metric(f"kia_{name}_session_info", "gauge", f"One line per {title} player now playing (player is a pseudonym).",
               [({"player": x["pseudonym"], **{key: x[key] for key in ("product", "kind", "video", "location", "state")}}, 1)
                for x in sessions])  # fmt: skip


def render(output_dir: Path, relay=None, plex: PlexSessions | None = None, jellyfin: PlexSessions | None = None) -> str:
    out = _Out()
    try:
        s = json.loads((output_dir / "status.json").read_text())
    except (OSError, ValueError):
        s = {}
    out.metric("kia_info", "gauge", "kia's version.", [({"version": __version__}, 1)])
    out.one("kia_start_time_seconds", "gauge", "When this kia started (Unix time).", round(STARTED))

    run = s.get("last_run") or {}
    result = s.get("result")
    if result:
        out.one("kia_refresh_running", "gauge", "1 while the nightly refresh runs.", int(result == "running"))
        if result != "running" and run.get("finished"):
            out.one("kia_last_refresh_success", "gauge", "1 if the last refresh finished (with or without alerts).",
                    int(result in ("ok", "alerts")))  # fmt: skip
            out.one("kia_last_refresh_alerts", "gauge", "1 if the last refresh raised alerts.", int(result == "alerts"))
    out.one("kia_last_refresh_start_time_seconds", "gauge", "When the last refresh started (Unix time).", _ts(run.get("started")))
    out.one("kia_last_refresh_end_time_seconds", "gauge", "When the last refresh finished (Unix time).", _ts(run.get("finished")))
    if run.get("minutes") is not None:
        out.one("kia_last_refresh_duration_seconds", "gauge", "How long the last refresh took.", run["minutes"] * 60)
    out.one("kia_next_refresh_time_seconds", "gauge", "When the next nightly refresh starts (Unix time).", _ts(s.get("next_run")))
    out.one("kia_guide_updated_time_seconds", "gauge", "When the guide was last rebuilt (Unix time).", _ts(s.get("guide_updated")))

    ch = s.get("channels") or {}
    out.metric("kia_channels", "gauge", "Channels in the full playlist.",
               [({"kind": "tv"}, ch.get("tv")), ({"kind": "radio"}, ch.get("radio"))])  # fmt: skip
    out.one("kia_channels_with_guide", "gauge", "Channels with guide data.", ch.get("with_guide"))
    cov = s.get("coverage") or {}
    out.metric("kia_guide_coverage_channels", "gauge", "TV channels by how much of the guide they have.",
               [({"level": k}, cov.get(k)) for k in ("full", "partial", "none")])  # fmt: skip
    out.metric("kia_source_streams", "gauge", "Streams in each channel source at the last check, by result.",
               [({"source": src.get("name"), "status": st}, src.get(st))
                for src in s.get("sources") or [] for st in ("ok", "flaky", "dead")])  # fmt: skip
    out.metric("kia_playlist_channels", "gauge", "Channels in each playlist.",
               [({"playlist": p.get("name"), "kind": kind}, p.get(kind))
                for p in s.get("profiles") or [] for kind in ("tv", "radio")])  # fmt: skip
    out.one("kia_alerts", "gauge", "Alerts from the last refresh.", len(s.get("alerts") or []))

    if relay is not None:
        r = relay.counters()
        names = s.get("pluto_channels") or {}
        out.one("kia_pluto_channels_playing", "gauge", "Pluto TV channels kia is playing now.", r["playing"])
        out.metric("kia_pluto_viewers", "gauge", "Players watching each Pluto TV channel now.",
                   [({"channel": cid, "name": names.get(cid, "")}, n) for cid, n in r["viewers"].items()])  # fmt: skip
        for key, help_text in (("sessions", "Pluto sessions started."), ("rewinds", "Times Pluto went back in a stream."),
                               ("errors", "Pieces or playlists kia couldn't pass on."),
                               ("fallbacks", "Times kia fell back to the shared Pluto stream."),
                               ("pieces", "Video pieces passed on to players."), ("bytes", "Video bytes passed on to players.")):  # fmt: skip
            out.one(f"kia_pluto_{key}_total", "counter", help_text, r[key])

    if plex is not None:
        _plex(out, plex)
    if jellyfin is not None:
        _plex(out, jellyfin, "jellyfin", "Jellyfin")

    use = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    out.one("kia_process_cpu_seconds_total", "counter", "Processor time used by kia itself.",
            round(use.ru_utime + use.ru_stime, 2))  # fmt: skip
    out.one("kia_children_cpu_seconds_total", "counter", "Processor time used by the programs kia ran (ffmpeg, ffprobe, the guide grabber).",
            round(kids.ru_utime + kids.ru_stime, 2))  # fmt: skip
    out.one("kia_process_resident_memory_bytes", "gauge", "Memory kia uses.", _rss_bytes())
    return "\n".join(out.lines) + "\n"
