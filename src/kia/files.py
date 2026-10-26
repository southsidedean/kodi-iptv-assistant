"""Writing a file so a reader never sees half of it: write a temporary file next to it,
then rename it over the old one. And reading one back without trusting its shape."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


def temp_name(path: Path) -> Path:
    """A temporary file next to path, its own to this process and thread, so two writers
    (the daemon and a kia command, or two of the daemon's threads) never share one. It
    ends in .tmp, which the web server doesn't serve and backups leave out."""
    return path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")


def read_object(path: Path) -> dict:
    """The JSON object in a file kia wrote; {} when it's missing, can't be read, isn't JSON,
    or holds anything else (a list, null). kia writes objects, so a file in another shape is
    damaged (a hand edit, a full disk), and counts as missing rather than stopping kia."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def obj(value: object) -> dict:
    """value if it's a JSON object, else {}: for the objects inside one."""
    return value if isinstance(value, dict) else {}


def items(value: object, kind: type = str) -> list:
    """value's items of kind if it's a list, else []: a string isn't read letter by letter."""
    return [v for v in value if isinstance(v, kind)] if isinstance(value, list) else []
