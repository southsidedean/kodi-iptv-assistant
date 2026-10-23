"""kia plex-tuners: add kia's Threadfins to Plex as one DVR, through Plex's own API.

Plex's web page sets up a tuner only when Plex has no DVR yet, and it won't add an
XMLTV guide to a DVR that uses Plex's own guide (an antenna tuner's, say). This does
what the web page does, without those limits:

- registers each Threadfin with Plex as an HDHomeRun tuner;
- puts them all in one DVR of their own, each with its own guide, and leaves any other
  DVR alone (a DVR that already has a Threadfin from this kia is reused);
- turns on every channel and matches it to the same number in Threadfin's guide, so
  running it again adds channels the playlists have gained;
- names each tuner after the kia playlist it carries.

Threadfin's settings are in the kia folder (threadfin/, threadfin-2/): they say which
of kia's playlists each one reads. Plex reaches Threadfin at this computer's address
(from KIA_PUBLIC_URL, or --address) and the port in .env.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Threadfin's folders in the kia folder, and the .env setting for each one's port.
FOLDERS = (("threadfin", "KIA_THREADFIN_PORT", 34400), ("threadfin-2", "KIA_THREADFIN_2_PORT", 34401))
GRABBER = "tv.plex.grabbers.hdhomerun"


class PlexError(Exception):
    pass


@dataclass
class Tuner:
    address: str  # host:port, as Plex reaches it
    title: str  # what Plex calls the tuner and its guide
    channels: list[str]  # Threadfin's channel numbers

    @property
    def guide(self) -> str:
        return f"http://{self.address}/xmltv/threadfin.xml"

    @property
    def lineup(self) -> str:
        return f"lineup://tv.plex.providers.epg.xmltv/{urllib.parse.quote(self.guide, safe='')}#{urllib.parse.quote(self.title)}"


def _get_json(url: str, timeout: float = 15) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def playlist_title(base: Path, folder: str, output_dir: Path) -> str | None:
    """The title of the kia playlist a Threadfin reads (its M3U file), from its settings."""
    try:
        files = json.loads((base / folder / "conf" / "settings.json").read_text()).get("files", {}).get("m3u", {})
    except (OSError, ValueError):
        return None
    names = [Path(urllib.parse.urlparse(f.get("file.source", "")).path).name for f in files.values()]
    try:
        profiles = {p["file"]: p["title"] for p in json.loads((output_dir / "profiles.json").read_text())["profiles"]}
    except (OSError, ValueError, KeyError):
        profiles = {}
    titles = [profiles.get(n, n.removesuffix(".m3u")) for n in names if n.endswith(".m3u")]
    return ", ".join(titles) or None


def in_stack(plex_url: str) -> bool:
    """Whether Plex runs in kia's own compose stack (the plex-server profile), where it
    reaches each Threadfin by its name on Docker's network."""
    return urllib.parse.urlparse(plex_url).hostname == "plex"


# Where Plex in the stack keeps its settings (compose.yaml mounts KIA_PLEX_CONFIG, by
# default plex/config in the kia folder, as /config).
PREFERENCES = Path("plex/config/Library/Application Support/Plex Media Server/Preferences.xml")


def own_token(base: Path) -> str | None:
    """The token Plex in the stack saved for itself when it was claimed, if kia can read it."""
    import xml.etree.ElementTree as ET  # Plex writes this file itself

    try:
        return ET.parse(base / PREFERENCES).getroot().get("PlexOnlineToken") or None
    except (OSError, ET.ParseError):
        return None


def find_tuners(base: Path, output_dir: Path, host: str | None, log=print, getter=_get_json) -> list[Tuner]:
    """Each running Threadfin of this kia: its address, its playlist's title, and its channels.
    host: this computer's address; None when Plex is in the stack, which reaches each
    Threadfin by its service name instead."""
    found = []
    for i, (folder, setting, default) in enumerate(FOLDERS, 1):
        if not (base / folder / "conf" / "settings.json").exists():
            continue
        port = os.environ.get(setting) or str(default)
        address = f"{folder}:34400" if host is None else f"{host}:{port}"
        try:
            lineup = getter(f"http://{address}/lineup.json")
        except (OSError, ValueError) as exc:
            log(f"Threadfin {i} isn't answering at {address} ({exc}); leaving it out.")
            continue
        channels = [str(c["GuideNumber"]) for c in lineup if isinstance(c, dict) and c.get("GuideNumber")]
        if not channels:
            log(f"Threadfin {i} at {address} has no channels yet (see its Mapping page); leaving it out.")
            continue
        name = playlist_title(base, folder, output_dir)
        found.append(Tuner(address, name or f"Threadfin {i}", channels))
    return found


