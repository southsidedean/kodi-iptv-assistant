"""compose.yaml's two kia containers (0.9 privilege separation): kia serves and holds the
secrets; kia-media runs the stream tools and must see none of them."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (ROOT / "compose.yaml").read_text()


def block(name: str) -> str:
    """A service's lines in compose.yaml, up to the next service."""
    start = COMPOSE.index(f"\n  {name}:\n")
    nxt = re.search(r"\n  [a-z0-9-]+:\n", COMPOSE[start + 1:])
    return COMPOSE[start:start + 1 + nxt.start()] if nxt else COMPOSE[start:]


MEDIA = block("kia-media")

# All kia-media sees of the kia folder, each read-only: what it reads to refresh (0.9.1). Listed
# one by one, so .env, its copies, private/, tls/, and every other part's folder aren't there at
# all, and nor is anything added to the folder later.
READS = {"./kia.toml": "/data/kia.toml", "./sources.txt": "/data/sources.txt",
         '"./${KIA_SOURCES_FILE:-sources.txt}"': '"/data/${KIA_SOURCES_FILE:-sources.txt}"',
         "./playlists": "/data/playlists", "./profiles": "/data/profiles"}  # fmt: skip
WRITES = {"output", "state", "work", "vendor", "media-service"}  # never private/, tls/, or profiles/


def test_kia_media_sees_only_what_it_reads_and_writes():
    reads = dict(re.findall(r"^      - type: bind\n        source: (\S+)\n        target: (\S+)\n"
                            r"        read_only: true\n        bind:\n          create_host_path: false$", MEDIA, re.M))
    assert reads == READS
    writes = set(re.findall(r"^\s+- \./([a-z-]+):/data/\1$", MEDIA, re.M))
    assert writes == WRITES
    # Nothing else: no mount of the whole folder, of .env, or of anything not listed above.
    mounts = re.search(r"^    volumes:\n((?:      .*\n|\s*\n)+)", MEDIA, re.M).group(1)
    assert len(re.findall(r"^      - ", mounts, re.M)) == len(READS) + len(WRITES)
    assert "./:/data" not in MEDIA and "/data/.env" not in MEDIA
    assert re.search(r"^    tmpfs:\n      - /tmp\n    cap_drop:", MEDIA, re.M)


def test_the_folders_kia_media_reads_are_always_there():
    # create_host_path: false makes a missing one an error, so a fresh clone has each.
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for path in ("kia.toml", "sources.txt", "playlists/.gitkeep", "profiles/.gitkeep"):
        assert path in tracked


def test_kia_medias_settings_hold_no_secret():
    names = re.findall(r"^      ([A-Z_]+):", MEDIA, re.M)
    assert "KIA_SOURCES_FILE" in names and "KIA_ROLE" in names
    secretish = re.compile(r"TOKEN|PASSWORD|SECRET|KEY|SMTP|EMAIL|NTFY|ALERT_COMMAND|PLEX|JELLYFIN|GRAFANA|CHAT|GATEWAY|FRONTDOOR|TRUST")
    assert [n for n in names if secretish.search(n)] == []
    assert re.search(r"^      KIA_ROLE: media$", MEDIA, re.M) and re.search(r"^      KIA_ROLE: web$", block("kia"), re.M)


def test_the_two_halves_run_their_own_commands_on_their_own_networks():
    assert 'command: ["daemon-web", "--at"' in block("kia") and 'command: ["daemon-media", "--at"' in MEDIA
    assert re.search(r"^    networks: \[media\]$", MEDIA, re.M)  # not kia's network, nor the gateway's
    assert "media:\n        aliases: [web-media]" in block("web")
    assert "KIA_PROXY_HOST: web-media" in MEDIA


def test_nginx_sends_pluto_to_kia_media_and_the_rest_to_kia():
    common = (ROOT / "deploy" / "kia-common.inc").read_text()
    pluto = common[common.index("location ^~ /pluto/"):]
    pluto = pluto[:pluto.index("}")]
    assert "set $pluto http://kia-media:8782;" in pluto and "proxy_pass $pluto;" in pluto
    metrics = common[common.index("location = /metrics"):]
    assert "set $kia http://kia:8781;" in metrics[:metrics.index("}")]
    assert "set $kia http://kia:8781;" in (ROOT / "deploy" / "kia-api.inc").read_text()
    assert 'KIA_API_PORT: "8782"' in MEDIA


@pytest.mark.skipif(not shutil.which("docker"), reason="needs docker compose")
def test_compose_reads_the_two_halves_as_written():
    out = subprocess.run(["docker", "compose", "config", "--format", "json"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode:
        pytest.skip(f"docker compose config: {out.stderr.strip()[:200]}")
    services = json.loads(out.stdout)["services"]
    assert list(services["kia-media"]["networks"]) == ["media"]
    assert {"kia", "kia-media"} <= set(services["web"]["depends_on"])
    assert services["kia-media"]["image"] == services["kia"]["image"]  # one image, two commands
