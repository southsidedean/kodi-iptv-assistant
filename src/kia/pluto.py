"""Pluto TV through kia, so its channels keep playing across ad breaks.

Pluto's live streams break most players at ad breaks: the picture goes black or freezes
and only comes back when the channel is started again. Three things cause it:

- The sessions behind the usual jmp2.uk links ask Pluto to leave the ads to the player
  (serverSideAds=false). Players don't insert ads, so Pluto's idea of where the viewer is
  drifts, and every so often it jumps back minutes into the show or into an ad break.
- Pluto's playlist numbering (EXT-X-MEDIA-SEQUENCE) jumps around, and at ad breaks it
  repeats pieces, sends them out of order, or switches clips without marking it.
- Every clip (each ad, each part of a show) starts its own clock, and players that don't
  handle that well lose the picture or the sound.

So for each Pluto channel someone plays, kia keeps its own Pluto session (with Pluto
putting in the ads), its own list of the channel's pieces in time order, and hands players
a plain playlist on one steady clock: each piece is decrypted and its timestamps moved to
carry on from the piece before. Pieces and keys still come straight from Pluto; ffmpeg
only copies (no re-encoding), so it's light work. The one thing kia makes itself is silence,
for a piece that comes without sound, so the sound never stops while the picture goes on.

Channels are idle until a player asks for them, and are let go two minutes after the
last request. Everyone watching a channel shares its session and its pieces.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin

from kia import __version__
from kia.m3u import Entry
from kia.probe import media_env

# jmp2.uk/plu-<id>.m3u8 (iptv-org and most lists), or a Pluto stitcher address.
_CHANNEL_RE = re.compile(r"(?:jmp2\.uk/plu-|pluto\.tv/.*?/channel/)([0-9a-f]{24})\b", re.I)  # incl. /stitch/embed/
_ID_RE = re.compile(r"[0-9a-f]{24}")

BOOT_URL = (
    "https://boot.pluto.tv/v4/start?appName=web&appVersion=9.0.0&deviceVersion=130.0.0"
    "&deviceModel=web&deviceMake=chrome&deviceType=web&clientModelNumber=1.0.0"
    "&clientID={client}&channelID={channel}&serverSideAds=true"
)
FALLBACK_URL = "https://jmp2.uk/plu-{channel}.m3u8"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

RETRY_DELAY = 1.0  # seconds before trying Pluto again
MAX_CHANNELS = 20  # Pluto channels playing at once
CACHE_PIECES = 12  # pieces kept per channel for viewers who are a little behind
# And at most this many bytes of them: with up to 20 channels, pieces of the largest allowed
# size (MAX_DOWNLOAD) would otherwise add up to some 4 GB, more than kia's memory limit.
CACHE_BYTES = 32 * 1024 * 1024
WINDOW = 6  # pieces in each playlist kia hands out
KEEP = 60  # pieces kia remembers per channel
REFRESH_EVERY = 2.0  # seconds between looks at Pluto's playlist
IDLE_AFTER = 120  # seconds without a request before a channel is let go
HOLD_LIMIT = 45  # seconds to hold back old pieces before playing what Pluto sends
SESSION_GAP = 20  # seconds between new sessions, at least
FALLBACK_RETRY = 300  # seconds on the jmp2.uk link before trying a session of our own again
FALLBACK_ALERT = 300  # seconds on the jmp2.uk link before the pluto_fallback alert
START = 10.0  # where kia's clock starts, in seconds
# A piece listed as shorter than this whose sound wouldn't copy is counted, but not reported as a
# problem: too short to hear or to stall a player.
SLIVER = 1.0
# The silence kia puts in a piece that has no sound, when the piece doesn't say what its sound
# would be: Pluto's own format, on every channel looked at (AAC, 48 kHz, two channels).
SILENCE_RATE, SILENCE_CHANNELS = 48000, 2
TIMEOUT = 15


def channel_id(url: str) -> str | None:
    m = _CHANNEL_RE.search(url)
    return m.group(1).lower() if m else None


def any_channel_id(url: str, base_url: str | None) -> str | None:
    """A Pluto channel's id from its address as a playlist has it, or as kia's playlists do
    once it plays through kia's relay (base_url/pluto/<id>.m3u8, see for_players)."""
    if base_url and url.startswith(f"{base_url}/pluto/"):
        cid = url[len(base_url) + len("/pluto/"):].removesuffix(".m3u8").lower()
        return cid if _ID_RE.fullmatch(cid) else None
    return channel_id(url)


# Samsung TV Plus's links, which also carry Pluto TV channels: jmp2.uk's own, from the start.
_PARTNER_RE = re.compile(r"https?://jmp2\.uk/stvp-[^/?#]+", re.I)


def final_url(url: str, timeout: float = TIMEOUT) -> str:
    """Where a link ends up after its redirects (only the answer's start is read). Through
    kia's outbound guard: the link comes from a playlist, and a redirect mustn't take kia to
    a device on your network."""
    from kia import native

    with native.opened(url, True, timeout, agent="VLC/3.0.20 LibVLC/3.0.20") as r:
        return r.geturl()


