"""One refresh at a time, and the Dashboard's saves taking turns with it, across processes:
`docker compose exec kia python3 -m kia run` is a second process next to the daemon."""

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from kia import cli, locks

pytestmark = pytest.mark.skipif(locks.fcntl is None, reason="flock isn't available here (Windows)")
SRC = str(Path(__file__).resolve().parent.parent / "src")


def hold(kind: str, state: Path, seconds: float) -> subprocess.Popen:
    """Another process holding a lock: like the daemon mid-refresh."""
    code = (
        "import sys, time\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\nfrom kia import locks\n"
        + ("with locks.refresh(Path(sys.argv[2]), 'the nightly refresh'):\n" if kind == "refresh" else
           "lock = locks.FilesLock(); lock.configure(Path(sys.argv[2]))\nwith lock:\n")
        + "    print('held', flush=True)\n    time.sleep(float(sys.argv[3]))\n"
    )  # fmt: skip
    proc = subprocess.Popen([sys.executable, "-c", code, SRC, str(state), str(seconds)], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_a_second_refresh_is_refused_with_what_is_running(tmp_path: Path):
    other = hold("refresh", tmp_path, 5)
    try:
        with pytest.raises(locks.Busy) as busy, locks.refresh(tmp_path, "kia run"):
            pass
        assert "the nightly refresh, started" in str(busy.value)
    finally:
        other.kill()
        other.wait()
    # Free again once it's done; and a step inside a refresh doesn't wait for itself.
    with locks.refresh(tmp_path, "kia run"), locks.refresh(tmp_path, "kia check"):
        pass


def test_kia_says_so_and_stops(tmp_path: Path, capsys, monkeypatch):
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://x/us.m3u us\n")
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    other = hold("refresh", tmp_path / "state", 5)
    try:
        assert cli.main(["guide", "-c", str(tmp_path / "kia.toml")]) == cli.EXIT_BUSY
    finally:
        other.kill()
        other.wait()
    assert "A refresh is already running (the nightly refresh, started" in capsys.readouterr().err


def test_writes_wait_for_each_other_across_processes(tmp_path: Path):
    other = hold("files", tmp_path, 1.0)
    lock = locks.FilesLock()
    lock.configure(tmp_path)
    start = time.monotonic()
    with lock, lock:  # reentrant within one process
        waited = time.monotonic() - start
    other.wait()
    assert waited >= 0.5


def test_threads_share_the_files_lock(tmp_path: Path):
    lock = locks.FilesLock()
    lock.configure(tmp_path)
    inside, order = threading.Event(), []

    def first():
        with lock:
            inside.set()
            time.sleep(0.3)
            order.append("first")

    t = threading.Thread(target=first)
    t.start()
    inside.wait(5)
    with lock:
        order.append("second")
    t.join()
    assert order == ["first", "second"]
