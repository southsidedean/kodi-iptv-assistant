"""Locks that hold across processes, not just threads.

`docker compose exec kia python3 -m kia run` is a second process next to the daemon, so a
lock in memory can't see it: two refreshes could run at once, or a refresh could write the
channel numbers while the Dashboard saves one. These locks are files in state/, held with
flock (on Windows, where there's no flock, they hold within one process only).

- refresh(): one refresh at a time (the nightly run, a guide refresh, or a step run by
  hand). It doesn't wait: a second one gets Busy, with what's running and since when.
  `kia check --url` doesn't take it: it writes to work/check-test, not the refresh's files.
- FilesLock: the short sections that write kia's state (channel choices, numbers, profiles)
  and the Dashboard's saves take turns. It waits; those sections take moments.
"""

from __future__ import annotations

import contextlib
import os
import threading
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None


class Busy(Exception):
    """Another refresh is running. The message says which, and since when."""


_refresh = {"depth": 0}
_refresh_guard = threading.Lock()


@contextlib.contextmanager
def refresh(state_dir: Path, what: str):
    """Hold the refresh lock for `what`; inside a refresh this process holds, it's a no-op."""
    with _refresh_guard:
        if _refresh["depth"]:
            _refresh["depth"] += 1
            nested = True
        else:
            nested = False
    if nested:
        try:
            yield
        finally:
            with _refresh_guard:
                _refresh["depth"] -= 1
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / "refresh.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                held = os.read(fd, 300).decode("utf-8", "replace").strip()
                raise Busy(held or "another refresh") from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{what}, started {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}".encode())
        with _refresh_guard:
            _refresh["depth"] = 1
        try:
            yield
        finally:
            with _refresh_guard:
                _refresh["depth"] = 0
            os.ftruncate(fd, 0)
    finally:
        os.close(fd)  # closing it also lets go of the flock


class FilesLock:
    """A reentrant lock for writing kia's state, shared with other processes once
    configure() has told it where state/ is (until then, it holds within this process)."""

    def __init__(self, name: str = "files.lock") -> None:
        self.name = name
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._fd: int | None = None
        self.path: Path | None = None

    def configure(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / self.name

    def __enter__(self) -> FilesLock:
        self._thread_lock.acquire()
        self._depth += 1
        if self._depth == 1 and self.path is not None and fcntl is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._fd = fd
            except BaseException:
                self._depth -= 1
                self._thread_lock.release()
                raise
        return self

    def __exit__(self, *exc: object) -> None:
        self._depth -= 1
        if self._depth == 0 and self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._thread_lock.release()
