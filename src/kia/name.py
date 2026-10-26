"""This kia's name, for telling several installs apart.

It goes in alert titles ("kia (den): ..."), the Dashboard's header, the name assistants
give kia's MCP server (kia-den), and the kia_info number in /metrics. One comes from, in
this order:

1. the Dashboard's System page, which keeps it in state/name.txt (kia can write state/,
   not .env);
2. KIA_NAME in .env, which scripts/install.sh and scripts/update.sh fill in with the
   computer's name when it's missing;
3. the computer's name, without Docker. In Docker, the container's own name is a random
   id, so there's none, and kia goes by plain "kia".

A name is lowercase letters, digits, and dashes, at most 40, as gateway.sh's device names
are: the same in a title, a file name, and an MCP server's name. Anything else is turned
into one (clean), and the file, which a taken-over kia container could write, is read
only as far as a name goes.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

from kia.files import temp_name

FILE = "name.txt"  # in state/: a name set on the Dashboard
NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")
_LETTERS = "abcdefghijklmnopqrstuvwxyz0123456789-"  # all a name can hold


def clean(text: str) -> str:
    """text as a name: lowercase, with every run of other characters as one dash, at most
    40 characters; "" when nothing is left."""
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")[:40].strip("-")


def current(state_dir: Path) -> tuple[str, str]:
    """This kia's name and where it comes from: "dashboard", "env", or "computer"; ("", "")
    when there's none (Docker, with no KIA_NAME)."""
    try:
        with open(state_dir / FILE, encoding="utf-8", errors="replace") as f:
            saved = f.read(100).strip()
    except OSError:
        saved = ""
    if NAME.fullmatch(saved):
        return saved, "dashboard"
    if env := clean(os.environ.get("KIA_NAME", "")):
        return env, "env"
    if computer := computer_name():
        return computer, "computer"
    return "", ""


def computer_name() -> str:
    """This computer's name as a name, or "" in Docker, where it's the container's random id."""
    if Path("/.dockerenv").exists():
        return ""
    return clean(socket.gethostname().split(".")[0])


def save(state_dir: Path, text: str) -> str:
    """Set the name from the Dashboard; "" goes back to KIA_NAME or the computer's name.
    Raises ValueError for one that isn't a name, saying what is."""
    path = state_dir / FILE
    if not text.strip():
        path.unlink(missing_ok=True)
        return current(state_dir)[0]
    name = text.strip()
    if not NAME.fullmatch(name):
        raise ValueError("A name is lowercase letters, digits, and dashes (not at either end), at most 40, such as den or cabin-pi.")
    # Spelled out again from kia's own letters: the same name, checked above. CodeQL follows
    # any text from a request into a file, and through the API's shared request body it took
    # a test's password for this one; these characters are kia's own.
    name = "".join(_LETTERS[_LETTERS.index(c)] for c in name)
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(path)
    tmp.write_text(name + "\n", encoding="utf-8")
    tmp.replace(path)
    return name


def titled(title: str, name: str) -> str:
    """An alert's title with the name: "kia: nightly run failed" becomes "kia (den): nightly
    run failed". Titles that don't start with "kia" are left alone."""
    if not name or not re.match(r"kia\b", title):
        return title
    return f"kia ({name})" + title[3:]