def partner_channels(entries: list[Entry], log=print, resolve=final_url) -> dict[str, str]:
    """Pluto TV channels that reach kia through another service's link (the Samsung TV
    Plus list has some) play through the relay too: their link ends at Pluto's stream
    server, with the same trouble at ad breaks. Only entries named for Pluto are looked
    up, one request each. Returns {their link: Pluto's own}."""
    found: dict[str, str] = {}
    for e in entries:
        if e.url in found or channel_id(e.url) or not _PARTNER_RE.fullmatch(e.url.split("?")[0]):
            continue
        if "pluto" not in f"{e.name} {e.tvg_id}".lower():
            continue
        try:
            cid = channel_id(resolve(e.url))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log(f"  {e.name}: couldn't follow its link ({exc})")
            continue
        if cid:
            found[e.url] = FALLBACK_URL.format(channel=cid)
    if found:
        log(f"{len(found)} Pluto TV channels from other services' lists play through kia's Pluto relay too")
    return found


def adopt_partner_channels(entries: list[Entry], log=print, resolve=final_url, found: dict[str, str] | None = None) -> int:
    """Point those entries at Pluto's own links (found: from partner_channels, looked up
    earlier). Returns how many changed."""
    found = partner_channels(entries, log, resolve) if found is None else found
    changed = 0
    for e in entries:
        if e.url in found:
            e.url = found[e.url]
            changed += 1
    return changed


def for_players(entries: list[Entry], base_url: str | None) -> list[Entry]:
    """Copies of the entries with each Pluto channel pointed at kia's relay."""
    if not base_url:
        return entries
    out = []
    for e in entries:
        cid = channel_id(e.url)
        out.append(replace(e, url=f"{base_url}/pluto/{cid}.m3u8", vlcopts={}, kodiprops=[]) if cid else e)
    return out


class NotHttps(ValueError):
    """An address the relay won't fetch: not https."""


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Follows redirects only to https addresses. Most things on a home network (a router's
    page, a printer) answer only plain http, so a redirect can't point the relay at them;
    one that answers https could still be reached."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise RedirectRefused(newurl, code, "redirect to an address that isn't https", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RedirectRefused(urllib.error.HTTPError):
    """A redirect off https, which the relay won't follow: not worth asking again."""


_OPENER = urllib.request.build_opener(_HttpsOnly)


# The most the relay downloads for one address. Pluto's pieces are a few seconds of video,
# a few MB each, and its playlists and keys are tiny. The addresses come from other
# people's servers (Pluto's, and jmp2.uk's when kia falls back), so without a limit one
# answer could fill kia's memory, with up to 20 channels at once.
MAX_DOWNLOAD = 16 * 1024 * 1024


class TooBig(ValueError):
    """An answer bigger than MAX_DOWNLOAD."""


def fetch(url: str, timeout: float = TIMEOUT) -> tuple[str, bytes]:
    """(final address, body). Only https: the addresses come from Pluto's playlists (and
    jmp2.uk's), so they go through kia's outbound guard, which refuses one on your network,
    https or not (kia.guard). Without the guard (a proxy of your own), _OPENER alone."""
    from kia import guard, native

    if not url.lower().startswith("https://"):
        raise NotHttps(f"not an https address: {url[:80]}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    g = guard.get()
    with g.ticket() if g else nullcontext() as ticket:
        opener = _OPENER
        if ticket:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _HttpsOnly, native._ThroughTheGuard(ticket.url))
        try:
            r = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            exc.close()  # its connection, which nothing reads: the status and reason are enough
            raise
        with r:
            body = r.read(MAX_DOWNLOAD + 1)  # one byte over says it's too big, without reading on
            if len(body) > MAX_DOWNLOAD:
                raise TooBig(f"more than {MAX_DOWNLOAD // (1024 * 1024)} MB from {url[:80]}")
            return r.geturl(), body


def fetch_retry(url: str, fetcher=None) -> tuple[str, bytes]:
    """Once more after a short wait: Pluto now and then answers 500 or times out."""
    fetcher = fetcher or fetch  # looked up now, not when kia starts, so a test's stand-in applies
    try:
        return fetcher(url)
    except (NotHttps, TooBig, RedirectRefused):
        raise  # it won't be https, or smaller, the next time either
    except (urllib.error.URLError, OSError, ValueError):
        time.sleep(RETRY_DELAY)
        return fetcher(url)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Piece:
    seq: int  # kia's own number
    pdt: datetime  # Pluto's program date-time
    dur: float  # as listed
    uri: str
    key_uri: str | None
    iv: str | None  # hex, or None to use the upstream sequence number
    upstream_seq: int
    clip: str | None  # an ad, or one part of a show
    pos: int | None  # where the piece sits in its clip
    ident: str  # the piece's address without its signature
    t0: float  # where it goes on kia's clock


@dataclass
class Session:
    variant: str
    started: float  # time.monotonic()
    started_at: str
    refresh_after: float  # seconds; Pluto asks for a fresh session after this
    own: bool  # False: the jmp2.uk link (kia's fallback)
    suspicious: bool = False


