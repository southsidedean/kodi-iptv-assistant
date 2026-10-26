"""Have the images compose.yaml pins to a version had a newer release? (CI runs this weekly.)

Most of kia's stack follows each image's newest release, but a few are pinned to the version
the docs were tested with (Threadfin, the graphics card's exporter, the gateway), and nothing
else says when those have a newer one. This reads the pinned defaults from compose.yaml, the
${KIA_..._IMAGE:-name:1.2.3} kind, asks Docker Hub or GitHub's registry (ghcr.io) for each
image's tags, and fails when a newer tag of the same shape is out: 1.2.38 after 1.2.37,
v1.7.0 after v1.6.0, or 4.5.2-4.8.1-ubuntu22.04 after 4.3.1-4.4.1-ubuntu22.04, never an image
built a different way (-nvidia, -distroless) or a test release (v1.7.0-alpha.1).

    python3 scripts/pinned-images.py [compose.yaml]
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

PINNED = re.compile(r"image:\s*\$\{(KIA_[A-Z0-9_]+):-([^}\s]+)\}")


def is_version(tag: str) -> bool:
    """A tag that names a version: 1.2.37, or v1.6.0. Not latest, alpine, or a branch."""
    return bool(re.match(r"v?\d", tag))


def pinned(compose: str) -> dict[str, str]:
    """{setting: image} for every default image that names a version: latest, alpine, and the
    like follow their newest release already."""
    found = {}
    for setting, image in PINNED.findall(compose):
        name, _, tag = image.rpartition(":")
        if name and "/" not in tag and is_version(tag):
            found[setting] = image
    return found


def shape(tag: str) -> str:
    """A tag with its numbers taken out: 1.2.37 and 1.2.38 have the same shape, 1.2.37-nvidia doesn't."""
    return re.sub(r"\d+", "N", tag)


def numbers(tag: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", tag))


def newer(tag: str, tags: list[str]) -> list[str]:
    """The tags of tag's shape with higher numbers, newest first."""
    return sorted((t for t in tags if shape(t) == shape(tag) and numbers(t) > numbers(tag)), key=numbers, reverse=True)


def docker_hub_tags(name: str, pages: int = 5) -> list[str]:
    """An image's tags on Docker Hub, most recently updated first (the first 500)."""
    namespace, _, repo = name.rpartition("/")
    url = (f"https://hub.docker.com/v2/namespaces/{namespace or 'library'}/repositories/{repo}/tags"
           "?page_size=100&ordering=last_updated")  # fmt: skip
    tags: list[str] = []
    for _ in range(pages):
        with urllib.request.urlopen(url, timeout=30) as r:
            page = json.load(r)
        tags += [t["name"] for t in page.get("results", [])]
        url = page.get("next")
        if not url:
            break
    return tags


def ghcr_tags(name: str) -> list[str]:
    """An image's tags on GitHub's registry, ghcr.io/owner/repo (public images need no
    account, only a token anyone can get)."""
    repo = name.removeprefix("ghcr.io/")
    with urllib.request.urlopen(f"https://ghcr.io/token?scope=repository:{repo}:pull", timeout=30) as r:
        token = json.load(r)["token"]
    req = urllib.request.Request(f"https://ghcr.io/v2/{repo}/tags/list?n=5000", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return list(json.load(r).get("tags") or [])


def registry_tags(name: str) -> list[str]:
    return ghcr_tags(name) if name.startswith("ghcr.io/") else docker_hub_tags(name)


def main(compose_path: str = "compose.yaml", tags_of=registry_tags) -> int:
    images = pinned(Path(compose_path).read_text())
    if not images:
        print("No pinned images in", compose_path)
        return 1
    behind = 0
    for setting, image in sorted(images.items()):
        name, _, tag = image.rpartition(":")
        registry = name.split("/")[0] if "." in name.split("/")[0] else ""
        if registry not in ("", "ghcr.io"):  # a registry other than Docker Hub and ghcr.io (quay.io)
            print(f"{setting}: {image} is on another registry; not checked")
            continue
        tags = tags_of(name)
        later = newer(tag, tags)
        if later:
            behind += 1
            newest = next((t for t in tags if is_version(t)), "?")  # not latest, not a branch
            print(f"{setting}: {image} has a newer release: {name}:{later[0]} (newest tag of any kind: {newest})")
        else:
            print(f"{setting}: {image} is the newest of its kind")
    return 1 if behind else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
