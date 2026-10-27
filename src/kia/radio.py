"""Radio stations from radio-browser.info, a free community database of internet radio,
and from two stations that publish their own: SomaFM's channels and KEXP."""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from kia import lan, native
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
        try:
            return json.loads(_get(url, timeout))
        except (OSError, ValueError, http.client.HTTPException) as exc:
            errors.append(f"{server}: {getattr(exc, 'reason', exc)}")
    raise RadioError("radio-browser unreachable (" + "; ".join(errors) + ")")


def genre(tags: str) -> str:
    first = next((t.strip() for t in (tags or "").split(",") if t.strip()), "")
    return first[0].upper() + first[1:] if first else "Radio"


def _text(value: object) -> str:
    """A field from a radio service's JSON as text. It's third-party data: a number, a list,
    or null where text belongs is read as no text, instead of failing the check."""
    return value if isinstance(value, str) else ""


def to_entries(stations: object, source: str, min_bitrate: int = 0) -> list[Entry]:
    if not isinstance(stations, list):
        raise RadioError("radio-browser didn't send a list of stations")
    entries = []
    for st in stations:
        if not isinstance(st, dict):
            continue
        name = " ".join(_text(st.get("name")).split())
        url = _text(st.get("url_resolved")) or _text(st.get("url"))
        bitrate = st.get("bitrate") if isinstance(st.get("bitrate"), int) else 0
        if not name or not url or (min_bitrate and bitrate < min_bitrate):
            continue
        attrs = {"tvg-id": f"radio.{_text(st.get('stationuuid'))}"}
        if _text(st.get("favicon")).startswith(("http://", "https://")):
            attrs["tvg-logo"] = st["favicon"]
        attrs["group-title"] = genre(_text(st.get("tags")))
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


MAX_ANSWER = 8 * 1024 * 1024  # bytes: radio-browser's and SomaFM's answers are under 1 MB


def _get(url: str, timeout: float = 30) -> bytes:
    """A station list or .pls playlist, through kia's outbound guard (kia.native.opened): the
    addresses come from other people's services, so neither they nor a redirect may reach
    your network. Cut off at MAX_ANSWER and two minutes."""
    return native.download(url, True, MAX_ANSWER, timeout=timeout)


def pls_stream(text: str) -> str | None:
    """The first stream in a .pls playlist (File1=...)."""
    files = sorted((int(m[1]), m[2].strip()) for m in re.finditer(r"^File(\d+)=(.+)$", text, re.M))
    return files[0][1] if files else None


def somafm(params: dict[str, str], source: str, get=None) -> list[Entry]:
    """SomaFM's channels, or only those in channels= (SomaFM's ids, such as groovesalad)."""
    get = get or _get  # looked up now, not when kia starts, so a test's stand-in applies
    try:
        data = json.loads(get(SOMAFM_CHANNELS))
    except (OSError, ValueError) as exc:
        raise RadioError(f"SomaFM's channel list didn't load ({exc})") from None
    channels = data.get("channels") if isinstance(data, dict) else None
    if not isinstance(channels, list):
        raise RadioError("SomaFM's channel list isn't what kia expects (no list of channels)")
    channels = [c for c in channels if isinstance(c, dict)]
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
        raw = ch.get("playlists")
        lists = [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []
        best = next((p for fmt in ("aac", "mp3") for p in lists if p.get("format") == fmt and p.get("quality") == "highest"), None)
        # The playlist's address comes from SomaFM's list, so it's checked like any stranger's:
        # https only, and not on your network (kia.lan).
        address = _text(best.get("url")) if best else ""
        try:
            fine = address.startswith("https://") and lan.host_of(address) and not lan.is_local(lan.host_of(address))
            url = pls_stream(get(address).decode("utf-8", errors="replace")) if fine else None
        except (OSError, ValueError, http.client.HTTPException):
            url = None
        if not url:
            continue  # this channel's playlist didn't answer; the rest still count
        attrs = {"tvg-id": f"radio.somafm.{cid}", "group-title": genre(_text(ch.get("genre")).replace("|", ",")), "radio": "true"}
        logo = _text(ch.get("xlimage")) or _text(ch.get("largeimage")) or _text(ch.get("image"))
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