@dataclass
class Stats:
    rewinds: int = 0
    sessions: int = 0
    errors: int = 0
    fallbacks: int = 0
    silent: int = 0  # pieces whose sound wouldn't copy (SoundDropped)
    soundless: int = 0  # pieces that came from Pluto with no sound (SoundDropped.from_pluto)
    problems: deque = field(default_factory=lambda: deque(maxlen=8))
    # Problems also go to kia's log, which outlives the channel (pluto.json doesn't).
    log: Callable[[str], None] = lambda text: None

    def problem(self, text: str) -> None:
        self.problems.append({"time": _now(), "text": text})
        self.log(text)


def parse_media(text: str, base: str) -> list[dict]:
    """The pieces in one of Pluto's media playlists, in order."""
    out = []
    pdt = key_uri = iv = None
    seq = 0
    m = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text)
    if m:
        seq = int(m.group(1))
    dur = 0.0
    disc = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pdt = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00"))
        elif line.startswith("#EXT-X-KEY:"):
            if "METHOD=NONE" in line:
                key_uri = iv = None
            else:
                u = re.search(r'URI="([^"]+)"', line)
                v = re.search(r"IV=0x([0-9A-Fa-f]+)", line)
                key_uri = urljoin(base, u.group(1)) if u else None
                iv = v.group(1).rjust(32, "0") if v else None
        elif line.startswith("#EXT-X-DISCONTINUITY") and not line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE"):
            disc = True
        elif line.startswith("#EXTINF:"):
            dur = float(line[8:].split(",")[0])
            if not math.isfinite(dur) or dur < 0:  # "inf" and "nan" read as floats too
                raise ValueError(f"a piece's length isn't a number of seconds: {line[:40]!r}")
        elif line and not line.startswith("#"):
            uri = urljoin(base, line)
            out.append({"pdt": pdt, "dur": dur, "uri": uri, "key_uri": key_uri, "iv": iv, "seq": seq, "disc": disc})
            seq += 1
            disc = False
            if pdt is not None:
                pdt = pdt + timedelta(seconds=dur)
    return out


def identify(uri: str) -> tuple[str | None, int | None, str]:
    """(clip, position in the clip, identity). A show comes in parts, each numbered from 0
    in its own folder (.../<range>/hls/...), so the part is part of the clip."""
    plain = unquote(uri).split("?")[0]
    ident = re.sub(r"/sign/[^/]+/[^/]+", "", plain)
    m = re.search(r"(?:creative|clip)/([0-9a-f]{24})", plain)
    part = re.search(r"/([^/]+)/hls/", plain)
    clip = (m.group(1) + (":" + part.group(1) if part else "")) if m else None
    p = re.search(r"(?:seg_|hls_\d+_)(\d+)\.ts|frag[(](\d+)", plain)
    pos = int(p.group(1) or p.group(2)) if p else None
    return clip, pos, ident


def best_variant(text: str, base: str) -> str | None:
    lines = text.splitlines()
    best, url = -1, None
    for i, line in enumerate(lines):
        m = re.match(r"#EXT-X-STREAM-INF:.*?BANDWIDTH=(\d+)", line)
        if m and int(m.group(1)) > best and i + 1 < len(lines):
            best, url = int(m.group(1)), urljoin(base, lines[i + 1].strip())
    return url


