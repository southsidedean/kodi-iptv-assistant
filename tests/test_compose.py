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

# Folders in the kia folder that hold a password, token, or key, or another program's data:
# kia-media sees each as an empty folder. A new part with secrets of its own goes here, and in
# compose.yaml's list.
SECRET_FOLDERS = {"private", "tls", "gateway", "frontdoor", "lldap", "threadfin", "threadfin-2", "plex",
                  "jellyfin", "monitoring"}  # fmt: skip


def test_kia_media_sees_none_of_the_folders_that_hold_secrets():
    hidden = set(re.findall(r"^\s+- /data/([a-z0-9-]+):size=1m,mode=0500$", MEDIA, re.M))
    assert hidden == SECRET_FOLDERS
    assert "- ./deploy/empty.env:/data/.env:ro" in MEDIA and (ROOT / "deploy" / "empty.env").read_text() == ""


def test_every_folder_in_the_repository_with_secrets_is_hidden():
    # The parts' own folders in the kia folder, from compose.yaml's mounts of them: each is
    # either hidden from kia-media, or known to hold nothing secret.
    not_secret = {"output", "state", "work", "vendor", "profiles", "media-service", "media", "playlists", "deploy",
                  "stack-service", "stacks", "docs", "scripts", "src", "tests", "tools", "images", ".github"}  # fmt: skip
    folders = {p.name for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")} - {"plans", "__pycache__"}
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    in_repo = {f for f in folders if any(t.startswith(f + "/") for t in tracked)}
    assert in_repo - not_secret - SECRET_FOLDERS == set()


def test_kia_media_writes_only_its_own_folders():
    writable = set(re.findall(r"^\s+- \./([a-z-]+):/data/\1$", MEDIA, re.M))
    assert writable == {"output", "state", "work", "vendor", "media-service"}  # never private/, tls/, or profiles/
    assert "- ./:/data:ro" in MEDIA


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
