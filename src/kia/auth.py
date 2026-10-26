"""The Dashboard's admin password.

Where it comes from, first match wins:
1. KIA_ADMIN_PASSWORD in .env. The Dashboard can't change it; change it in .env.
2. One saved from the Dashboard, or made by kia on first start, kept as a salted hash
   in state/admin.json. A password kia made is also written to state/admin-password.txt
   so the owner can read it; changing it on the Dashboard deletes that file.
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

from kia.files import temp_name

# No look-alikes (0/o, 1/l/i), so it's easy to read off a screen and type on a TV.
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
MIN_LENGTH = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def typeable(password: str) -> bool:
    """Only characters a browser can send in a request header: letters, digits, spaces, and
    the symbols on a US keyboard, with no accents or emoji."""
    return all(" " <= c <= "~" for c in password)


def generate() -> str:
    """Like kxq7-m2pa-9fht: 12 characters, about 59 bits."""
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3))


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT).hex()


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(path)
    tmp.unlink(missing_ok=True)  # one left by a crash could have other permissions
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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
        record = self._record()
        return "set" if record and not record.get("generated") else "generated"

    def ensure(self, log=print) -> None:
        """Make a password on first start when .env doesn't set one."""
        if self.enabled and self.env_password:
            if not typeable(self.env_password):
                log("KIA_ADMIN_PASSWORD has characters a browser can't send (accents, emoji), so the "
                    "Dashboard can't use it: change it in .env to letters, digits, and ordinary symbols.")  # fmt: skip
            elif self.env_password != self.env_password.strip():
                log("KIA_ADMIN_PASSWORD starts or ends with a space, which a browser drops when it sends the password, "
                    "so the Dashboard can't use it: take the spaces out in .env.")  # fmt: skip
            elif len(self.env_password) < MIN_LENGTH:
                log(f"KIA_ADMIN_PASSWORD is shorter than {MIN_LENGTH} characters: easy to guess on your network.")
        if not self.enabled or self.env_password or self._record() is not None:
            return
        if self.saved.exists():
            log(f"{self.saved} is damaged, so kia made a new password.")
        self.reset(log)

    def reset(self, log=print) -> str:
        """A new password, saved in state/admin-password.txt (readable only by you). It's
        never printed, so it can't land in a log others can read."""
        password = generate()
        self._save(password, generated=True)
        _write_private(self.shown, password + "\n")
        log(f"Made a new password for editing on the Dashboard; it's in {self.shown}")
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
        record = self._record()
        if record is None:
            return False
        return hmac.compare_digest(_hash(given, bytes.fromhex(record["salt"])), record["hash"])

    def _record(self) -> dict | None:
        """The saved password's record, or None when there's none kia can use: missing, not
        JSON, another shape, or without a salt and hash in hex. ensure() then makes a new one."""
        try:
            record = json.loads(self.saved.read_text())
            if isinstance(record, dict) and isinstance(record.get("hash"), str):
                bytes.fromhex(record["salt"])
                return record
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return None

    def change(self, new: str) -> None:
        """Save a new password (the caller has checked the current one)."""
        if self.env_password:
            raise ValueError("The password is set in kia's .env file, so change it there and restart kia.")
        if len(new) < MIN_LENGTH:
            raise ValueError(f"Use at least {MIN_LENGTH} characters.")
        if new != new.strip():
            # It travels in a header, and browsers drop spaces at its ends: it could never be typed back.
            raise ValueError("Leave out spaces at the start and end.")
        if not typeable(new):
            raise ValueError("Use letters, digits, spaces, and ordinary symbols only, with no accents or emoji: "
                             "browsers can't send other characters in kia's password.")  # fmt: skip
        self._save(new, generated=False)
        self.shown.unlink(missing_ok=True)


def from_env(state_dir: Path) -> Auth:
    editing = (os.environ.get("KIA_EDITING") or "true").strip().lower() not in ("false", "no", "off", "0")
    return Auth(state_dir, os.environ.get("KIA_ADMIN_PASSWORD") or None, editing)