class Plex:
    """The few calls of Plex Media Server's API that Plex Web makes to set up a DVR."""

    def __init__(self, url: str, token: str, timeout: float = 120, startup_wait: float = 300, retry_every: float = 5):
        self.url, self.timeout = url.rstrip("/"), timeout
        # A new Plex answers its health check and /identity well before it accepts changes:
        # for its first minutes it turns them away with 400 "the server is still starting
        # up". Calls wait for it, up to startup_wait seconds.
        self.startup_wait, self.retry_every = startup_wait, retry_every
        self.headers = {"Accept": "application/json", "X-Plex-Token": token, "X-Plex-Product": "kia",
                        "X-Plex-Client-Identifier": "kodi-iptv-assistant"}  # fmt: skip

    def call(self, method: str, path: str, params: dict | None = None) -> dict:
        query = "?" + urllib.parse.urlencode(params, safe="") if params else ""
        data = b"" if method in ("POST", "PUT") else None
        give_up = time.monotonic() + self.startup_wait
        while True:
            req = urllib.request.Request(self.url + path + query, method=method, headers=self.headers, data=data)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read()
                break
            except urllib.error.HTTPError as exc:
                said = exc.read(500).decode(errors="replace") if exc.fp else ""
                if "starting up" in said and time.monotonic() < give_up:
                    time.sleep(self.retry_every)
                    continue
                hint = " (check the token)" if exc.code == 401 else " (Plex is still starting up)" if "starting up" in said else ""
                raise PlexError(f"Plex answered {exc.code} to {method} {path}{hint}") from None
            except OSError as exc:
                raise PlexError(f"Can't reach Plex at {self.url}: {exc}") from None
        return json.loads(body) if body.strip().startswith(b"{") else {}

    def text(self, path: str, params: dict | None = None) -> str:
        """A GET that answers with text (a transcoder playlist). Plex picks how to transcode
        from the player it's told about, so these name a common one."""
        query = "?" + urllib.parse.urlencode(params, safe="") if params else ""
        headers = {**self.headers, "X-Plex-Product": "Plex Web", "X-Plex-Platform": "Chrome"}
        req = urllib.request.Request(self.url + path + query, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read().decode(errors="replace")
        except OSError as exc:
            raise PlexError(f"Plex didn't answer {path}: {exc}") from None

    def dvrs(self) -> list[dict]:
        return self.call("GET", "/livetv/dvrs").get("MediaContainer", {}).get("Dvr", [])

    def sessions(self) -> list[dict]:
        """What's playing now, one dict per player: player (its name), player_id (Plex's id
        for the device), product, kind (live for Live TV,
        else movie, episode, track...), video (copy, transcode, or directplay), hw (gpu or cpu
        when converting, else none), location
        (lan or wan), and state (playing, paused, buffering)."""
        out = []
        for m in self.call("GET", "/status/sessions").get("MediaContainer", {}).get("Metadata", []):
            player, ts = m.get("Player") or {}, m.get("TranscodeSession") or {}
            out.append({
                "player": player.get("title") or player.get("product") or "?",
                "player_id": player.get("machineIdentifier") or player.get("title") or "?",
                "product": player.get("product") or "?",
                "kind": "live" if str(m.get("live", "")) == "1" else m.get("type") or "?",
                "video": ts.get("videoDecision") or "directplay",
                # Converting on the graphics card (Plex's hardware transcoding) or the processor.
                "hw": ("gpu" if ts.get("transcodeHwEncoding") or ts.get("transcodeHwDecoding") or ts.get("transcodeHwFullPipeline")
                       else "cpu") if ts.get("videoDecision") == "transcode" else "none",
                "location": (m.get("Session") or {}).get("location") or "?",
                "state": player.get("state") or "?",
            })  # fmt: skip
        return out


def _guide_port(lineup_id: str) -> str | None:
    """The port of the Threadfin an XMLTV lineup reads, from its id."""
    if not lineup_id.startswith("lineup://tv.plex.providers.epg.xmltv/"):
        return None
    url = urllib.parse.unquote(lineup_id.split("/", 3)[3].split("#")[0])
    return urllib.parse.urlparse(url).port and str(urllib.parse.urlparse(url).port) if url.endswith("/xmltv/threadfin.xml") else None


def set_up(plex: Plex, tuners: list[Tuner], log=print) -> dict:
    """Make or update the Threadfin DVR. Returns it as Plex describes it."""
    if not tuners:
        raise PlexError("No Threadfin to add. Is COMPOSE_PROFILES=plex in .env, and is Threadfin set up?")
    # Plex knows a tuner by its device ID, whatever address it was added with, so register
    # them all first, then look for a DVR that already has one of them.
    devices = []
    for t in tuners:
        found = plex.call("POST", f"/media/grabbers/{GRABBER}/devices", {"uri": t.address}).get("MediaContainer", {})
        device = (found.get("Device") or [None])[0]
        if not device:
            raise PlexError(f"Plex couldn't add the tuner at {t.address}; can the Plex computer reach it?")
        devices.append(device)
    keys = {str(d["key"]) for d in devices}
    dvr = next((d for d in plex.dvrs() if any(str(dv.get("key")) in keys for dv in d.get("Device", []))), None)
    for t, device in zip(tuners, devices, strict=True):
        key, port = str(device["key"]), t.address.rsplit(":", 1)[-1]
        if dvr is None:
            made = plex.call("POST", "/livetv/dvrs", {"device": device["uuid"], "lineup": t.lineup, "language": "eng",
                                                      "country": "usa", "postalCode": ""})  # fmt: skip
            dvr = made["MediaContainer"]["Dvr"][0]
            log(f"Made a new DVR in Plex for kia's Threadfins (DVR {dvr['key']}).")
        else:
            if not any(_guide_port(lu.get("id", "")) == port for lu in dvr.get("Lineup", [])):
                plex.call("PUT", f"/livetv/dvrs/{dvr['key']}/lineups", {"lineup": t.lineup})
            if not any(str(dv.get("key")) == key for dv in dvr.get("Device", [])):
                plex.call("PUT", f"/livetv/dvrs/{dvr['key']}/devices/{key}")
        mapping = {"channelsEnabled": ",".join(t.channels)}
        for n in t.channels:
            mapping[f"channelMappingByKey[{n}]"] = n
            mapping[f"channelMapping[{n}]"] = n
        plex.call("PUT", f"/media/grabbers/devices/{key}/channelmap", mapping)
        plex.call("PUT", f"/media/grabbers/devices/{key}", {"title": t.title, "enabled": "1"})
        log(f"{t.title}: {len(t.channels)} channels, guide {t.guide}")
        dvr = next(d for d in plex.dvrs() if str(d["key"]) == str(dvr["key"]))
    plex.call("POST", f"/livetv/dvrs/{dvr['key']}/reloadGuide")
    log("Plex is loading the guide now; the channels show under Live TV in a few minutes.")
    return dvr


def remove(plex: Plex, tuners: list[Tuner], log=print) -> bool:
    """Delete the DVR that holds kia's Threadfins, if it holds nothing else. Returns whether it did."""
    addresses = {t.address for t in tuners}
    keys = set()
    for t in tuners:
        device = (plex.call("POST", f"/media/grabbers/{GRABBER}/devices", {"uri": t.address}).get("MediaContainer", {})
                  .get("Device") or [None])[0]  # fmt: skip
        if device:
            keys.add(str(device["key"]))
    for dvr in plex.dvrs():
        mine = [dv for dv in dvr.get("Device", []) if str(dv.get("key")) in keys]
        if not mine:
            continue
        if len(mine) < len(dvr.get("Device", [])):
            raise PlexError(f"DVR {dvr['key']} also has other tuners, so kia won't delete it. "
                            "Remove kia's Threadfins from it on Plex's Live TV & DVR page instead.")  # fmt: skip
        plex.call("DELETE", f"/livetv/dvrs/{dvr['key']}")
        log(f"Deleted DVR {dvr['key']} ({', '.join(sorted(addresses))}). Your other DVRs are as they were.")
        return True
    log("Plex has no DVR with kia's Threadfins.")
    return False


# Plex in the stack keeps the codecs it downloads with its settings. A new Plex has none,
# and Live TV alone never makes it fetch them: it only copies the stream, but reading the
# audio's sample rate needs the AAC codec, so every channel fails with "Could not tune
# channel". Playing any video once makes Plex download them; warm_up does that for it.
CODECS = Path("plex/config/Library/Application Support/Plex Media Server/Codecs")
WARMUP = Path("plex/warmup")  # kia writes here; Plex reads it as /kia-warmup (compose.yaml)
WARMUP_LIBRARY = "kia codec warm-up"


def has_codecs(base: Path) -> bool:
    """Whether Plex in the stack has its AAC codec, which Live TV needs. Plex for x86
    calls it libaac_decoder.so; Plex for Arm (Docker on an Apple-silicon Mac, a Raspberry
    Pi) calls it libaac_lc_decoder.so."""
    folder = base / CODECS
    return any(folder.glob("*/libaac_decoder.so")) or any(folder.glob("*/libaac_lc_decoder.so"))


def _wait(check, seconds: float, step: float = 2) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(step)
    return check()


def _scanning(plex: Plex, skip: str) -> list[str]:
    """The libraries Plex is scanning now, apart from skip. Plex scans one at a time, so a
    new library waits until they're done."""
    sections = plex.call("GET", "/library/sections").get("MediaContainer", {}).get("Directory", [])
    return [d.get("title", "?") for d in sections if d.get("refreshing") not in (None, False, 0, "0") and d.get("key") != skip]


def _a_video(plex: Plex) -> str | None:
    """A movie or episode Plex already has with AAC sound (its ratingKey), to play instead of
    a clip. Plex downloads a codec when a video needs it, so the sound must be AAC."""
    sections = plex.call("GET", "/library/sections").get("MediaContainer", {}).get("Directory", [])
    for d in sections:
        kind = {"movie": "1", "show": "4"}.get(d.get("type", ""))
        if not kind or d.get("title") == WARMUP_LIBRARY:
            continue
        found = plex.call("GET", f"/library/sections/{d['key']}/all",
                          {"type": kind, "audioCodec": "aac", "X-Plex-Container-Start": "0", "X-Plex-Container-Size": "1"})  # fmt: skip
        items = found.get("MediaContainer", {}).get("Metadata", [])
        if items:
            return str(items[0]["ratingKey"])
    return None


def _play(plex: Plex, base: Path, key: str, wait: float) -> bool:
    """Start Plex's transcoder on one item, until the codecs arrive (or wait runs out)."""
    session = "kia-codec-warm-up"
    playlist = plex.text("/video/:/transcode/universal/start.m3u8", {
        "path": f"/library/metadata/{key}", "mediaIndex": "0", "partIndex": "0", "protocol": "hls",
        "directPlay": "0", "directStream": "0", "videoQuality": "50", "maxVideoBitrate": "720", "session": session})  # fmt: skip
    variant = next((line for line in playlist.splitlines() if line and not line.startswith("#")), "")
    if variant:
        plex.text(f"/video/:/transcode/universal/{variant}")  # asks the transcoder for the first piece
    ok = _wait(lambda: has_codecs(base), wait)
    plex.text("/video/:/transcode/universal/stop", {"session": session})
    return ok


def warm_up(plex: Plex, base: Path, log=print, ffmpeg: str = "ffmpeg", wait: float = 90, busy_wait: float = 1200,
            step: float = 2) -> bool:
    """Have a new Plex download its codecs by playing a video through its transcoder for a
    moment: one of its own if it has any, or else a 10-second clip kia makes and adds as a
    library, then removes. Returns whether the codecs came. Plex scans one library at a
    time, so the clip's library waits while Plex scans others, up to busy_wait seconds."""
    log("Plex hasn't downloaded its codecs yet, which Live TV needs; playing a few seconds of video so it does.")
    key = _a_video(plex)
    ok = _play(plex, base, key, wait) if key else _warm_up_with_clip(plex, base, log, ffmpeg, wait, busy_wait, step)
    log("Plex has its codecs now." if ok else "Plex still has no codecs; Live TV may fail until you play any video in Plex.")
    return ok


def _warm_up_with_clip(plex: Plex, base: Path, log, ffmpeg: str, wait: float, busy_wait: float, step: float) -> bool:
    folder = base / WARMUP
    clip = folder / "kia warm-up (2026).mp4"
    folder.mkdir(parents=True, exist_ok=True)
    made = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
                           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "10", "-c:v", "libx264",
                           "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
                          capture_output=True, timeout=120, check=False)  # fmt: skip
    if made.returncode != 0:
        raise PlexError(f"Couldn't make the warm-up clip: {made.stderr.decode(errors='replace').strip()[-200:]}")
    section = None
    try:
        plex.call("POST", "/library/sections", {"name": WARMUP_LIBRARY, "type": "movie", "agent": "tv.plex.agents.movie",
                                                "scanner": "Plex Movie", "language": "en-US", "location": "/kia-warmup"})  # fmt: skip
        sections = plex.call("GET", "/library/sections").get("MediaContainer", {}).get("Directory", [])
        section = next((d["key"] for d in sections if d.get("title") == WARMUP_LIBRARY), None)
        if section is None:
            raise PlexError("Plex didn't make the warm-up library.")
        items: list = []

        def scanned() -> bool:
            items[:] = plex.call("GET", f"/library/sections/{section}/all").get("MediaContainer", {}).get("Metadata", [])
            return bool(items)

        start = time.monotonic()
        deadline, told = start + wait, False
        while not scanned():
            busy = _scanning(plex, section)
            now = time.monotonic()
            if busy:
                if not told:
                    log(f"Plex is scanning {', '.join(busy)} first (it scans one library at a time); waiting for it.")
                    told = True
                deadline = min(max(deadline, now + wait), start + busy_wait)
            if now >= deadline:
                if busy:
                    raise PlexError(f"Plex is still scanning {', '.join(busy)}. Run this again when it's done.")
                raise PlexError("Plex didn't find the warm-up clip.")
            time.sleep(step)
        return _play(plex, base, str(items[0]["ratingKey"]), wait)
    finally:
        if section is not None:
            plex.call("DELETE", f"/library/sections/{section}")
        clip.unlink(missing_ok=True)

