"""kia backup and kia restore: your settings and choices in one file.

A backup holds what you can't get back by running kia again: .env, kia.toml, your
sources file, your own playlists (playlists/), profiles (profiles/), state/ (the
password, channel numbers, favorites, playlist choices, stream history, alerts; not the
MCP server's token),
Threadfin's settings (threadfin/, threadfin-2/), and the secure page's certificate
(tls/, including its private key, so keep backups private). output/, work/, and vendor/ are left out; the next refresh rebuilds them.

A restore first backs up what's there now, so it can be undone, then puts the files
back. It refuses archives that aren't kia backups, and paths that would land outside
the kia folder.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from kia import __version__

FILES = (".env", "kia.toml", "sources.txt", "sources.local.txt")
FOLDERS = ("playlists", "profiles", "state", "tls", "threadfin", "threadfin-2")
# Left out: the MCP server's token (kia.mcp). A backup that turns up somewhere mustn't carry a
# working key to kia; after a restore, kia mcp-token makes a new one.
LEFT_OUT = ("state/mcp-token.txt",)
MANIFEST = "kia-backup.json"


def _wanted(base: Path, sources_file: Path | None, extra: tuple[str, ...] = ()) -> list[Path]:
    names = set(FILES) | set(extra)
    if sources_file is not None:
        try:
            names.add(str(sources_file.resolve().relative_to(base.resolve())))
        except ValueError:
            pass  # a sources file outside the kia folder isn't backed up
    found = [base / n for n in sorted(names) if (base / n).is_file()]
    for folder in FOLDERS:
        if (base / folder).is_dir():
            found += sorted(p for p in (base / folder).rglob("*") if p.is_file() and not p.name.endswith(".tmp")
                            and p.relative_to(base).as_posix() not in LEFT_OUT)  # fmt: skip
    return found


def make(base: Path, out: Path, sources_file: Path | None = None, extra: tuple[str, ...] = ()) -> list[str]:
    """Write a backup of the kia folder `base` to `out` (.tar.gz), with the files named in
    extra too. Returns what's in it."""
    files = _wanted(base, sources_file, extra)
    names = [str(p.relative_to(base)) for p in files]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    manifest = json.dumps({"kia": __version__, "made": datetime.now().astimezone().isoformat(timespec="seconds"),
                           "files": names}, indent=1).encode()  # fmt: skip
    # Private from the start: it holds the password and the certificate's key.
    os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
    # dereference: store what a symlink points at (e.g. a Let's Encrypt certificate) as a file.
    with tarfile.open(tmp, "w:gz", dereference=True) as tar:
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
        for p, name in zip(files, names, strict=True):
            tar.add(p, arcname=name, recursive=False)
    tmp.replace(out)
    return names


def default_name(folder: Path) -> Path:
    return folder / f"kia-backup-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"


def _safe(name: str) -> bool:
    p = PurePosixPath(name)
    return not p.is_absolute() and ".." not in p.parts and bool(p.parts)


def read_manifest(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        try:
            f = tar.extractfile(MANIFEST)
        except KeyError:
            f = None
        if f is None:
            raise ValueError(f"{archive.name} isn't a kia backup (it has no {MANIFEST})")
        return json.loads(f.read())


def restore(archive: Path, base: Path, safety_folder: Path, log=print) -> tuple[list[str], Path | None]:
    """Put a backup's files back into the kia folder `base`, after backing up what's there
    now into safety_folder. Returns (restored files, the safety backup)."""
    manifest = read_manifest(archive)
    with tarfile.open(archive, "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.name != MANIFEST]
        bad = [m.name for m in members if not _safe(m.name) or not (m.isfile() or m.isdir())]
        if bad:
            raise ValueError(f"{archive.name} has entries kia won't restore: {', '.join(bad[:5])}")
        # Only kia's own settings files and folders, plus a sources file of your own at the
        # top of the kia folder; never what the archive's list claims beyond that.
        def own(name: str) -> bool:
            parts = PurePosixPath(name).parts
            return (name in FILES or parts[0] in FOLDERS
                    or (len(parts) == 1 and name.endswith(".txt") and name in manifest.get("files", [])))  # fmt: skip

        allowed = [m for m in members if m.isfile() and own(m.name)]
        # The safety copy has what the restore replaces, a sources file of your own too.
        extra = tuple(m.name for m in allowed if "/" not in m.name and m.name not in FILES)
        safety = None
        if _wanted(base, None, extra):
            safety = default_name(safety_folder).with_name(f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.tar.gz")
            make(base, safety, extra=extra)
            log(f"Saved the current settings first, in {safety}")
        restored = []
        for m in allowed:
            target = base / m.name
            # A link in a folder along the way (state/d -> somewhere else) would put the file
            # outside the kia folder: kia's container can write state/ and could have planted one.
            linked = next((p for p in target.parents if p != base and base in p.parents and p.is_symlink()), None)
            if linked is not None:
                log(f"Left {m.name} out: {linked.relative_to(base)} is a link")
                continue
            if target.is_symlink():
                # Such as tls/cert.pem linked to Let's Encrypt's: replacing the link with the
                # backup's copy would keep kia on that copy after the next renewal.
                log(f"Left {m.name} as it is: it's a link to {os.readlink(target)}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            data = tar.extractfile(m)
            if data is None:
                continue
            tmp = target.with_name(target.name + ".restore-tmp")
            tmp.unlink(missing_ok=True)
            # Private while it's written (it may be the password or the certificate's key), then
            # the backup's own permissions, never executable.
            with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
                f.write(data.read())
            tmp.chmod((m.mode & 0o666) or 0o600)
            tmp.replace(target)
            restored.append(m.name)
    log(f"Restored {len(restored)} files from a backup made {manifest.get('made', '?')} by kia {manifest.get('kia', '?')}")
    return restored, safety
