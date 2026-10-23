"""tools/mock-media/make_library.py: a pretend library for testing and screenshots."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "mock-media" / "make_library.py"
spec = importlib.util.spec_from_file_location("make_library", TOOL)
lib = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lib  # dataclasses look their module up here
spec.loader.exec_module(lib)


def test_the_library_is_laid_out_the_way_plex_expects():
    items = lib.plan()
    paths = [i.path for i in items]
    assert len(paths) == len(set(paths))  # nothing overwrites anything
    movies = [p for p in paths if p.startswith("movies/")]
    assert len(movies) == 40
    assert all(re.fullmatch(r"movies/(.+) \((\d{4})\)/\1 \(\2\)\.mp4", p) for p in movies)
    assert "movies/Agent 327 - Operation Barbershop (2017)/Agent 327 - Operation Barbershop (2017).mp4" in movies
    episodes = [p for p in paths if p.startswith("tv/")]
    assert len({p.split("/")[1] for p in episodes}) == 6
    assert all(re.fullmatch(r"tv/(.+ \(\d{4}\))/Season (\d\d)/\1 - s\2e\d\d\.mp4", p) for p in episodes)
    songs = [i for i in items if i.path.startswith("music/")]
    assert len({i.tags["artist"] for i in songs}) == 15
    assert all(re.fullmatch(r"music/[^/]+/[^/]+/\d\d - [^/]+\.m4a", i.path) for i in songs)
    assert not any(c in p for p in paths for c in '<>:"\\|?*')


def test_list_prints_the_plan(capsys):
    assert lib.main(["--list"]) == 0
    assert capsys.readouterr().out.splitlines() == [i.path for i in lib.plan()]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg")
def test_a_placeholder_has_aac_sound_and_its_tags(tmp_path: Path):
    song = next(i for i in lib.plan() if i.path.startswith("music/"))
    movie = lib.plan()[0]
    for item in (movie, song):
        lib.make(item, tmp_path, "ffmpeg")
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                       str(tmp_path / song.path)], capture_output=True, check=True).stdout)  # fmt: skip
    assert [s["codec_name"] for s in probe["streams"]] == ["aac"]
    assert probe["format"]["tags"]["artist"] == song.tags["artist"]
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(tmp_path / movie.path)],
                                      capture_output=True, check=True).stdout)  # fmt: skip
    assert sorted(s["codec_name"] for s in probe["streams"]) == ["aac", "h264"]
    assert not list(tmp_path.rglob("*.part.*"))
