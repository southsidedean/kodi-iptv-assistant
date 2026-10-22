"""/metrics: kia's numbers for Prometheus (docs/metrics.md).

Read from status.json (the refresh, channels, guide, sources, playlists) and, live, from
the Pluto relay and this process. Nothing here is secret: the same numbers are on the
Dashboard. Prometheus's text format, version 0.0.4.
"""

from __future__ import annotations

import json
import resource
import sys
import time
from datetime import datetime
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


def render(output_dir: Path, relay=None) -> str:
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

    use = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    out.one("kia_process_cpu_seconds_total", "counter", "Processor time used by kia itself.",
            round(use.ru_utime + use.ru_stime, 2))  # fmt: skip
    out.one("kia_children_cpu_seconds_total", "counter", "Processor time used by the programs kia ran (ffmpeg, ffprobe, the guide grabber).",
            round(kids.ru_utime + kids.ru_stime, 2))  # fmt: skip
    out.one("kia_process_resident_memory_bytes", "gauge", "Memory kia uses.", _rss_bytes())
    return "\n".join(out.lines) + "\n"
