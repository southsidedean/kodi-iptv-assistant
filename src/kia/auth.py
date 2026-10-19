"""The status page's admin password.

Where it comes from, first match wins:
1. KIA_ADMIN_PASSWORD in .env. The status page can't change it; change it in .env.
2. One saved from the status page, or made by kia on first start, kept as a salted hash
   in state/admin.json. A password kia made is also written to state/admin-password.txt
   so the owner can read it; changing it on the status page deletes that file.
KIA_EDITING=false turns editing off whatever the password.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# No look-alikes (0/o, 1/l/i), so it's easy to read off a screen and type on a TV.
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
MIN_LENGTH = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def generate() -> str:
    """Like kxq7-m2pa-9fht: 12 characters, about 59 bits."""
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3))


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT).hex()


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    tmp.replace(path)


@dataclass
class Auth:
    state_dir: Path
    env_password: str | None = None
    enabled: bool = True

    @property
    def saved(self) -> Path:
        return self.state_dir / "admin.json"

    @property
    def shown(self) -> Path:
        return self.state_dir / "admin-password.txt"

    @property
    def source(self) -> str:
        """env, generated, set, or off."""
        if not self.enabled:
            return "off"
        if self.env_password:
            return "env"
        try:
            return "generated" if json.loads(self.saved.read_text()).get("generated") else "set"
        except (OSError, ValueError):
            return "generated"

    def ensure(self, log=print) -> None:
        """Make a password on first start when .env doesn't set one."""
        if not self.enabled or self.env_password or self.saved.exists():
            return
        self.reset(log)

    def reset(self, log=print) -> str:
        password = generate()
        self._save(password, generated=True)
        _write_private(self.shown, password + "\n")
        log(f"Made a password for editing on the status page: {password} (also in {self.shown})")
        return password

    def _save(self, password: str, generated: bool) -> None:
        salt = secrets.token_bytes(16)
        record = {"salt": salt.hex(), "hash": _hash(password, salt), "scrypt": _SCRYPT, "generated": generated,
                  "changed": datetime.now(timezone.utc).isoformat(timespec="seconds")}  # fmt: skip
        _write_private(self.saved, json.dumps(record, indent=1))

    def check(self, given: str | None) -> bool:
        if not self.enabled or not given:
            return False
        if self.env_password:
            return hmac.compare_digest(given.encode(), self.env_password.encode())
        try:
            record = json.loads(self.saved.read_text())
            expected = record["hash"]
            actual = _hash(given, bytes.fromhex(record["salt"]))
        except (OSError, ValueError, KeyError):
            return False
        return hmac.compare_digest(actual, expected)

    def change(self, new: str) -> None:
        """Save a new password (the caller has checked the current one)."""
        if self.env_password:
            raise ValueError("The password is set in kia's .env file, so change it there and restart kia.")
        if len(new) < MIN_LENGTH:
            raise ValueError(f"Use at least {MIN_LENGTH} characters.")
        self._save(new, generated=False)
        self.shown.unlink(missing_ok=True)


def from_env(state_dir: Path) -> Auth:
    editing = (os.environ.get("KIA_EDITING") or "true").strip().lower() not in ("false", "no", "off", "0")
    return Auth(state_dir, os.environ.get("KIA_ADMIN_PASSWORD") or None, editing)
