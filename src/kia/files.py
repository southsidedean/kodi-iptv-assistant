"""Writing a file so a reader never sees half of it: write a temporary file next to it,
then rename it over the old one."""

from __future__ import annotations

import os
import threading
from pathlib import Path


def temp_name(path: Path) -> Path:
    """A temporary file next to path, its own to this process and thread, so two writers
    (the daemon and a kia command, or two of the daemon's threads) never share one. It
    ends in .tmp, which the web server doesn't serve and backups leave out."""
    return path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
