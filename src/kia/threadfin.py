"""kia threadfin-setup: give each Threadfin its settings before it first starts.

Threadfin (docs/plex.md) needs a playlist, a guide, a filter that passes every channel,
its guide source set to XEPG, a tuner count, and FFmpeg options that pass on one stream.
Set by hand, the order matters: channels Threadfin sees before it has the guide never get
matched to it. Written here before Threadfin's first start, everything is in place at
once, and Threadfin fills in the rest of its settings itself.

A Threadfin that already has settings (threadfin/conf/settings.json) keeps them: changes
made on its own page stay. The one exception is its login: Threadfin's page has none by
default, so anyone on your network could change its playlist or FFmpeg settings. A
Threadfin without any user gets one, admin, with a random password saved in its
conf/admin-password.txt, and its page asks for it. Plex's access (the tuner, playlist, and
guide) stays open, as Plex needs. compose.yaml runs this as a one-off container
(threadfin-setup) before the Threadfins start.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from kia.files import temp_name

# The Threadfins compose.yaml can start: their folder in the kia folder, and the .env
# setting that names the kia playlist each one reads.
THREADFINS = (("threadfin", "KIA_THREADFIN_PLAYLIST", "plex"), ("threadfin-2", "KIA_THREADFIN_2_PLAYLIST", "plex-2"))
# Pass on the best single stream, not every quality at once (Threadfin's own options).
FFMPEG_OPTIONS = "-hide_banner -loglevel error -i [URL] -c copy -f mpegts pipe:1"
TUNERS = 4
# Threadfin re-reads the playlist and guide at these times (HHMM), so new channels reach
# Plex the same day; kia's quick guide refresh runs every few hours too.
UPDATES = ["0000", "0600", "1200", "1800"]


def playlist_for(index: int, output_dir: Path) -> str:
    """The kia playlist a Threadfin reads, when .env doesn't say: plex, or plex-1 and
    plex-2 for two tuners, whichever exists."""
    if index == 1:
        if not (output_dir / "plex.m3u").exists() and (output_dir / "plex-1.m3u").exists():
            return "plex-1"
        return "plex"
    return "plex-2"


def settings(playlist: str, base: str = "http://web") -> dict:
    """A new Threadfin's settings, reading kia's playlist `playlist` and its guide."""
    m3u, xmltv = "MKIAPLAYLIST00000001", "XKIAGUIDE00000000001"  # Threadfin's own ids for them
    blank = {"http_proxy.ip": "", "http_proxy.port": ""}
    return {
        "version": "0.5.0",  # Threadfin refuses a settings file without a version
        "files": {
            "hdhr": {},
            "m3u": {m3u: {"id.provider": m3u, "file.threadfin": f"{m3u}.m3u", "file.source": f"{base}/{playlist}.m3u",
                          "name": f"kia {playlist}", "description": "Set up by kia", "type": "m3u",
                          "buffer": "ffmpeg", "tuner": TUNERS, "http_headers.origin": "", "http_headers.referer": "",
                          **blank}},
            "xmltv": {xmltv: {"id.provider": xmltv, "file.threadfin": f"{xmltv}.xml",
                              "file.source": f"{base}/{playlist}.guide.xml.gz", "name": f"kia {playlist} guide",
                              "description": "Set up by kia", "type": "xmltv", **blank}},
        },
        # A single space matches every channel kia writes (Threadfin passes on only
        # channels that a filter picks).
        "filter": {"0": {"name": "Everything", "description": "Every channel from kia", "type": "custom-filter",
                         "filter": " ", "caseSensitive": False, "startingNumber": "1", "x-category": ""}},
        "epgSource": "XEPG",
        "tuner": TUNERS,
        "ffmpeg.options": FFMPEG_OPTIONS,
        "update": UPDATES,
    }


def _threadfin_hash(secret: str) -> str:
    """Threadfin 1.2's own hash for a user name or password (its authentication package):
    an HMAC-SHA256 of a fixed text keyed with the secret. It doesn't use the salt it stores."""
    return base64.b64encode(hmac.new(secret.encode(), b"_remote_db", hashlib.sha256).digest()).decode()


def _write_private(path: Path, text: str) -> None:
    tmp = temp_name(path)
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    tmp.replace(path)


