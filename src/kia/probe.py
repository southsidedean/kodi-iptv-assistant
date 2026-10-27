"""Check whether a stream is actually playable by asking ffprobe to open it."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, asdict


@dataclass
class ProbeResult:
    ok: bool
    error: str | None = None
    elapsed: float = 0.0
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


_CAUSE_RE = re.compile(r"Server returned|HTTP error|Connection|timed out|resolve|route to host", re.IGNORECASE)


def _summarize_error(stderr: str) -> str:
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    if not lines:
        return "ffprobe failed with no output"
    # Prefer the root cause (an HTTP or network error) over whatever ffmpeg logged last.
    causes = [ln for ln in lines if _CAUSE_RE.search(ln)]
    line = (causes or lines)[-1]
    # Drop URLs and "[hls @ 0x...]" prefixes so identical failures group together.
    line = re.sub(r"^\[\w+ @ 0x[0-9a-f]+\]\s*", "", line)
    line = re.sub(r"^\S+://\S+?:\s+", "", line)
    line = re.sub(r"\s*\[\S+://\S+\]$", "", line)
    return line[:200]


def interpret(payload: dict, require_video: bool = True) -> ProbeResult:
    """Turn ffprobe's -show_entries JSON into a ProbeResult."""
    streams = payload.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video" and s.get("codec_name") != "mjpeg"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    # HLS masters expose every variant; report the best one.
    best = max(video, key=lambda s: s.get("height") or 0, default=None)
    result = ProbeResult(
        ok=bool(best) or (not require_video and bool(audio)),
        video_codec=best.get("codec_name") if best else None,
        audio_codec=audio[0].get("codec_name") if audio else None,
        width=best.get("width") if best else None,
        height=best.get("height") if best else None,
    )
    if not result.ok:
        result.error = "no video stream" if audio else "no playable streams"
    return result


# What ffprobe and ffmpeg need from kia's environment when they open streams from the
# internet: none of kia's passwords and tokens go with them.
_MEDIA_ENV = ("PATH", "HOME", "TMPDIR", "TZ", "LANG", "LC_ALL", "http_proxy", "https_proxy", "no_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")  # fmt: skip


def media_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in _MEDIA_ENV}


# Through kia's outbound guard (kia.guard), what ffprobe may open: plain http, which goes to
# the guard as a proxy (http_proxy), the tcp it runs on, and what HLS needs (crypto for
# encrypted pieces, data for inline ones). Not https, tls, or httpproxy: ffmpeg's HLS reader
# opens any piece whose address starts with "http", and httpproxy://host connects to host
# itself, past the guard. https streams reach ffprobe as the guard's own http addresses
# (kia.guard.for_ffprobe), and the guard opens them. Never file:, udp:, or other ways around it.
GUARDED_PROTOCOLS = "http,tcp,crypto,data"


def guarded_env(proxy: str) -> dict[str, str]:
    """media_env() for a test through the guard at proxy. ffmpeg reads http_proxy for http
    and https alike; no_proxy goes, since a name it lists would skip the guard."""
    env = {k: v for k, v in media_env().items() if k.lower() not in ("http_proxy", "https_proxy", "no_proxy")}
    env["http_proxy"] = proxy
    return env


def build_command(
    url: str,
    timeout: float,
    user_agent: str | None = None,
    referrer: str | None = None,
    ffprobe: str = "ffprobe",
    guarded: bool = False,
) -> list[str]:
    cmd = [ffprobe, "-v", "warning", "-hide_banner", "-rw_timeout", str(int(timeout * 1_000_000))]
    if guarded:
        cmd += ["-protocol_whitelist", GUARDED_PROTOCOLS]
    if user_agent:
        cmd += ["-user_agent", user_agent]
    if referrer:
        cmd += ["-referer", referrer]
    cmd += ["-show_entries", "stream=codec_type,codec_name,width,height", "-of", "json", url]
    return cmd


async def probe(
    url: str,
    timeout: float = 20.0,
    user_agent: str | None = None,
    referrer: str | None = None,
    require_video: bool = True,
    ffprobe: str = "ffprobe",
    proxy: str | None = None,
) -> ProbeResult:
    """Test a stream with ffprobe; with proxy (a kia.guard ticket's address), through the
    outbound guard."""
    cmd = build_command(url, timeout, user_agent, referrer, ffprobe, guarded=proxy is not None)
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=media_env() if proxy is None else guarded_env(proxy),
    )
    try:
        # ffprobe's rw_timeout only catches a server that stops sending. This also stops one
        # that sends too slowly, or an HLS playlist with many variants to open.
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout * 2)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return ProbeResult(ok=False, error="timed out", elapsed=time.monotonic() - start)
    elapsed = time.monotonic() - start

    try:
        payload = json.loads(stdout or b"{}")
    except json.JSONDecodeError:
        payload = {}
    result = interpret(payload, require_video)
    result.elapsed = round(elapsed, 2)
    # ffprobe exits 0 when an HLS master loads but every variant fails, so check stderr either way.
    if not result.ok and (proc.returncode != 0 or stderr.strip()):
        result.error = _summarize_error(stderr.decode(errors="replace"))
    return result


GUARD_REFUSED = ("it led to an address on your own network (a redirect, a piece, or a name that looked up "
                 "differently), and the playlist comes from the internet, so kia's outbound guard stopped it")  # fmt: skip


async def probe_guarded(url: str, run=None, **kwargs) -> ProbeResult:
    """probe() (or run, which takes the same arguments), through kia's outbound guard
    (kia.guard), for a stream from a playlist that isn't yours. http and https only: rtmp
    and rtsp can't go through it, and keep kia.lan's check alone, as do all streams when
    you've set a proxy of your own."""
    from kia import guard

    run = run or probe
    g = guard.get() if url.split(":", 1)[0].lower() in ("http", "https") else None
    if g is None:
        return await run(url, **kwargs)
    with g.ticket() as ticket:
        result = await run(guard.for_ffprobe(url), proxy=ticket.url, **kwargs)
    if ticket.refused and not result.ok:
        return ProbeResult(ok=False, error=GUARD_REFUSED, elapsed=result.elapsed)
    return result
