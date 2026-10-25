"""scripts/update.sh: moves kia to its newest release (not a test release, not main),
refuses when kia's own files were changed, and asks before restarting while someone watches.
Docker and curl are stand-ins."""

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
    (kia / ".env").write_text("KIA_PORT=1\n")
    (bin_dir / "curl").write_text('#!/bin/sh\necho "kia_pluto_channels_playing 0"\n')  # kia's numbers: nobody watching
    (bin_dir / "curl").chmod(0o755)
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


def test_services_restart_when_their_mounted_config_files_change(copy: Path):
    # Docker keeps a file mounted on its own pinned to the copy the container started with.
    origin = copy.parent / "origin"
    (origin / "deploy" / "monitoring").mkdir(parents=True)
    (origin / "deploy" / "nginx.conf").write_text("server {}\n")
    (origin / "deploy" / "monitoring" / "prometheus.yml").write_text("scrape_configs: []\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "new settings")
    git(origin, "tag", "v0.4.0")
    log = copy.parent / "docker.log"
    (copy.parent / "bin" / "docker").write_text(
        f'#!/bin/sh\necho "$*" >> {log}\n'
        'case "$*" in *"ps --services --status running"*) printf "kia\\nweb\\nplex\\n" ;; esac\n')  # no Prometheus running
    r = update(copy, "v0.4.0")
    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    assert "compose restart web" in calls and "compose restart prometheus" not in calls  # only what runs
    assert "Restarted web" in r.stdout
    log.write_text("")
    assert update(copy, "v0.4.0").returncode == 0  # already there: nothing changed, nothing restarts
    assert not any(c.startswith("compose restart") for c in log.read_text().splitlines())


def test_asks_first_while_someone_watches_through_jellyfin(copy: Path):
    (copy.parent / "bin" / "curl").write_text(
        '#!/bin/sh\nprintf \'kia_jellyfin_sessions{kind="live",video="copy",hw="none",location="lan"} 1\\n\'\n')
    (copy.parent / "bin" / "curl").chmod(0o755)
    r = update(copy)  # no terminal to answer: not restarted
    assert r.returncode == 0 and "kia says: 1 playing" in r.stdout and "Not restarted" in r.stdout
    assert not (copy.parent / "docker.log").exists()


def test_a_failed_run_still_restarts_what_changed_on_the_next_one(copy: Path):
    origin = copy.parent / "origin"
    (origin / "deploy").mkdir()
    (origin / "deploy" / "nginx.conf").write_text("server {}\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "new settings")
    git(origin, "tag", "v0.4.0")
    log = copy.parent / "docker.log"
    (copy.parent / "bin" / "docker").write_text(
        f'#!/bin/sh\necho "$*" >> {log}\n'
        '[ -e "$HOME/docker-down" ] && exit 1\n'
        'case "$*" in *"ps --services --status running"*) echo web ;; esac\n')
    (copy.parent / "docker-down").touch()
    assert update(copy, "v0.4.0").returncode != 0  # Docker isn't running: the files moved, nothing restarted
    (copy.parent / "docker-down").unlink()
    r = update(copy, "v0.4.0")
    assert r.returncode == 0 and "Restarted web" in r.stdout
    assert update(copy, "v0.4.0").returncode == 0 and "Restarted web" not in update(copy, "v0.4.0").stdout


def test_a_published_image_follows_the_release(copy: Path):
    (copy / ".env").write_text("KIA_PORT=1\nKIA_IMAGE=ghcr.io/example/kia:0.1.0\n")
    r = update(copy)
    assert r.returncode == 0, r.stderr
    assert "KIA_IMAGE=ghcr.io/example/kia:0.2.0\n" in (copy / ".env").read_text()
    assert (copy.parent / "docker.log").read_text().splitlines() == ["compose pull", "compose up -d"]


def test_what_was_deployed_is_kept_where_kia_cant_write(copy: Path):
    # state/ is writable by the kia container. The record of what the containers started
    # with lives in git's folder instead, and an old one in state/ is taken only if it's a
    # commit: anything else (--output=FILE, say) would reach git as an option.
    target = copy.parent / "victim"
    target.write_text("keep me\n")
    (copy / "state").mkdir()
    (copy / "state" / "deployed-rev").write_text(f"--output={target}\n")
    r = update(copy)
    assert r.returncode == 0, r.stderr
    assert target.read_text() == "keep me\n" and not (copy / "state" / "deployed-rev").exists()
    assert (copy / ".git" / "kia-deployed-rev").read_text().strip() == git(copy, "rev-parse", "HEAD")


def test_an_old_record_of_a_real_commit_is_taken_over(copy: Path):
    v1 = git(copy, "rev-parse", "HEAD")
    (copy / "state").mkdir()
    (copy / "state" / "deployed-rev").write_text(v1 + "\n")
    origin = copy.parent / "origin"
    (origin / "deploy").mkdir()
    (origin / "deploy" / "nginx.conf").write_text("server {}\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "new settings")
    git(origin, "tag", "v0.4.0")
    (copy.parent / "bin" / "docker").write_text(
        f'#!/bin/sh\necho "$*" >> {copy.parent / "docker.log"}\n'
        'case "$*" in *"ps --services --status running"*) echo web ;; esac\n')
    r = update(copy, "v0.4.0")
    assert r.returncode == 0 and "Restarted web" in r.stdout  # it knew the containers ran v0.1.0's files


def test_a_link_left_in_state_is_removed_not_followed(copy: Path):
    target = copy.parent / "victim"
    target.write_text("keep me\n")
    (copy / "state").mkdir()
    (copy / "state" / "deployed-rev").symlink_to(target)
    assert update(copy).returncode == 0
    assert target.read_text() == "keep me\n" and not (copy / "state" / "deployed-rev").is_symlink()


def test_a_copy_that_follows_main_stays_on_main(copy: Path):
    assert update(copy, "--main", "--no-restart").returncode == 0
    r = update(copy, "--no-restart")  # no version named
    assert r.returncode == 0 and "follows main" in r.stdout
    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert update(copy, "v0.2.0", "--no-restart").returncode == 0  # a release, when you name one
    assert git(copy, "describe", "--tags") == "v0.2.0"


def test_kia_running_without_an_answer_isnt_taken_as_nobody_watching(copy: Path):
    (copy.parent / "bin" / "curl").write_text("#!/bin/sh\nexit 7\n")
    log = copy.parent / "docker.log"
    (copy.parent / "bin" / "docker").write_text(
        f'#!/bin/sh\necho "$*" >> {log}\ncase "$*" in *"ps --services --status running"*) echo kia ;; esac\n')
    r = update(copy)
    assert "can't tell who's watching" in r.stdout and "Not restarted" in r.stdout
    assert "compose up -d" not in log.read_text()
    # kia not running at all: nobody can be watching through it.
    (copy.parent / "bin" / "docker").write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
    r = update(copy)
    assert r.returncode == 0 and "compose up -d" in log.read_text()


@pytest.mark.parametrize("image", ["ghcr.io/example/kia@sha256:" + "a" * 64, "my-server.lan:5000/kia"])
def test_an_image_by_digest_or_on_a_registry_port_is_left_alone(copy: Path, image: str):
    (copy / ".env").write_text(f"KIA_PORT=1\nKIA_IMAGE={image}\n")
    r = update(copy)
    assert r.returncode == 0, r.stderr
    assert f"KIA_IMAGE={image}\n" in (copy / ".env").read_text()