def secure_login(conf: Path, folder: str, log=print) -> bool:
    """Give a Threadfin without users a login (see the module's notes). True if it got one."""
    users_file = conf / "authentication.json"
    try:
        db = json.loads(users_file.read_text())
    except (OSError, ValueError):
        db = {}
    if isinstance(db, dict) and db.get("users"):
        # Someone set up Threadfin's login already. If it was kia, stopped before it turned the
        # login on for Threadfin's page, finish that. Only then: once it's done (LOGIN_DONE), a
        # login you turn off on Threadfin's own page stays off.
        if (conf / "admin-password.txt").exists() and not (conf / LOGIN_DONE).exists():
            _turn_on_login(conf, folder, log)
        return False
    password = secrets.token_urlsafe(18)
    alphabet = "-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789aBcDeFgHiJkLmNoPqRsTuVwXyZ_"
    user_id = "id-" + "".join(secrets.choice("ABCDEFGHJKLMNOPQRSTUVWXYZ0123456789") for _ in range(10))
    user = {
        "_id": user_id,
        "_salt": "".join(secrets.choice(alphabet) for _ in range(20)),
        "_username": _threadfin_hash("admin"),
        "_password": _threadfin_hash(password),
        # As Threadfin's own first user: its page, Plex's tuner, the playlist and the guide.
        "data": {"username": "admin", "authentication.web": True, "authentication.pms": True,
                 "authentication.m3u": True, "authentication.xml": True, "authentication.api": False,
                 "defaultUser": True},
    }  # fmt: skip
    db = {"dbVersion": db.get("dbVersion", "1.0"), "hash": "sha256", "users": {user_id: user}}
    conf.mkdir(parents=True, exist_ok=True)
    _write_private(conf / "admin-password.txt", password + "\n")
    _write_private(users_file, json.dumps(db, indent=1) + "\n")
    return _turn_on_login(conf, folder, log)


LOGIN_DONE = "kia-login-done"  # in conf/: kia has turned the page's login on once


def _turn_on_login(conf: Path, folder: str, log) -> bool:
    """Have Threadfin's page ask for its login. True if it didn't already."""
    settings_file = conf / "settings.json"
    try:
        current = json.loads(settings_file.read_text())
    except (OSError, ValueError) as exc:
        log(f"{folder}: couldn't read conf/settings.json ({exc}), so its page doesn't ask for a login yet.")
        return False
    if not isinstance(current, dict):
        log(f"{folder}: conf/settings.json isn't Threadfin's settings, so its page doesn't ask for a login yet.")
        return False
    if current.get("authentication.web") is True:
        (conf / LOGIN_DONE).touch()
        return False
    current["authentication.web"] = True  # only the page: Plex's access stays open
    tmp = temp_name(settings_file)
    tmp.write_text(json.dumps(current, indent=1) + "\n")
    tmp.replace(settings_file)
    (conf / LOGIN_DONE).touch()
    log(f"{folder}: its page now asks for a login: admin, with the password in {folder}/conf/admin-password.txt.")
    return True


def set_up(base: Path, output_dir: Path, log=print) -> list[str]:
    """Write settings for each Threadfin folder that has none. Returns the folders set up."""
    done = []
    # compose.yaml passes COMPOSE_PROFILES on, so only the Threadfins that run get set up.
    running = {p.strip() for p in os.environ.get("KIA_COMPOSE_PROFILES", "").split(",") if p.strip()}
    for index, (folder, setting, profile) in enumerate(THREADFINS, 1):
        if running and profile not in running:
            continue
        conf = base / folder / "conf"
        target = conf / "settings.json"
        if target.exists():
            log(f"{folder}: already set up; leaving its settings as they are.")
            secure_login(conf, folder, log)
            continue
        playlist = os.environ.get(setting) or playlist_for(index, output_dir)
        conf.mkdir(parents=True, exist_ok=True)
        tmp = temp_name(target)
        tmp.write_text(json.dumps(settings(playlist), indent=1) + "\n")
        tmp.replace(target)
        note = "" if (output_dir / f"{playlist}.m3u").exists() else (
            f" There's no playlist named {playlist} yet: make it on the Dashboard, then run "
            f"docker compose restart {folder}.")  # fmt: skip
        log(f"{folder}: set up to read kia's {playlist} playlist and its guide.{note}")
        secure_login(conf, folder, log)
        done.append(folder)
    return done
