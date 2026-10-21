"""kia threadfin-setup: give each Threadfin its settings before it first starts.

Threadfin (docs/plex.md) needs a playlist, a guide, a filter that passes every channel,
its guide source set to XEPG, a tuner count, and FFmpeg options that pass on one stream.
Set by hand, the order matters: channels Threadfin sees before it has the guide never get
matched to it. Written here before Threadfin's first start, everything is in place at
once, and Threadfin fills in the rest of its settings itself.

A Threadfin that already has settings (threadfin/conf/settings.json) is never touched:
changes made on its own page stay. compose.yaml runs this as a one-off container
(threadfin-setup) before the Threadfins start.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# The Threadfins compose.yaml can start: their folder in the kia folder, and the .env
# setting that names the kia playlist each one reads.
THREADFINS = (("threadfin", "KIA_THREADFIN_PLAYLIST"), ("threadfin-2", "KIA_THREADFIN_2_PLAYLIST"))
# Pass on the best single stream, not every quality at once (Threadfin's own options).
FFMPEG_OPTIONS = "-hide_banner -loglevel error -i [URL] -c copy -f mpegts pipe:1"
TUNERS = 4
# Threadfin re-reads the playlist and guide at these times (HHMM), so new channels reach
# Plex the same day; kia's quick guide refresh runs every few hours too.
UPDATES = ["0000", "0600", "1200", "1800"]


def playlist_for(index: int, output_dir: Path) -> str:
    """The kia playlist a Threadfin reads, when .env doesn't say: plex, or plex-1 and
    plex-2 for two tuners, whichever exists."""
    if index == 1:
        if not (output_dir / "plex.m3u").exists() and (output_dir / "plex-1.m3u").exists():
            return "plex-1"
        return "plex"
    return "plex-2"


def settings(playlist: str, base: str = "http://web") -> dict:
    """A new Threadfin's settings, reading kia's playlist `playlist` and its guide."""
    m3u, xmltv = "MKIAPLAYLIST00000001", "XKIAGUIDE00000000001"  # Threadfin's own ids for them
    blank = {"http_proxy.ip": "", "http_proxy.port": ""}
    return {
        "version": "0.5.0",  # Threadfin refuses a settings file without a version
        "files": {
            "hdhr": {},
            "m3u": {m3u: {"id.provider": m3u, "file.threadfin": f"{m3u}.m3u", "file.source": f"{base}/{playlist}.m3u",
                          "name": f"kia {playlist}", "description": "Set up by kia", "type": "m3u",
                          "buffer": "ffmpeg", "tuner": TUNERS, "http_headers.origin": "", "http_headers.referer": "",
                          **blank}},
            "xmltv": {xmltv: {"id.provider": xmltv, "file.threadfin": f"{xmltv}.xml",
                              "file.source": f"{base}/{playlist}.guide.xml.gz", "name": f"kia {playlist} guide",
                              "description": "Set up by kia", "type": "xmltv", **blank}},
        },
        # A single space matches every channel kia writes (Threadfin passes on only
        # channels that a filter picks).
        "filter": {"0": {"name": "Everything", "description": "Every channel from kia", "type": "custom-filter",
                         "filter": " ", "caseSensitive": False, "startingNumber": "1", "x-category": ""}},
        "epgSource": "XEPG",
        "tuner": TUNERS,
        "ffmpeg.options": FFMPEG_OPTIONS,
        "update": UPDATES,
    }


def set_up(base: Path, output_dir: Path, log=print) -> list[str]:
    """Write settings for each Threadfin folder that has none. Returns the folders set up."""
    done = []
    for index, (folder, setting) in enumerate(THREADFINS, 1):
        conf = base / folder / "conf"
        target = conf / "settings.json"
        if target.exists():
            log(f"{folder}: already set up; leaving it as it is.")
            continue
        playlist = os.environ.get(setting) or playlist_for(index, output_dir)
        conf.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name("settings.json.tmp")
        tmp.write_text(json.dumps(settings(playlist), indent=1) + "\n")
        tmp.replace(target)
        note = "" if (output_dir / f"{playlist}.m3u").exists() else (
            f" There's no playlist named {playlist} yet: make it on the Dashboard, then run "
            f"docker compose restart {folder}.")  # fmt: skip
        log(f"{folder}: set up to read kia's {playlist} playlist and its guide.{note}")
        done.append(folder)
    return done
