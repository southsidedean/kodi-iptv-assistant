"""Radio stations from radio-browser.info, a free community database of internet radio,
and from two stations that publish their own: SomaFM's channels and KEXP."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from kia import __version__
from kia.m3u import Entry

PREFIX = "radio-browser:"
SERVERS = (
    "de1.api.radio-browser.info",
    "nl1.api.radio-browser.info",
    "at1.api.radio-browser.info",
    "all.api.radio-browser.info",
)
# sources.txt filter -> radio-browser search parameter
FILTERS = {
    "country": "countrycode",
    "state": "state",
    "language": "language",
    "tag": "tag",
    "tags": "tagList",
    "codec": "codec",
    "station": "name",
}
OPTIONS = {"top", "min_bitrate"}
DEFAULT_TOP = 100
MAX_TOP = 2000


class RadioError(Exception):
    pass


def build_query(params: dict[str, str]) -> dict[str, str]:
    """Turn sources.txt filters into radio-browser search parameters (most popular first)."""
    unknown = sorted(set(params) - set(FILTERS) - OPTIONS)
    if unknown:
        raise ValueError(f"radio-browser: unknown filter(s) {unknown}; use {sorted(set(FILTERS) | OPTIONS)}")
    try:
        top = int(params.get("top", DEFAULT_TOP))
    except ValueError:
        raise ValueError(f"radio-browser: top={params['top']!r} must be a number") from None
    if not 1 <= top <= MAX_TOP:
        raise ValueError(f"radio-browser: top must be between 1 and {MAX_TOP}")
    if params.get("min_bitrate") and not params["min_bitrate"].isdigit():
        raise ValueError(f"radio-browser: min_bitrate={params['min_bitrate']!r} must be a number, in kbps")
    query = {"hidebroken": "true", "order": "votes", "reverse": "true", "limit": str(top)}
    for key, api_key in FILTERS.items():
        if params.get(key):
            value = params[key]
            query[api_key] = value.upper() if key == "country" else value
    return query


def fetch_stations(params: dict[str, str], timeout: float = 30) -> list[dict]:
    query = urllib.parse.urlencode(build_query(params))
    errors = []
    for server in SERVERS:
        url = f"https://{server}/json/stations/search?{query}"
        req = urllib.request.Request(url, headers={"User-Agent": f"kodi-iptv-assistant/{__version__}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(f"{server}: {getattr(exc, 'reason', exc)}")
    raise RadioError("radio-browser unreachable (" + "; ".join(errors) + ")")


def genre(tags: str) -> str:
    first = next((t.strip() for t in (tags or "").split(",") if t.strip()), "")
    return first[0].upper() + first[1:] if first else "Radio"


def to_entries(stations: list[dict], source: str, min_bitrate: int = 0) -> list[Entry]:
    entries = []
    for st in stations:
        name = " ".join((st.get("name") or "").split())
        url = st.get("url_resolved") or st.get("url")
        if not name or not url or (min_bitrate and (st.get("bitrate") or 0) < min_bitrate):
            continue
        attrs = {"tvg-id": f"radio.{st.get('stationuuid', '')}"}
        if str(st.get("favicon") or "").startswith(("http://", "https://")):
            attrs["tvg-logo"] = st["favicon"]
        attrs["group-title"] = genre(st.get("tags", ""))
        attrs["radio"] = "true"
        entries.append(Entry(name=name, url=url, attrs=attrs, source=source))
    return entries


def load(params: dict[str, str], source: str) -> list[Entry]:
    stations = fetch_stations(params)
    return to_entries(stations, source, int(params.get("min_bitrate") or 0))


# SomaFM: listener-supported, commercial-free channels. Its channel list names each one,
# its genres, logo, and playlists (.pls) that point at the streams.
SOMAFM_CHANNELS = "https://api.somafm.com/channels.json"
# KEXP, Seattle: its own 160 kbps AAC stream (kexp.org/listen).
KEXP_STREAM = "https://kexp.streamguys1.com/kexp160.aac"
# The choices each station line takes (radio-browser's are FILTERS and OPTIONS above).
STATION_PARAMS = {"somafm": {"channels"}, "kexp": set()}


def check_params(kind: str, params: dict[str, str]) -> None:
    """Check a station line's choices when kia reads the sources file."""
    if kind == "radio-browser":
        build_query(params)
        return
    unknown = sorted(set(params) - STATION_PARAMS[kind])
    if unknown:
        allowed = ", ".join(sorted(STATION_PARAMS[kind])) or "none"
        raise ValueError(f"{kind}: unknown choice(s) {unknown}; it takes: {allowed}")


def _get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"kodi-iptv-assistant/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def pls_stream(text: str) -> str | None:
    """The first stream in a .pls playlist (File1=...)."""
    files = sorted((int(m[1]), m[2].strip()) for m in re.finditer(r"^File(\d+)=(.+)$", text, re.M))
    return files[0][1] if files else None


def somafm(params: dict[str, str], source: str, get=_get) -> list[Entry]:
    """SomaFM's channels, or only those in channels= (SomaFM's ids, such as groovesalad)."""
    try:
        channels = json.loads(get(SOMAFM_CHANNELS)).get("channels", [])
    except (OSError, ValueError) as exc:
        raise RadioError(f"SomaFM's channel list didn't load ({exc})") from None
    wanted = {c.strip().lower() for c in params.get("channels", "").split(",") if c.strip()}
    unknown = sorted(wanted - {str(c.get("id", "")).lower() for c in channels})
    if unknown:
        raise RadioError(f"SomaFM has no channel {', '.join(unknown)} (the ids are in its channel list, {SOMAFM_CHANNELS})")
    entries = []
    for ch in channels:
        cid = str(ch.get("id") or "").lower()
        if not cid or (wanted and cid not in wanted):
            continue
        # Its best playlist: AAC at its highest quality, else MP3.
        lists = ch.get("playlists") or []
        best = next((p for fmt in ("aac", "mp3") for p in lists if p.get("format") == fmt and p.get("quality") == "highest"), None)
        try:
            url = pls_stream(get(best["url"]).decode("utf-8", errors="replace")) if best else None
        except OSError:
            url = None
        if not url:
            continue  # this channel's playlist didn't answer; the rest still count
        attrs = {"tvg-id": f"radio.somafm.{cid}", "group-title": genre((ch.get("genre") or "").replace("|", ",")), "radio": "true"}
        logo = ch.get("xlimage") or ch.get("largeimage") or ch.get("image") or ""
        if logo.startswith("https://"):
            attrs["tvg-logo"] = logo
        entries.append(Entry(name=f"SomaFM {' '.join(str(ch.get('title') or cid).split())}", url=url, attrs=attrs, source=source))
    if not entries:
        raise RadioError("none of SomaFM's channels answered")
    return entries


def kexp(source: str) -> list[Entry]:
    """KEXP. Its show schedule comes from kexp.org (kia.native), for a station named "KEXP"."""
    return [Entry(name="KEXP", url=KEXP_STREAM, attrs={"tvg-id": "radio.kexp", "group-title": "Eclectic", "radio": "true"},
                  source=source)]  # fmt: skip


def load_stations(url: str, params: dict[str, str], source: str) -> list[Entry]:
    """The stations a station line asks for: radio-browser:, somafm:, or kexp:."""
    kind = url.partition(":")[0].strip().lower()
    if kind == "somafm":
        return somafm(params, source)
    if kind == "kexp":
        return kexp(source)
    return load(params, source)