class Channel:
    def __init__(self, cid: str, relay: Relay):
        self.cid = cid
        self.relay = relay
        self.trace = relay.trace  # every piece, for working out problems
        self.lock = threading.Lock()  # the piece list
        self.refresh_lock = threading.Lock()  # one look at Pluto at a time
        self.remux_lock = threading.Lock()  # one remux at a time, so viewers share them
        self.session: Session | None = None
        self.pieces: list[Piece] = []
        self.next_seq = 0
        self.furthest: dict[str, int] = {}  # clip -> furthest position handed out
        self.pending_break = False
        self.last_fetch = 0.0
        self.last_added = time.monotonic()
        self.last_session_try = -SESSION_GAP
        self.last_request = time.monotonic()
        self.fallback_since: float | None = None
        self.renew = False
        self.switch_now = False
        self.cache: dict[int, bytes] = {}
        self.keys: dict[str, bytes] = {}
        self.viewers: dict[str, float] = {}
        self.stats = Stats(log=lambda text, cid=cid, relay=relay: relay.log(f"Pluto {cid}: {text}"))

    # Sessions

    def _boot(self) -> Session:
        _, body = fetch_retry(BOOT_URL.format(client=uuid.uuid4(), channel=self.cid), self.relay.fetcher)
        b = json.loads(body)
        master = (f"{b['servers']['stitcher']}/v2/stitch/hls/channel/{self.cid}/master.m3u8"
                  f"?{b['stitcherParams']}&jwt={b['sessionToken']}&masterJWTPassthrough=true")  # fmt: skip
        base, text = fetch_retry(master, self.relay.fetcher)
        variant = best_variant(text.decode(), base)
        if not variant:
            raise ValueError("Pluto's session had no streams in it")
        return Session(variant, time.monotonic(), _now(), float(b.get("refreshInSec") or 28800), True,
                       bool(b.get("isSuspicious")))  # fmt: skip

    def _jmp2(self) -> Session:
        base, text = fetch_retry(FALLBACK_URL.format(channel=self.cid), self.relay.fetcher)
        variant = best_variant(text.decode(), base) or base
        return Session(variant, time.monotonic(), _now(), 86400.0, False)

    def new_session(self, why: str) -> None:
        self.last_session_try = time.monotonic()
        self.renew = False
        try:
            s = self._boot()
        except Exception as exc:  # Pluto's session service is down or changed
            self.stats.errors += 1
            self.stats.problem(f"Couldn't open a Pluto session ({_short(exc)}); using the plain jmp2.uk link")
            try:
                s = self._jmp2()
            except Exception as exc2:
                self.stats.problem(f"The jmp2.uk link failed too ({_short(exc2)})")
                raise
            if self.fallback_since is None:
                self.fallback_since = time.monotonic()
                self.stats.fallbacks += 1
        else:
            if self.fallback_since is not None:
                self.stats.problem("Back on kia's own Pluto session")
            self.fallback_since = None
            if s.suspicious and not (self.session and self.session.suspicious):
                self.stats.problem("Pluto flagged kia's session as suspicious")
        self.session = s
        self.stats.sessions += 1
        self.relay.log(f"Pluto {self.cid}: new session ({why}){'' if s.own else ', on the jmp2.uk link'}")

    # Pluto's playlist -> kia's list of pieces

    def refresh(self) -> None:
        now = time.monotonic()
        s = self.session
        if s is None:
            self.new_session("first viewer")
        elif not s.own and now - self.last_session_try > FALLBACK_RETRY:
            self.new_session("trying our own session again")
        elif now - s.started > s.refresh_after * 1.5:
            self.new_session("session too old")  # no ad break came along to switch at
        elif now - s.started > s.refresh_after - min(1800, s.refresh_after / 4):
            # At the next clip change: from half an hour before Pluto wants a new session,
            # or the last quarter of a short one. Half an hour before a session shorter
            # than that would be from its start, and every clip change would open a new one.
            self.renew = True
        text, base = self._playlist()
        with self.lock:
            added, went_back = self._take(parse_media(text, base))
        if self.switch_now:
            self.switch_now = False
            self.new_session("Pluto asks for a fresh one every few hours")
        elif went_back and not added and now - self.last_session_try > SESSION_GAP:
            self.new_session("Pluto went back")
            self.last_fetch = 0.0

    def _playlist(self) -> tuple[str, str]:
        try:
            base, body = fetch_retry(self.session.variant, self.relay.fetcher)
            text = body.decode(errors="replace")
        except Exception as exc:
            self.stats.errors += 1
            self.stats.problem(f"Pluto's playlist didn't load ({_short(exc)}); new session")
            self.new_session("playlist failed")
            base, body = fetch_retry(self.session.variant, self.relay.fetcher)
            text = body.decode(errors="replace")
        if "#EXTINF" not in text:
            self.stats.errors += 1
            self.stats.problem("Pluto sent something other than a playlist; new session")
            self.new_session("not a playlist")
            base, body = fetch_retry(self.session.variant, self.relay.fetcher)
            text = body.decode(errors="replace")
        return text, base

    def _take(self, listed: list[dict]) -> tuple[int, bool]:
        """Add the new pieces from one look at Pluto's playlist. Returns (added, went back)."""
        added = 0
        went_back = False
        for p in listed:
            if p["pdt"] is None:
                continue
            last = self.pieces[-1] if self.pieces else None
            if last is not None and p["pdt"] <= last.pdt + timedelta(seconds=0.2):
                continue  # had it already
            clip, pos, ident = identify(p["uri"])
            done = self.furthest.get(clip) if clip else None
            back = pos is not None and done is not None and pos <= done
            held_long = time.monotonic() - self.last_added > HOLD_LIMIT
            if back and not held_long:
                if self.trace:
                    self.trace(f"{self.cid[:6]} hold {clip}:{pos} (had {done})")
                went_back = True  # Pluto went back to a piece it already sent; hold it back
                self.pending_break = True
                continue
            if any(x.ident == ident for x in self.pieces[-3:]):
                self.pending_break = True  # the same piece twice in a row
                continue
            if back:
                self.stats.problem("Pluto kept going back; playing what it sends")
            gap = last is not None and abs((p["pdt"] - (last.pdt + timedelta(seconds=last.dur))).total_seconds()) > 1
            brk = last is not None and (p["disc"] or self.pending_break or gap or clip != last.clip)
            self.pending_break = False
            if brk and self.renew and added == 0:
                self.switch_now = True  # at a clip change, where the jump won't show
                break
            piece = Piece(self.next_seq, p["pdt"], p["dur"], p["uri"], p["key_uri"], p["iv"], p["seq"], clip, pos,
                          ident, last.t0 + last.dur if last else START)  # fmt: skip
            self.pieces.append(piece)
            if self.trace:
                self.trace(f"{self.cid[:6]} add {piece.seq} {p['pdt']:%H:%M:%S} {p['dur']} {clip}:{pos}{' break' if brk else ''}")
            self.next_seq += 1
            added += 1
            self.last_added = time.monotonic()
            if clip is not None and pos is not None:
                self.furthest[clip] = pos if back else max(pos, self.furthest.pop(clip, -1))  # popped: now the newest
                while len(self.furthest) > 200:  # only recent clips can come back; forget the oldest
                    del self.furthest[next(iter(self.furthest))]
        if went_back:
            self.stats.rewinds += 1
            self.relay.log(f"Pluto {self.cid}: Pluto went back to pieces it already sent; holding them back")
        del self.pieces[:-KEEP]
        return added, went_back

    def playlist(self) -> str:
        self.last_request = time.monotonic()
        with self.refresh_lock:
            if time.monotonic() - self.last_fetch > REFRESH_EVERY:
                self.last_fetch = time.monotonic()
                self.refresh()
        with self.lock:
            win = self.pieces[-WINDOW:]
        if not win:
            raise ValueError("Pluto hasn't sent anything to play yet")
        target = max(int(p.dur + 0.999) for p in win)
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target}", f"#EXT-X-MEDIA-SEQUENCE:{win[0].seq}"]
        for p in win:
            lines += [f"#EXTINF:{p.dur:.3f},", f"{self.cid}/{p.seq}.ts"]
        return "\n".join(lines) + "\n"

    # One piece, decrypted and moved onto kia's clock

    def piece(self, seq: int, viewer: str) -> bytes | None:
        self.last_request = time.monotonic()
        with self.lock:  # the server is threaded: two viewers can arrive at once
            self.viewers[viewer] = time.monotonic()
            if len(self.viewers) > 50:  # players come and go over hours of watching
                self.viewers = {v: t for v, t in self.viewers.items() if self.last_request - t < 300}
            p = next((x for x in self.pieces if x.seq == seq), None)
        if p is None:
            return None
        with self.remux_lock:
            if seq in self.cache:
                return self.cache[seq]
            data = self._remux(p)
            if self.trace:
                self.trace(f"{self.cid[:6]} sent {seq} at {p.t0:.3f} ({len(data)} bytes) to {viewer}")
            if data:  # a piece that failed is tried again on the next request, not kept
                self.cache[seq] = data
                while len(self.cache) > CACHE_PIECES or (len(self.cache) > 1 and sum(map(len, self.cache.values())) > CACHE_BYTES):
                    del self.cache[next(iter(self.cache))]  # the oldest one in
            return data

    def _key(self, uri: str) -> bytes:
        if uri not in self.keys:
            self.keys = {k: v for k, v in list(self.keys.items())[-8:]}
            key = fetch_retry(uri, self.relay.fetcher)[1]
            if len(key) != 16:  # AES-128's: anything else is never a key, and isn't kept
                raise ValueError(f"the key at {uri[:80]} is {len(key)} bytes, not 16")
            self.keys[uri] = key
        return self.keys[uri]

    def _remux(self, p: Piece) -> bytes:
        try:
            body = fetch_retry(p.uri, self.relay.fetcher)[1]
            key = self._key(p.key_uri).hex() if p.key_uri else None
        except Exception as exc:
            self.stats.errors += 1
            self.stats.problem(f"A piece didn't load from Pluto ({_short(exc)})")
            return b""
        iv = p.iv or f"{p.upstream_seq:032x}"
        try:
            return self.relay.remux(body, key, iv, p.dur, p.t0)
        except SoundDropped as dropped:
            if dropped.from_pluto:
                # Pluto's own doing (some ads come without sound) and filled with silence: worth
                # a count, but not a problem on the Dashboard every ad break.
                self.stats.soundless += 1
                return dropped.data
            # The sound was there but wouldn't copy. A player that follows the sound's clock (a
            # Roku does) stalls on a piece without it, so a whole piece of it is worth knowing about.
            self.stats.silent += 1
            if p.dur >= SLIVER:
                self.stats.problem(f"A {p.dur:.1f}-second piece went out without its sound ({dropped.reason[:80]})")
                self.relay.debug_save(self.cid, p, body, key, iv, dropped)
            return dropped.data
        except Exception as exc:
            self.stats.errors += 1
            self.stats.problem(f"ffmpeg couldn't copy a piece ({_short(exc)})")
            self.relay.debug_save(self.cid, p, body, key, iv, exc)  # off unless the debug folder exists
            return b""

    def status(self) -> dict:
        now = time.monotonic()
        s = self.session
        return {
            "id": self.cid,
            "viewers": sum(1 for t in list(self.viewers.values()) if now - t < 30),
            "session_started": s.started_at if s else None,
            "session_hours": round((now - s.started) / 3600, 1) if s else None,
            "next_refresh_hours": round(max(0.0, s.refresh_after - (now - s.started)) / 3600, 1) if s and s.own else None,
            "own_session": bool(s and s.own),
            "suspicious": bool(s and s.suspicious),
            "fallback_minutes": round((now - self.fallback_since) / 60) if self.fallback_since is not None else None,
            "rewinds": self.stats.rewinds,
            "sessions": self.stats.sessions,
            "errors": self.stats.errors,
            "fallbacks": self.stats.fallbacks,
            "silent": self.stats.silent,
            "soundless": self.stats.soundless,
            "problems": list(self.stats.problems),
        }


