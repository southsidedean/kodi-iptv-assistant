"""The stream check's own run of ffprobe (probe.probe), with small scripts standing in for
ffprobe: what it prints, how it fails, and one that never answers."""

import asyncio
import json
from pathlib import Path

from kia import probe

STREAMS = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
                       {"codec_type": "audio", "codec_name": "aac"}]}  # fmt: skip


def fake_ffprobe(tmp_path: Path, body: str) -> str:
    path = tmp_path / "ffprobe"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def run(ffprobe: str, **kwargs) -> probe.ProbeResult:
    return asyncio.run(probe.probe("http://192.0.2.10/live.m3u8", ffprobe=ffprobe, **kwargs))


def test_a_stream_that_plays(tmp_path: Path):
    result = run(fake_ffprobe(tmp_path, f"echo '{json.dumps(STREAMS)}'\n"))
    assert result.ok and result.error is None
    assert (result.video_codec, result.audio_codec, result.width, result.height) == ("h264", "aac", 1280, 720)


def test_the_address_and_settings_reach_ffprobe(tmp_path: Path):
    args = tmp_path / "args"
    ffprobe = fake_ffprobe(tmp_path, f'printf "%s\\n" "$@" > {args}\necho "{{}}"\n')
    run(ffprobe, user_agent="Kodi/21.0", referrer="https://example.com/")
    seen = args.read_text().splitlines()
    assert seen[-1] == "http://192.0.2.10/live.m3u8"
    assert seen[seen.index("-user_agent") + 1] == "Kodi/21.0" and seen[seen.index("-referer") + 1] == "https://example.com/"


def test_ffprobe_runs_without_kias_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KIA_ADMIN_PASSWORD", "not-for-ffprobe")
    env = tmp_path / "env"
    run(fake_ffprobe(tmp_path, f'env > {env}\necho "{{}}"\n'))
    assert "not-for-ffprobe" not in env.read_text()


def test_a_dead_stream_says_why(tmp_path: Path):
    result = run(fake_ffprobe(tmp_path, "echo 'http://192.0.2.10/live.m3u8: Server returned 404 Not Found' >&2\nexit 1\n"))
    assert not result.ok and "404" in result.error


def test_output_that_isnt_json_counts_as_dead(tmp_path: Path):
    result = run(fake_ffprobe(tmp_path, "echo 'not json'\nexit 1\n"))
    assert not result.ok


def test_audio_only_is_dead_unless_video_isnt_required(tmp_path: Path):
    radio = {"streams": [{"codec_type": "audio", "codec_name": "mp3"}]}
    ffprobe = fake_ffprobe(tmp_path, f"echo '{json.dumps(radio)}'\n")
    assert not run(ffprobe).ok
    assert run(ffprobe, require_video=False).ok


def test_one_that_never_answers_is_stopped(tmp_path: Path):
    # The check waits twice the timeout, then kills ffprobe instead of hanging the night.
    result = run(fake_ffprobe(tmp_path, "exec sleep 30\n"), timeout=0.2)
    assert not result.ok and result.error == "timed out" and result.elapsed < 5
