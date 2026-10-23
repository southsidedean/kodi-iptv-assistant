"""Jellyfin's sessions, for kia's /metrics (kia_jellyfin_sessions), the way kia reads Plex's.

Set KIA_JELLYFIN_URL (http://jellyfin:8096 for Jellyfin in the stack) and KIA_JELLYFIN_TOKEN,
an API key from Jellyfin's Dashboard (API Keys).
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request

from kia import __version__


class JellyfinError(Exception):
    pass


# Jellyfin's item types -> the kinds kia reports for Plex too (live is live TV).
KINDS = {"TvChannel": "live", "LiveTvChannel": "live", "Movie": "movie", "Episode": "episode", "Audio": "track",
         "MusicVideo": "clip", "Video": "clip", "Recording": "movie"}  # fmt: skip


def location(endpoint: str) -> str:
    """lan for a player on your network (a private address), wan for one outside it."""
    host = (endpoint or "").strip()
    if host.startswith("["):
        host = host[1:].split("]")[0]
    elif host.count(":") == 1:
        host = host.split(":")[0]  # IPv4 with a port
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "?"
    if ip.version == 6 and ip.ipv4_mapped:  # ::ffff:192.168.1.20
        ip = ip.ipv4_mapped
    return "lan" if ip.is_private or ip.is_loopback or ip.is_link_local else "wan"


class Jellyfin:
    def __init__(self, url: str, token: str, timeout: float = 5):
        self.url, self.timeout = url.rstrip("/"), timeout
        self.headers = {"Accept": "application/json",
                        "Authorization": f'MediaBrowser Client="kia", Device="kia", DeviceId="kia", '
                                         f'Version="{__version__}", Token="{token}"'}  # fmt: skip

    def call(self, path: str) -> object:
        req = urllib.request.Request(self.url + path, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as exc:
            hint = " (check KIA_JELLYFIN_TOKEN)" if exc.code == 401 else ""
            raise JellyfinError(f"Jellyfin answered {exc.code} to {path}{hint}") from None
        except (OSError, ValueError) as exc:
            raise JellyfinError(f"Can't reach Jellyfin at {self.url}: {exc}") from None

    def sessions(self) -> list[dict]:
        """What's playing now, in the shape Plex.sessions uses: player, player_id, product,
        kind, video (copy, transcode, or directplay), hw (gpu or cpu when converting, else
        none), location (lan or wan), and state (playing or paused)."""
        out = []
        for s in self.call("/Sessions?activeWithinSeconds=120") or []:
            item = s.get("NowPlayingItem")
            if not isinstance(item, dict):
                continue  # signed in, but not playing anything
            play, tc = s.get("PlayState") or {}, s.get("TranscodingInfo") or {}
            method = play.get("PlayMethod") or "DirectPlay"
            converting = method == "Transcode" and not tc.get("IsVideoDirect", False)
            video = "transcode" if converting else "copy" if method in ("Transcode", "DirectStream") else "directplay"
            accel = str(tc.get("HardwareAccelerationType") or "none").lower()
            out.append({
                "player": s.get("DeviceName") or s.get("Client") or "?",
                # Jellyfin's ids live next to Plex's in one pseudonym list, so they're kept apart.
                "player_id": "jellyfin:" + str(s.get("DeviceId") or s.get("DeviceName") or "?"),
                "product": s.get("Client") or "?",
                "kind": KINDS.get(item.get("Type") or "", (item.get("Type") or "?").lower()),
                "video": video,
                "hw": ("gpu" if accel != "none" else "cpu") if converting else "none",
                "location": location(s.get("RemoteEndPoint") or ""),
                "state": "paused" if play.get("IsPaused") else "playing",
            })  # fmt: skip
        return out