class SoundDropped(Exception):  # not an error: the piece still plays, without its own sound
    """remux passed on a piece's picture without its sound. data is that copy, ready to play,
    with silence in place of the sound when ffmpeg could make it; reason, why. from_pluto: the
    piece came with no sound at all, rather than sound that wouldn't copy."""

    def __init__(self, data: bytes, reason: str, from_pluto: bool = False):
        super().__init__(reason)
        self.data, self.reason, self.from_pluto = data, reason, from_pluto


def _short(exc: BaseException) -> str:
    text = str(getattr(exc, "reason", None) or exc) or type(exc).__name__
    return text[:120]


@dataclass
class Start:
    video: float  # the first picture's timestamp, in seconds (the sound's, in a piece without one)
    audio: float | None  # the first sound's, or None: no sound in the part looked at
    rate: int  # the sound's format as the piece declares it, else Pluto's usual
    channels: int


def probe_start(path: str, key: str | None, iv: str, ffprobe: str = "ffprobe", whole: bool = False) -> Start:
    """Where a piece's picture and sound start, from its first 3 seconds (whole: all of it)."""
    src = ["-f", "mpegts", "-key", key, "-iv", iv, "-i", f"crypto:{path}"] if key else ["-f", "mpegts", "-i", path]
    look = [] if whole else ["-read_intervals", "%+3"]
    out = subprocess.run([ffprobe, "-v", "error", *look, "-show_entries",
                          "stream=codec_type,sample_rate,channels:packet=codec_type,dts_time", "-of", "json", *src],
                         capture_output=True, timeout=30, check=False, env=media_env()).stdout  # fmt: skip
    try:
        found = json.loads(out or b"{}")
    except ValueError:
        found = {}
    found = found if isinstance(found, dict) else {}
    first: dict[str, float] = {}
    for p in found.get("packets") or []:
        kind, dts = (p.get("codec_type"), p.get("dts_time")) if isinstance(p, dict) else (None, None)
        if kind in ("video", "audio") and kind not in first and dts not in (None, "", "N/A"):
            first[kind] = float(dts)
    if not first:
        raise ValueError("no audio or video in it")
    # The sound's declared format, read as numbers only: these go into ffmpeg's silence below.
    streams = found.get("streams") or []
    sound = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"), {})
    rate, channels = str(sound.get("sample_rate", "")), sound.get("channels")
    return Start(video=first.get("video", first.get("audio", 0.0)), audio=first.get("audio"),
                 rate=int(rate) if rate.isdigit() and 8000 <= int(rate) <= 96000 else SILENCE_RATE,
                 channels=channels if type(channels) is int and channels in (1, 2) else SILENCE_CHANNELS)  # fmt: skip


