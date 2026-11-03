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


# kia's own keys and passwords (the admin password, the MCP token, the players' pseudonym
# key) live in private/, next to state/, not in it. When kia runs as two containers, the one
# that runs the stream tools must write state/, so it never mounts private/. Older versions
# kept them in state/: private_file moves one across the first time it's asked for.
PRIVATE_DIR = "private"


def private_folder(state_dir: Path) -> Path:
    return state_dir.parent / PRIVATE_DIR


def private_usable(state_dir: Path) -> bool:
    """Whether kia can keep files in private/. Not when Docker made the folder (as root,
    because it was missing when the container started), or a read-only one is mounted."""
    folder = private_folder(state_dir)
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(folder, os.W_OK | os.X_OK)


def private_file(state_dir: Path, name: str) -> Path:
    """Where kia keeps its own file `name`: private/name, moving it there from state/ if an
    older kia left it. While private/ can't be written, state/name, so the password still
    works; kia's log says how to fix the folder (cli.secure_setup)."""
    new, old = private_folder(state_dir) / name, state_dir / name
    if new.exists():
        return new
    if not private_usable(state_dir):
        return old
    if old.exists():
        # Copied and then removed, not renamed: in Docker the two folders are separate
        # mounts, and a rename can't cross from one to the other. Owner-only, like the original.
        tmp = temp_name(new)
        try:
            tmp.unlink(missing_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(old.read_bytes())
            tmp.replace(new)
            old.unlink(missing_ok=True)
        except OSError:
            tmp.unlink(missing_ok=True)
            # Another kia process may have moved it a moment ago; then it's in private/ now.
            return new if new.exists() else old
    return new
