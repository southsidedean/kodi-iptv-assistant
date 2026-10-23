#!/usr/bin/env python3
"""Make a pretend media library, laid out the way Plex and Jellyfin expect, for testing
and for the docs' screenshots: about 40 movies, 6 TV series, and 15 music artists.

Every file is a 10-second placeholder (a test pattern and a tone, AAC sound), so the whole
library is small. Plex and Jellyfin match movies and shows by their folder and file names,
so the library looks like a real one, with posters and summaries. Only public-domain films,
the Blender Foundation's open movies, public-domain TV episodes, and public-domain or
Creative Commons music are named.

    python3 tools/mock-media/make_library.py /srv/media          # makes movies/, tv/, music/
    python3 tools/mock-media/make_library.py --list               # what it would make

Needs ffmpeg. Files that already exist are left alone, so it can be run again.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Public-domain films (in the US) and the Blender Foundation's open movies.
MOVIES = [
    ("A Trip to the Moon", 1902), ("The Great Train Robbery", 1903), ("The Cabinet of Dr. Caligari", 1920),
    ("The Kid", 1921), ("Nosferatu", 1922), ("Safety Last!", 1923), ("Sherlock Jr.", 1924),
    ("The Phantom of the Opera", 1925), ("The General", 1926), ("Metropolis", 1927), ("Steamboat Bill, Jr.", 1928),
    ("His Girl Friday", 1940), ("Meet John Doe", 1941), ("Penny Serenade", 1941),
    ("Detour", 1945), ("Scarlet Street", 1945), ("The Stranger", 1946), ("D.O.A.", 1949), ("Royal Wedding", 1951),
    ("Father's Little Dividend", 1951), ("Kansas City Confidential", 1952), ("The Hitch-Hiker", 1953),
    ("Suddenly", 1954), ("Plan 9 from Outer Space", 1957), ("House on Haunted Hill", 1959),
    ("The Little Shop of Horrors", 1960), ("Carnival of Souls", 1962), ("The Brain That Wouldn't Die", 1962),
    ("Charade", 1963), ("Dementia 13", 1963), ("McLintock!", 1963), ("The Last Man on Earth", 1964), ("Night of the Living Dead", 1968),
    ("Elephants Dream", 2006), ("Big Buck Bunny", 2008), ("Sintel", 2010), ("Tears of Steel", 2012),
    ("Cosmos Laundromat", 2015), ("Agent 327: Operation Barbershop", 2017), ("Spring", 2019),
]  # fmt: skip

# Series with public-domain episodes: (title, year it started, episodes per season).
SHOWS = [
    ("The Cisco Kid", 1950, [8]),
    ("Dragnet", 1951, [8, 6]),
    ("Sherlock Holmes", 1954, [10]),
    ("One Step Beyond", 1959, [8, 8]),
    ("Bonanza", 1959, [6]),
    ("The Beverly Hillbillies", 1962, [8]),
]

# Public-domain and Creative Commons music: artist -> [(album, year, [tracks])].
MUSIC = {
    "Scott Joplin": [("Piano Rags", 1902, ["Maple Leaf Rag", "The Entertainer", "Elite Syncopations",
                                           "The Easy Winners", "Solace"])],
    "Kevin MacLeod": [("Royalty Free", 2010, ["Carefree", "Sneaky Snitch", "Wallpaper", "Monkeys Spinning Monkeys"])],
    "Chris Zabriskie": [("Divider", 2014, ["Divider", "Cylinder Five", "Air Hadley"])],
    "Broke For Free": [("Layers", 2012, ["Night Owl", "Something Elated", "Day Bird"])],
    "Kai Engel": [("Satin", 2017, ["Moonlight Reprise", "Snowfall", "Brand New World"])],
    "Lee Rosevere": [("Music for Podcasts", 2016, ["Let's Start at the Beginning", "Thinking It Over", "Curiosity"])],
    "Podington Bear": [("Solo Instruments", 2012, ["Starling", "Lucky Day", "Moonlit"])],
    "Blue Dot Sessions": [("Aroma", 2016, ["Pintle", "Lahaina", "Cloud Line"])],
    "Josh Woodward": [("Ashes", 2012, ["Swansong", "The Wake", "Afterglow"])],
    "Komiku": [("It's Time for Adventure", 2018, ["Bicycle", "The Road We Use to Travel", "Champ de tournesol"])],
    "Jahzzar": [("Travellers Guide", 2012, ["Siesta", "Railroad", "Comedie"])],
    "Ketsa": [("Raising Frequency", 2019, ["Firefly", "Seeing You", "Rain Drops"])],
    "Tours": [("Enthusiast", 2012, ["Enthusiast", "Washington", "Synthwave"])],
    "Monplaisir": [("Heat of the Summer", 2018, ["Rainbow", "Summer Breeze", "Sunrise"])],
    "The Freak Fandango Orchestra": [("Tales of a Dead Fish", 2010, ["Requiem for a Fish", "Clandestino", "Stars"])],
}  # fmt: skip


@dataclass
class Item:
    path: str  # relative to the library folder
    kind: str  # video or audio
    tags: dict[str, str] = field(default_factory=dict)


def safe(name: str) -> str:
    """A title as a file or folder name: no characters Windows or NFS shares refuse."""
    name = name.replace(": ", " - ")
    return "".join("-" if c in '<>:"/\\|?*' else c for c in name).strip(". ")


def plan() -> list[Item]:
    """Every file, with Plex's names: movies/Title (Year)/Title (Year).mp4,
    tv/Show (Year)/Season 01/Show (Year) - s01e01.mp4, music/Artist/Album/01 - Track.m4a."""
    items = []
    for title, year in MOVIES:
        name = f"{safe(title)} ({year})"
        items.append(Item(f"movies/{name}/{name}.mp4", "video", {"title": title, "year": str(year)}))
    for title, year, seasons in SHOWS:
        show = f"{safe(title)} ({year})"
        for s, episodes in enumerate(seasons, 1):
            for e in range(1, episodes + 1):
                items.append(Item(f"tv/{show}/Season {s:02d}/{show} - s{s:02d}e{e:02d}.mp4", "video", {"show": title}))
    for artist, albums in MUSIC.items():
        for album, year, tracks in albums:
            for n, track in enumerate(tracks, 1):
                items.append(Item(f"music/{safe(artist)}/{safe(album)}/{n:02d} - {safe(track)}.m4a", "audio", {
                    "artist": artist, "album_artist": artist, "album": album, "title": track,
                    "track": f"{n}/{len(tracks)}", "date": str(year)}))  # fmt: skip
    return items


def make(item: Item, out: Path, ffmpeg: str) -> None:
    path = out / item.path
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = [arg for k, v in item.tags.items() for arg in ("-metadata", f"{k}={v}")]
    if item.kind == "video":
        inputs = ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
        codecs = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k"]
    else:
        inputs = ["-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100"]
        codecs = ["-c:a", "aac", "-b:a", "96k"]
    tmp = path.with_name(path.stem + ".part" + path.suffix)
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *inputs, "-t", "10", *codecs, *meta,
                    "-shortest", str(tmp)], check=True)  # fmt: skip
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", nargs="?", type=Path, help="where to make movies/, tv/, and music/")
    parser.add_argument("--list", action="store_true", help="only list the files")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args(argv)
    items = plan()
    if args.list:
        print("\n".join(i.path for i in items))
        return 0
    if not args.folder:
        parser.error("say where to make the library, or use --list")
    if not shutil.which(args.ffmpeg):
        print("make_library: ffmpeg isn't installed (sudo apt install ffmpeg, or brew install ffmpeg)", file=sys.stderr)
        return 1
    made = 0
    for item in items:
        if not (args.folder / item.path).exists():
            make(item, args.folder, args.ffmpeg)
            made += 1
    counts = {k: sum(i.path.startswith(k) for i in items) for k in ("movies/", "tv/", "music/")}
    print(f"{args.folder}: {counts['movies/']} movies, {counts['tv/']} TV episodes, {counts['music/']} songs "
          f"({made} made now)")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