def remux(body: bytes, key: str | None, iv: str, dur: float, t0: float,
          ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> bytes:  # fmt: skip
    """Decrypt one piece and move it onto kia's clock, starting at t0 seconds. Audio keeps
    its small offset from the video, unless it's out by a second or more (as in some ads).

    Some ads come with no sound at all, for half a minute or more. Passed on like that, the
    sound has a hole the picture doesn't, and players and Plex's converter close it up: the
    sound after the break plays against the wrong picture until the channel is started again.
    So kia puts silence of the piece's length in its place, and raises SoundDropped (from_pluto)
    with that copy, so the relay can count it.

    That covers the short sliver at the end of some ads, too, which declares an audio stream
    with no usable sample rate and no audio packets: copied as it is, the mpegts muxer refuses it
    ("sample rate not set"). A piece whose sound is there but won't copy gets silence as well, or
    no sound if even that fails, rather than being dropped, which stalls the player and loops the
    session. Its SoundDropped isn't from_pluto, and the relay reports those a second or longer."""
    tmp = tempfile.mkdtemp(prefix="kia-pluto-")
    try:
        path = f"{tmp}/in.ts"
        with open(path, "wb") as f:
            f.write(body)
        start = probe_start(path, key, iv, ffprobe)
        if start.audio is None:  # nothing in the first 3 seconds: look at it all before calling it soundless
            start = probe_start(path, key, iv, ffprobe, whole=True)
        tv = round((t0 - start.video) * 90000)
        a0 = start.audio if start.audio is not None else start.video
        ta = tv if abs(a0 - start.video) < 1 else round((t0 - a0) * 90000)
        src = ["-f", "mpegts", "-key", key, "-iv", iv, "-i", f"crypto:{path}"] if key else ["-f", "mpegts", "-i", path]

        def copy(sound: str) -> subprocess.CompletedProcess:
            """sound: "copy" (the piece's own), "silence" (made here, from t0), or "none"."""
            maps, codecs, bsf = ["-map", "0:v:0?"], ["-c:v", "copy"], ["-bsf:v", f"setts=pts=PTS+{tv}:dts=DTS+{tv}"]
            silence = []
            if sound == "copy":
                maps += ["-map", "0:a:0?"]
                codecs += ["-c:a", "copy"]
                bsf += ["-bsf:a", f"setts=pts=PTS+{ta}:dts=DTS+{ta}"]
            elif sound == "silence":
                # A second input, already on kia's clock (itsoffset), as long as the piece, in the
                # piece's own sound format so a player sees no change at the join.
                layout = "mono" if start.channels == 1 else "stereo"
                silence = ["-itsoffset", f"{t0:.6f}", "-f", "lavfi", "-t", f"{dur:.3f}",
                           "-i", f"anullsrc=r={start.rate}:cl={layout}"]  # fmt: skip
                maps += ["-map", "1:a:0"]
                codecs += ["-c:a", "aac", "-b:a", "64k"]
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-copyts",
                   "-t", f"{dur:.3f}", *src,  # Pluto sometimes packs in more than it lists
                   *silence, *maps, *codecs, *bsf,
                   "-muxdelay", "0", "-muxpreload", "0", "-mpegts_flags", "+initial_discontinuity",
                   "-f", "mpegts", "pipe:1"]  # fmt: skip
            return subprocess.run(cmd, capture_output=True, timeout=30, check=False, env=media_env())

        if start.audio is None:  # no sound came from Pluto
            filled = copy("silence")
            if filled.stdout:
                raise SoundDropped(filled.stdout, "no sound in the piece", from_pluto=True)
            proc = copy("none")  # silence failed too: the picture alone, as before
            if proc.stdout:
                raise SoundDropped(proc.stdout, "no sound in the piece", from_pluto=True)
            raise ValueError((filled.stderr or proc.stderr).decode(errors="replace").strip()[-200:] or "no output")
        proc = copy("copy")
        if not proc.stdout:
            reason = (proc.stderr or b"").decode(errors="replace").strip()[-200:] or "no output"
            for fallback in ("silence", "none"):  # keep the picture going
                other = copy(fallback)
                if other.stdout:
                    raise SoundDropped(other.stdout, reason)
            raise ValueError((proc.stderr or other.stderr).decode(errors="replace").strip()[-200:] or "no output")
        return proc.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


