"""Check whether a stream is actually playable by asking ffprobe to open it."""

from __future__ import annotations

import asyncio
import json
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


def build_command(
    url: str,
    timeout: float,
    user_agent: str | None = None,
    referrer: str | None = None,
    ffprobe: str = "ffprobe",
) -> list[str]:
    cmd = [ffprobe, "-v", "warning", "-hide_banner", "-rw_timeout", str(int(timeout * 1_000_000))]
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
) -> ProbeResult:
    cmd = build_command(url, timeout, user_agent, referrer, ffprobe)
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        # ffprobe's rw_timeout only catches a server that stops sending. This also stops one
        # that sends too slowly, or an HLS playlist with many variants to open.
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout * 2)
    except asyncio.TimeoutError:
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
