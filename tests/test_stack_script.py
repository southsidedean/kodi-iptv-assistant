"""scripts/stack.sh: KIA_STACK in .env becomes Compose profiles, checked for this computer,
and apply starts what's chosen and stops what isn't. Docker, uname, and nc are stand-ins."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")


@pytest.fixture
def kia(tmp_path: Path) -> Path:
    """A kia folder with the script, the presets, an .env, and stand-ins for docker, uname, and nc:
    Linux, nothing on port 9100, Docker without NVIDIA, kia and jellyfin running."""
    folder = tmp_path / "kia"
    (folder / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "stack.sh", folder / "scripts" / "stack.sh")
    shutil.copytree(ROOT / "stacks", folder / "stacks")
    (folder / "media").mkdir()
    (folder / "output").mkdir()
    for name in ("plex", "plex-2"):
        (folder / "output" / f"{name}.m3u").write_text("#EXTM3U\n")  # the playlists Threadfin reads
    (folder / ".env").write_text("KIA_PUBLIC_URL=http://192.168.1.20:8780\nKIA_PORT=1\nCOMPOSE_PROFILES=\n"
                                 "KIA_PLEX_ADVERTISE=http://192.168.1.20:32400/\nKIA_JELLYFIN_ADVERTISE=\n")  # fmt: skip
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    stub(bin_dir, "docker", f'echo "COMPOSE_PROFILES=$COMPOSE_PROFILES $*" >> {log}\n'
         'case "$*" in\n'
         '  info*) [ -n "$FAKE_NVIDIA" ] && echo " Runtimes: io.containerd.runc.v2 nvidia runc" ;;\n'
         '  *"ps --services --status running"*) printf "kia\\nweb\\njellyfin\\n" ;;\n'
         '  *"ps --all --services"*) printf "kia\\nweb\\njellyfin\\nthreadfin-2\\n" ;;\n'
         '  *"config --services"*) printf "kia\\nweb\\nthreadfin\\nplex\\n" ;;\n'
         'esac\n')  # fmt: skip
    stub(bin_dir, "uname", 'echo "${FAKE_OS:-Linux}"\n')
    stub(bin_dir, "nc", 'exit 1\n')  # nothing listens anywhere
    return folder


def stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def stack(kia: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    full = {"PATH": f"{kia.parent / 'bin'}:/usr/bin:/bin", "HOME": str(kia.parent), **env}
    return subprocess.run(["sh", "scripts/stack.sh", *args], cwd=kia, env=full, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def with_stack(kia: Path, value: str) -> None:
    (kia / ".env").write_text((kia / ".env").read_text() + f"KIA_STACK={value}\n")


def test_parts_become_profiles(kia: Path):
    with_stack(kia, "plex,observability, plex-tuner-2")
    r = stack(kia, "check")
    assert r.returncode == 0, r.stdout
    assert "COMPOSE_PROFILES=plex,plex-server,monitoring,plex-2,monitoring-node" in r.stdout  # node-exporter: nothing on 9100
    assert "add observability-disks" in r.stdout and "NVIDIA card" not in r.stdout  # hints for this computer


def test_mistakes_are_problems(kia: Path):
    with_stack(kia, "plex,plex-tuner,observability-gpu,plexx")
    r = stack(kia, "check")
    assert r.returncode == 1
    for said in ("plexx, which isn't a part", "plex already brings its tuner", "needs an NVIDIA card",
                 "go with observability"):  # fmt: skip
        assert said in r.stdout, said
    assert "problem(s)" in r.stdout


def test_a_mac_gets_the_mac_rules(kia: Path):
    with_stack(kia, "jellyfin,observability,observability-disks")
    r = stack(kia, "check", FAKE_OS="Darwin")
    assert r.returncode == 1
    assert "for Linux; on a Mac" in r.stdout
    assert "brew install node_exporter" in r.stdout  # nothing answers on 9100
    assert "KIA_JELLYFIN_CONFIG keeps its database in a folder" in r.stdout
    assert "monitoring-node" not in r.stdout  # a Mac's node-exporter comes from Homebrew


def test_nvidia_is_offered_and_allowed(kia: Path):
    with_stack(kia, "observability")
    assert "this computer has an NVIDIA card" in stack(kia, "check", FAKE_NVIDIA="1").stdout
    with_stack(kia, "observability,observability-gpu")
    assert stack(kia, "check", FAKE_NVIDIA="1").returncode == 0


def test_apply_sets_the_profiles_and_stops_what_is_no_longer_chosen(kia: Path):
    r = stack(kia, "apply", "--preset", "plex-box", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    env = (kia / ".env").read_text()
    assert "KIA_STACK=plex\n" in env and "COMPOSE_PROFILES=plex,plex-server\n" in env
    assert "KIA_PLEX_URL=http://plex:32400\n" in env  # Plex in the stack, as kia reaches it
    calls = (kia.parent / "docker.log").read_text().splitlines()
    assert any(c.endswith(" compose up -d --quiet-pull") for c in calls)
    assert any("rm --stop --force jellyfin" in c and c.startswith("COMPOSE_PROFILES=* ") for c in calls)  # not chosen any more
    assert any("rm --stop --force threadfin-2" in c for c in calls)  # stopped or stuck restarting, it goes too
    assert not any("rm --stop --force kia" in c for c in calls)
    assert list((kia / "state").glob("env.before-stack-*"))  # the old .env, kept
    assert "Next for Plex" in r.stdout


def test_apply_refuses_a_stack_with_problems(kia: Path):
    with_stack(kia, "observability-gpu")
    r = stack(kia, "apply", "--yes")
    assert r.returncode == 1
    assert not (kia.parent / "docker.log").exists() or "up -d" not in (kia.parent / "docker.log").read_text()


def test_without_kia_stack_it_says_what_compose_profiles_does(kia: Path):
    r = stack(kia, "check")
    assert r.returncode == 1 and "KIA_STACK isn't in .env" in r.stdout
    assert stack(kia, "list").returncode == 0
    assert stack(kia, "apply", "--preset", "nope").returncode == 2


def test_apply_asks_before_turning_off_what_runs_now(kia: Path):
    env = (kia / ".env").read_text().replace("COMPOSE_PROFILES=\n", "COMPOSE_PROFILES=plex,monitoring\n")
    (kia / ".env").write_text(env + "KIA_STACK=jellyfin\n")
    r = stack(kia, "apply")  # no terminal to answer: nothing changes
    assert "These Compose profiles stop, as KIA_STACK leaves them out: plex, monitoring" in r.stdout
    assert "Not changed" in r.stdout and "COMPOSE_PROFILES=plex,monitoring\n" in (kia / ".env").read_text()
    assert stack(kia, "apply", "--yes").returncode == 0
    assert "COMPOSE_PROFILES=jellyfin-server\n" in (kia / ".env").read_text()


def test_threadfin_needs_its_playlist_first(kia: Path):
    (kia / "output" / "plex.m3u").unlink()
    with_stack(kia, "plex")
    r = stack(kia, "check")
    assert r.returncode == 1 and "no playlist named plex for Threadfin yet" in r.stdout
    assert "plex-box.md, step 2" in r.stdout  # Plex in the stack: the cookbook
    (kia / "output" / "plex-1.m3u").write_text("#EXTM3U\n")  # the split names work too
    with_stack(kia, "plex-tuner,plex-tuner-2")
    (kia / "output" / "plex-2.m3u").unlink()
    r = stack(kia, "check")
    assert "no playlist named plex-2 for the second Threadfin" in r.stdout and "named plex for" not in r.stdout
    (kia / "output" / "plex-1.m3u").unlink()
    assert "docs/plex.md, step 1" in stack(kia, "check").stdout  # a Plex elsewhere: the Plex guide


def test_an_empty_kia_stack_leaves_compose_profiles_set_by_hand_alone(kia: Path):
    env = (kia / ".env").read_text().replace("COMPOSE_PROFILES=\n", "COMPOSE_PROFILES=plex\n")
    (kia / ".env").write_text(env + "KIA_STACK=\n")  # as .env.example has it, then docs/plex.md by hand before 0.5.6
    r = stack(kia, "check")
    assert r.returncode == 1 and "KIA_STACK is empty, so COMPOSE_PROFILES decides what runs: plex" in r.stdout
    assert stack(kia, "apply", "--yes").returncode == 1
    assert "COMPOSE_PROFILES=plex\n" in (kia / ".env").read_text()
    assert not (kia.parent / "docker.log").exists() or "up -d" not in (kia.parent / "docker.log").read_text()
    assert stack(kia, "apply", "--preset", "plex-elsewhere", "--yes").returncode == 0  # a preset is a choice


def test_apply_starts_its_profiles_even_with_others_in_the_shell(kia: Path):
    r = stack(kia, "apply", "--preset", "plex-box", "--yes", COMPOSE_PROFILES="monitoring")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = (kia.parent / "docker.log").read_text().splitlines()
    assert "COMPOSE_PROFILES=plex,plex-server compose up -d --quiet-pull" in calls
