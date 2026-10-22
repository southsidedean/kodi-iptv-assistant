"""scripts/update.sh: moves kia to its newest release (not a test release, not main),
refuses when kia's own files were changed, and asks before restarting while someone watches."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("git") or not shutil.which("sh"), reason="needs git and sh")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()  # fmt: skip


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    """A kia copy cloned from an origin with releases v0.1.0 and v0.2.0, a test release
    v0.3.0-rc1, and one more commit on main; the copy is on v0.1.0."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "update.sh", origin / "scripts" / "update.sh")
    for n, tag in enumerate(["v0.1.0", "v0.2.0", "v0.3.0-rc1", None]):
        (origin / "compose.yaml").write_text(f"# {n}\n")
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", f"commit {n}")
        if tag:
            git(origin, "tag", tag)
    kia = tmp_path / "kia"
    git(tmp_path, "clone", "-q", str(origin), str(kia))
    git(kia, "-c", "advice.detachedHead=false", "checkout", "-q", "v0.1.0")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(f'#!/bin/sh\necho "$*" >> {tmp_path / "docker.log"}\n')
    (bin_dir / "docker").chmod(0o755)
    (kia / ".env").write_text("KIA_PORT=1\n")  # nothing answers there: nobody's watching
    return kia


def update(kia: Path, *args: str) -> subprocess.CompletedProcess:
    env = {"PATH": f"{kia.parent / 'bin'}:/usr/bin:/bin", "HOME": str(kia.parent)}
    return subprocess.run(["sh", "scripts/update.sh", *args], cwd=kia, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_moves_to_the_newest_release_and_restarts(copy: Path):
    r = update(copy)
    assert r.returncode == 0, r.stderr
    assert git(copy, "describe", "--tags") == "v0.2.0"  # not the test release, not main
    assert (copy.parent / "docker.log").read_text().splitlines() == [
        "compose build --pull", "compose pull --ignore-buildable", "compose up -d"]  # fmt: skip


def test_a_given_release_main_and_files_only(copy: Path):
    assert update(copy, "--no-restart").returncode == 0
    assert git(copy, "describe", "--tags") == "v0.2.0" and not (copy.parent / "docker.log").exists()
    assert update(copy, "v0.1.0", "--no-restart").returncode == 0 and git(copy, "describe", "--tags") == "v0.1.0"
    assert update(copy, "--main", "--no-restart").returncode == 0
    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD") == "main" and "commit 3" in git(copy, "log", "-1", "--format=%s")
    assert update(copy, "v9.9.9").returncode == 1


def test_stops_when_kias_own_files_were_changed(copy: Path):
    (copy / "compose.yaml").write_text("# mine\n")
    r = update(copy)
    assert r.returncode == 1 and "were changed here" in r.stderr
    assert git(copy, "describe", "--tags") == "v0.1.0" and (copy / "compose.yaml").read_text() == "# mine\n"
