"""scripts/gateway.sh: a key for each device that uses the gateway part, kept only as its
hash in the gateway's settings, and the gateway's own token for kia. docker is a stand-in."""

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")


@pytest.fixture
def kia(tmp_path: Path) -> Path:
    """A kia folder with the script and an .env, and a docker stand-in that logs what it was
    asked and what came in on stdin, then prints a setup as kia mcp-token --gateway would."""
    folder = tmp_path / "kia"
    (folder / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "gateway.sh", folder / "scripts" / "gateway.sh")
    (folder / ".env").write_text("KIA_GATEWAY_PORT=8746\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log, stdin = tmp_path / "docker.log", tmp_path / "docker.stdin"
    (bin_dir / "docker").write_text(f'#!/bin/sh\necho "$*" >> {log}\n'
                                    'case "$*" in\n'
                                    '  *" ps "*) [ -n "$FAKE_RUNNING" ] && echo 3715ea6ba64e; exit 0 ;;\n'
                                    '  *restart*) exit 0 ;;\n'
                                    'esac\n'
                                    f'cat > {stdin}\n'
                                    '[ -n "$FAKE_NO_KIA" ] && exit 1\necho "SETUP FROM KIA"\n')  # fmt: skip
    (bin_dir / "docker").chmod(0o755)
    return folder


def gateway(kia: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    full = {"PATH": f"{kia.parent / 'bin'}:/usr/bin:/bin", "HOME": str(kia.parent), **env}
    return subprocess.run(["sh", "scripts/gateway.sh", *args], cwd=kia, env=full, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def shown_key(out: str) -> str:
    keys = re.findall(r"^  ([A-Za-z0-9_-]{40,})$", out, flags=re.M)
    assert len(keys) == 1, out
    return keys[0]


def test_a_device_key_is_shown_once_and_kept_only_as_its_hash(kia: Path):
    r = gateway(kia, "add", "laptop")
    assert r.returncode == 0, r.stderr
    key = shown_key(r.stdout)
    digest = hashlib.sha256(key.encode()).hexdigest()
    config = (kia / "gateway" / "config.yaml").read_text()
    assert f"keyHash: sha256:{digest}" in config and "device: laptop" in config
    assert "x-kia-device: apiKey.device" in config  # for kia's limit on each device (kia.mcp.via_gateway)
    for name in ("config.yaml", "devices", "kia-token", "loaded"):
        path = kia / "gateway" / name
        assert key not in path.read_text(), name  # nowhere on disk
        assert path.stat().st_mode & 0o777 == 0o600, name  # only you (and kia and the gateway, as you)
    assert (kia / "gateway" / "devices").read_text() == f"laptop {digest}\n"
    # The gateway's own token for kia: made once, and not the device's key.
    token = (kia / "gateway" / "kia-token").read_text().strip()
    assert len(token) == 43 and token != key and "file: /gateway/kia-token" in config
    # kia printed the setup, with the key handed over on stdin, never on a command line.
    assert "SETUP FROM KIA" in r.stdout
    assert (kia.parent / "docker.stdin").read_text().strip() == key
    assert key not in (kia.parent / "docker.log").read_text()
    assert "compose exec -T kia python3 -m kia mcp-token --gateway --for claude-code" in (kia.parent / "docker.log").read_text()
    # A second device: its own key, and the gateway's token stays the same.
    second = shown_key(gateway(kia, "add", "phone", "--for", "curl").stdout)
    assert second != key and (kia / "gateway" / "kia-token").read_text().strip() == token
    assert gateway(kia, "list").stdout.split() == ["laptop", "phone"]


def test_remove_and_the_names_it_refuses(kia: Path):
    key = shown_key(gateway(kia, "add", "laptop").stdout)
    assert gateway(kia, "add", "laptop").returncode != 0  # one key each: remove it first
    r = gateway(kia, "remove", "laptop")
    assert r.returncode == 0 and "no longer works" in r.stdout
    config = (kia / "gateway" / "config.yaml").read_text()
    assert hashlib.sha256(key.encode()).hexdigest() not in config
    assert "keys: []" in config  # no devices: every call is refused
    assert gateway(kia, "remove", "laptop").returncode != 0
    assert "No devices" in gateway(kia, "list").stdout
    # The name goes into YAML and the log: nothing else gets in.
    for bad in ("Laptop", "-x", 'a"b', "a b", "x" * 41, "a:b"):
        r = gateway(kia, "add", bad)
        assert r.returncode != 0 and not (kia / "gateway" / "devices").read_text(), bad
    # In a UTF-8 locale, macOS's sh lets [a-z] take capitals and accented letters too.
    for bad in ("Laptop", "caf\u00e9"):
        r = gateway(kia, "add", bad, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
        assert r.returncode != 0 and not (kia / "gateway" / "devices").read_text(), bad
    assert gateway(kia, "add", "laptop", "--for", "something").returncode != 0


def test_the_limit_comes_from_env_and_is_checked(kia: Path):
    (kia / ".env").write_text("KIA_GATEWAY_RATE=30\n")
    assert gateway(kia, "write").returncode == 0
    config = (kia / "gateway" / "config.yaml").read_text()
    assert "maxTokens: 30" in config and "tokensPerFill: 30" in config and "key: apiKey.device" in config
    for bad in ("0", "abc", "10001", "5; rm -rf /", "0120", "99999999999999999999"):
        (kia / ".env").write_text(f"KIA_GATEWAY_RATE={bad}\n")
        r = gateway(kia, "write")
        assert r.returncode != 0 and "KIA_GATEWAY_RATE" in r.stderr, bad
    assert "maxTokens: 30" in (kia / "gateway" / "config.yaml").read_text()  # a refused write changes nothing


def test_without_kia_running_the_address_is_still_given(kia: Path):
    r = gateway(kia, "add", "laptop", FAKE_NO_KIA="1")
    assert r.returncode == 0 and ":8746/mcp" in r.stdout and "SETUP FROM KIA" not in r.stdout


def test_a_link_in_place_of_the_folder_is_refused(kia: Path, tmp_path: Path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (kia / "gateway").symlink_to(elsewhere)
    r = gateway(kia, "write")
    assert r.returncode != 0 and not list(elsewhere.iterdir())


def restarts(kia: Path) -> int:
    log = kia.parent / "docker.log"
    return log.read_text().count("compose restart gateway") if log.exists() else 0


def test_a_running_gateway_restarts_when_its_settings_or_certificate_change(kia: Path):
    # With Docker Desktop, the gateway misses every change after the first, so gateway.sh
    # restarts it; nothing changed, no restart.
    (kia / "tls").mkdir()
    (kia / "tls" / "fullchain.pem").write_text("CERTIFICATE 1\n")
    r = gateway(kia, "add", "laptop", FAKE_RUNNING="1")
    assert r.returncode == 0 and "Restarted the gateway" in r.stdout and restarts(kia) == 1
    assert gateway(kia, "write", FAKE_RUNNING="1").returncode == 0 and restarts(kia) == 1
    assert gateway(kia, "refresh", FAKE_RUNNING="1").stdout == "" and restarts(kia) == 1
    gateway(kia, "remove", "laptop", FAKE_RUNNING="1")
    assert restarts(kia) == 2  # a removed key stops working at once
    # kia renews its certificate by renaming a new one into place.
    (kia / "tls" / "new.pem").write_text("CERTIFICATE 2\n")
    (kia / "tls" / "new.pem").replace(kia / "tls" / "fullchain.pem")
    assert gateway(kia, "refresh", FAKE_RUNNING="1").returncode == 0 and restarts(kia) == 3
    assert gateway(kia, "refresh", FAKE_RUNNING="1").returncode == 0 and restarts(kia) == 3


def test_a_gateway_that_isnt_running_isnt_started(kia: Path):
    assert gateway(kia, "add", "laptop").returncode == 0 and restarts(kia) == 0
    # It reads the new settings when it starts, so there's nothing to restart for later.
    assert gateway(kia, "refresh", FAKE_RUNNING="1").returncode == 0 and restarts(kia) == 0


def test_refresh_never_opens_the_certificate(kia: Path, tmp_path: Path):
    # kia's container can write tls/, and the kia-stack service runs refresh: a pipe there
    # would hang anything that read it.
    gateway(kia, "write")
    (kia / "tls").mkdir()
    os.mkfifo(kia / "tls" / "fullchain.pem")
    r = subprocess.run(["sh", "scripts/gateway.sh", "refresh"], cwd=kia, capture_output=True, text=True, timeout=10,
                       env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path), "FAKE_RUNNING": "1"})  # fmt: skip
    assert r.returncode == 0 and restarts(kia) == 1