DEBUG_CAPTURES = 25  # the most pieces the debug capture keeps, so it can't fill the disk


class Relay:
    """Every Pluto channel being watched. Writes its status to status_file (pluto.json)."""

    def __init__(self, status_file: Path | None = None, log: Callable[[str], None] = print,
                 fetcher=None, remux_fn=remux, on_fallback: Callable[[str, int], None] | None = None,
                 allowed: Callable[[], set[str]] | None = None, debug_dir: Path | None = None,
                 metrics_file: Path | None = None):  # fmt: skip
        self.status_file = status_file
        # The relay's /metrics numbers (counters()), written beside pluto.json. The web half of
        # the 0.9 split serves /metrics but doesn't run the relay, so it reads these from here
        # instead of a live relay (kia.metrics). The single-container daemon reads the live relay
        # and leaves this file alone.
        self.metrics_file = metrics_file
        # A diagnostic, off unless you make the folder it points at (state/pluto-debug): then the
        # few pieces ffmpeg can't copy are saved there for a look, Pluto's own bytes only.
        self.debug_dir = debug_dir
        self.log = log
        self.fetcher = fetcher or fetch
        self.remux = remux_fn
        self.on_fallback = on_fallback
        self.allowed = allowed  # the channel ids kia's playlists have (None: any)
        self.save_lock = threading.Lock()
        self.channels: dict[str, Channel] = {}
        self.lock = threading.Lock()
        self.alerted: dict[str, float] = {}
        self.trace: Callable[[str], None] | None = None
        self.last_saved = 0.0
        # For /metrics: counts from channels no longer playing, and what was passed on.
        self.retired = {"sessions": 0, "rewinds": 0, "errors": 0, "fallbacks": 0, "silent": 0, "soundless": 0}
        self.pieces = 0
        self.bytes = 0

    def debug_save(self, cid: str, p: Piece, body: bytes, key: str | None, iv: str, exc: BaseException) -> None:
        """When the debug folder exists (you make it; it's off otherwise), save a piece ffmpeg
        couldn't copy, or copied without its sound, raw, so a real failure can be looked at later. It keeps no more than
        DEBUG_CAPTURES pieces, and writes only what came from Pluto (the piece, its key and IV),
        never any of kia's own secrets."""
        d = self.debug_dir
        if not d:
            return
        try:
            if not d.is_dir():  # not armed
                return
            with self.save_lock:
                if sum(1 for _ in d.glob("*.ts")) >= DEBUG_CAPTURES:
                    return
                stamp = time.strftime("%Y%m%d-%H%M%S")
                name = f"{cid}-{p.upstream_seq}-{stamp}-{uuid.uuid4().hex[:6]}"  # unique: retries share a second
                (d / f"{name}.ts").write_bytes(body)
                (d / f"{name}.json").write_text(json.dumps({
                    "cid": cid, "uri": p.uri, "key_uri": p.key_uri, "iv": iv, "key": key,
                    "dur": p.dur, "clip": p.clip, "error": _short(exc), "at": stamp,
                }, indent=2))  # fmt: skip
            self.log(f"Pluto {cid}: saved a piece ffmpeg couldn't copy whole to {d}/{name}.ts")
        except OSError:
            pass  # a diagnostic must never get in the way of playing

    def channel(self, cid: str, start: bool = True) -> Channel | None:
        """The channel, starting it if needed (start); None when it isn't playing and won't
        start, or too many are playing."""
        with self.lock:
            self._let_go_idle()
            ch = self.channels.get(cid)
            if ch is None:
                if not start or len(self.channels) >= MAX_CHANNELS:
                    return None
                ch = self.channels[cid] = Channel(cid, self)
            return ch

    def _let_go_idle(self) -> None:
        now = time.monotonic()
        for cid in [c for c, ch in self.channels.items() if now - ch.last_request > IDLE_AFTER]:
            for key in self.retired:
                self.retired[key] += getattr(self.channels[cid].stats, key)
            del self.channels[cid]

    def handle(self, path: str, viewer: str) -> tuple[int, str, bytes]:
        """(status, content type, body) for /pluto/<id>.m3u8 or /pluto/<id>/<n>.ts."""
        if path.split("?", 1)[0] in ("/pluto", "/pluto/"):
            # Kodi looks at the folder above a stream it opens; answer instead of a 404.
            return 200, "text/plain; charset=utf-8", b"Pluto TV channels that kia plays. See pluto.json.\n"
        m = re.fullmatch(r"/pluto/([0-9a-fA-F]{24})(?:\.m3u8|/(\d+)\.ts)", path.split("?", 1)[0])
        if not m:
            return 404, "text/plain", b"Not a Pluto channel address."
        cid = m.group(1).lower()
        if self.allowed is not None and cid not in self.allowed():
            return 404, "text/plain", b"That Pluto channel isn't in kia's playlists."
        # Only a player opening the channel's playlist starts it. A request for a piece of a
        # channel that isn't playing (an old address, or someone trying addresses) would
        # otherwise hold one of the MAX_CHANNELS places for IDLE_AFTER, and enough of them
        # would turn real viewers away.
        if m.group(2) is not None and self.channel(cid, start=False) is None:
            return 404, "text/plain", b"That piece is gone; reload the playlist."
        ch = self.channel(cid)
        if ch is None:
            return 503, "text/plain", f"kia is already playing {MAX_CHANNELS} Pluto channels.".encode()
        try:
            if m.group(2) is None:
                body = ch.playlist().encode()
                kind = "application/vnd.apple.mpegurl"
            else:
                data = ch.piece(int(m.group(2)), viewer)
                if data is None:
                    return 404, "text/plain", b"That piece is gone; reload the playlist."
                if not data:
                    return 502, "text/plain", b"Pluto didn't send that piece."
                body, kind = data, "video/mp2t"
                with self.lock:
                    self.pieces += 1
                    self.bytes += len(body)
        except Exception as exc:
            ch.stats.errors += 1
            ch.stats.problem(f"Couldn't play the channel ({_short(exc)})")
            self.save_status()
            return 502, "text/plain", f"Pluto didn't answer: {_short(exc)}".encode()
        self._check_fallback(ch)
        self.save_status()
        return 200, kind, body

    def _check_fallback(self, ch: Channel) -> None:
        if ch.fallback_since is None or not self.on_fallback:
            return
        minutes = int((time.monotonic() - ch.fallback_since) / 60)
        if time.monotonic() - ch.fallback_since > FALLBACK_ALERT and time.monotonic() - self.alerted.get(ch.cid, -1e9) > 3600:
            now = time.monotonic()
            self.alerted = {c: t for c, t in self.alerted.items() if now - t < 3600}  # older ones no longer hold one back
            self.alerted[ch.cid] = now
            threading.Thread(target=self.on_fallback, args=(ch.cid, minutes), daemon=True).start()

    def status(self) -> dict:
        with self.lock:
            self._let_go_idle()
            channels = [ch.status() for ch in self.channels.values()]
        return {
            "updated": _now(),
            "version": __version__,
            "channels": channels,
            "suspicious": any(c["suspicious"] for c in channels),
            "fallback": any(c["fallback_minutes"] is not None and c["fallback_minutes"] >= FALLBACK_ALERT / 60
                            for c in channels),
        }  # fmt: skip

    def counters(self) -> dict:
        """Totals since kia started (they only grow), and who's watching now: for /metrics."""
        with self.lock:
            self._let_go_idle()
            live = list(self.channels.values())
            totals = {key: n + sum(getattr(ch.stats, key) for ch in live) for key, n in self.retired.items()}
            statuses = [ch.status() for ch in live]
            return {**totals, "pieces": self.pieces, "bytes": self.bytes, "playing": len(live),
                    "viewers": {st["id"]: st["viewers"] for st in statuses}}  # fmt: skip

    def save_status(self, force: bool = False) -> None:
        if not self.status_file and not self.metrics_file:
            return
        with self.save_lock:  # one writer at a time
            if not force and time.monotonic() - self.last_saved < 15:
                return
            self.last_saved = time.monotonic()
            # pluto.json for the Dashboard's card, pluto-metrics.json for the web half's /metrics.
            if self.status_file:
                self._write_json(self.status_file, self.status())
            if self.metrics_file:
                self._write_json(self.metrics_file, self.counters())

    def _write_json(self, path: Path, data: dict) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")  # whole, at once, so a reader never sees half
            tmp.write_text(json.dumps(data, indent=1))
            tmp.replace(path)
        except Exception as exc:  # a status file must never stop playback
            self.log(f"Couldn't write {path.name}: {exc}")

    def keep_status_fresh(self) -> None:
        """Rewrite pluto.json every minute, so it shows channels being let go."""

        def loop():
            while True:
                time.sleep(60)
                self.save_status(force=True)

        threading.Thread(target=loop, daemon=True, name="kia-pluto-status").start()
