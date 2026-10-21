"""Radio stations from radio-browser.info, a free community database of internet radio."""

from __future__ import annotations

import json
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
