"""scripts/install.sh, run for real in a kia folder: it makes .env, sets the preset, and starts
what the preset needs. Docker, uname, and nc are stand-ins; there's no terminal to answer."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")


@pytest.fixture
def kia(tmp_path: Path) -> Path:
    """A copy of kia (scripts, presets, .env.example), with Docker answering and kia's
    password already made, so the installer doesn't wait for it."""
    folder = tmp_path / "kia"
    (folder / "scripts").mkdir(parents=True)
    for name in ("install.sh", "stack.sh"):
        shutil.copy(ROOT / "scripts" / name, folder / "scripts" / name)
    shutil.copytree(ROOT / "stacks", folder / "stacks")
    shutil.copy(ROOT / ".env.example", folder / ".env.example")
    (folder / "compose.yaml").write_text("")
    (folder / "src" / "kia").mkdir(parents=True)
    (folder / "src" / "kia" / "cli.py").write_text("")
    (folder / ".git").mkdir()  # a copy of kia: nothing to download
    (folder / "media").mkdir()
    (folder / "state").mkdir()
    (folder / "state" / "admin-password.txt").write_text("not-a-real-password\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    stub(bin_dir, "docker", f'echo "COMPOSE_PROFILES=$COMPOSE_PROFILES $*" >> {log}\n'
         'case "$*" in info*) [ -z "$FAKE_DOCKER_DOWN" ] || exit 1 ;; esac\n')  # fmt: skip
    stub(bin_dir, "uname", 'echo "${FAKE_OS:-Linux}"\n')
    stub(bin_dir, "nc", "exit 1\n")
    return folder


def stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def install(kia: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    full = {"PATH": f"{kia.parent / 'bin'}:/usr/bin:/bin", "HOME": str(kia.parent), **env}
    # A new session: no terminal at all, as with ssh without -t or cron.
    return subprocess.run(["sh", "scripts/install.sh", "--tz", "Etc/UTC", "--port", "1", *args], cwd=kia, env=full,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, start_new_session=True,
                          check=False)  # fmt: skip


def calls(kia: Path) -> list[str]:
    log = kia.parent / "docker.log"
    return log.read_text().splitlines() if log.exists() else []


def env(kia: Path) -> str:
    return (kia / ".env").read_text()


def test_without_a_terminal_it_takes_the_defaults(kia: Path):
    r = install(kia, "--address", "192.168.1.20")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KIA_STACK=\n" in env(kia) and "KIA_PUBLIC_URL=http://192.168.1.20:1\n" in env(kia)  # preset kodi
    assert "COMPOSE_PROFILES= compose up -d --build" in calls(kia)
    assert not (kia / ".env.new").exists()
    # A password kia made before isn't printed again, only where it is.
    assert "not-a-real-password" not in r.stdout and "state/admin-password.txt" in r.stdout


def test_a_plex_preset_starts_kia_and_leaves_the_rest_for_after_the_playlist(kia: Path):
    r = install(kia, "--address", "192.168.1.20", "--preset", "plex-elsewhere", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KIA_STACK=plex-tuner\n" in env(kia) and "COMPOSE_PROFILES=\n" in env(kia)
    assert "COMPOSE_PROFILES= compose up -d --build" in calls(kia)
    assert not any("--quiet-pull" in c for c in calls(kia))  # stack.sh apply waits for the playlist
    assert "make the playlist named plex" in r.stdout and "docs/plex.md, step 1" in r.stdout
    assert "plex.tv/claim" not in r.stdout  # no Plex of its own to claim


def test_plex_in_the_stack_says_to_make_the_playlist_before_the_claim_code(kia: Path):
    r = install(kia, "--address", "192.168.1.20", "--preset", "plex-box", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.index("make the playlist named plex") < r.stdout.index("plex.tv/claim")
    assert "KIA_PLEX_ADVERTISE=http://192.168.1.20:32400/\n" in env(kia)


def test_other_presets_start_once_with_their_parts(kia: Path):
    r = install(kia, "--address", "192.168.1.20", "--preset", "jellyfin-box", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls(kia).index("COMPOSE_PROFILES= compose build") < calls(kia).index(
        "COMPOSE_PROFILES=jellyfin-server compose up -d --quiet-pull")  # fmt: skip
    assert "KIA_JELLYFIN_ADVERTISE=http://192.168.1.20:8096\n" in env(kia)


def test_the_address_is_cleaned_up_or_refused(kia: Path):
    assert install(kia, "--address", "http://192.168.1.20:8780/", "--yes").returncode == 0
    assert "KIA_PUBLIC_URL=http://192.168.1.20:1\n" in env(kia)
    (kia / ".env").unlink()
    r = install(kia, "--address", "a|b", "--yes")
    assert r.returncode == 1 and "characters an address can't have" in r.stderr
    assert not (kia / ".env").exists()  # nothing half-made to keep on the next run


def test_stops_before_anything_when_docker_isnt_running_or_an_option_has_no_value(kia: Path):
    r = install(kia, "--address", "192.168.1.20", "--yes", FAKE_DOCKER_DOWN="1")
    assert r.returncode == 1 and "Docker isn't answering" in r.stderr
    r = install(kia, "--address", "192.168.1.20", "--yes", FAKE_DOCKER_DOWN="1", FAKE_OS="Darwin")
    assert "Start Docker Desktop" in r.stderr
    assert not (kia / ".env").exists()
    r = install(kia, "--yes", "--dir")
    assert r.returncode == 1 and "--dir needs a value" in r.stderr
    r = install(kia, "--yes", "--preset", "nope")
    assert r.returncode == 1 and "there's no preset nope" in r.stderr and not (kia / ".env").exists()


def test_the_kia_stack_service_is_offered_and_installed_by_default(kia: Path):
    # It's how the Dashboard's Stack page changes what runs, so the installer asks, and with
    # nobody to answer (as here), says yes. A stand-in records the call: the real one would
    # set up a service on this computer.
    called = kia.parent / "install-service.log"
    stub(kia / "scripts", "install-service.sh", f'echo "$*" >> {called}\n')
    r = install(kia)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "kia-stack service lets you choose" in r.stdout
    assert called.read_text().split() == ["--stack"]


def test_no_stack_service_skips_it(kia: Path):
    called = kia.parent / "install-service.log"
    stub(kia / "scripts", "install-service.sh", f'echo "$*" >> {called}\n')
    r = install(kia, "--no-stack-service")
    assert r.returncode == 0 and not called.exists() and "kia-stack service lets you choose" not in r.stdout
