"""kia backup and kia restore: your settings and choices in one file.

A backup holds what you can't get back by running kia again: .env, kia.toml, your
sources file, your own playlists (playlists/), profiles (profiles/), state/ (the
password, channel numbers, favorites, playlist choices, stream history, alerts), and
the secure page's certificate (tls/, including its private key, so keep backups
private). output/, work/, and vendor/ are left out; the next refresh rebuilds them.

A restore first backs up what's there now, so it can be undone, then puts the files
back. It refuses archives that aren't kia backups, and paths that would land outside
the kia folder.
"""

from __future__ import annotations

import io
import json
import tarfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from kia import __version__

FILES = (".env", "kia.toml", "sources.txt", "sources.local.txt")
FOLDERS = ("playlists", "profiles", "state", "tls")
MANIFEST = "kia-backup.json"


def _wanted(base: Path, sources_file: Path | None) -> list[Path]:
    names = set(FILES)
    if sources_file is not None:
        try:
            names.add(str(sources_file.resolve().relative_to(base.resolve())))
        except ValueError:
            pass  # a sources file outside the kia folder isn't backed up
    found = [base / n for n in sorted(names) if (base / n).is_file()]
    for folder in FOLDERS:
        if (base / folder).is_dir():
            found += sorted(p for p in (base / folder).rglob("*") if p.is_file() and not p.name.endswith(".tmp"))
    return found


def make(base: Path, out: Path, sources_file: Path | None = None) -> list[str]:
    """Write a backup of the kia folder `base` to `out` (.tar.gz). Returns what's in it."""
    files = _wanted(base, sources_file)
    names = [str(p.relative_to(base)) for p in files]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    manifest = json.dumps({"kia": __version__, "made": datetime.now().astimezone().isoformat(timespec="seconds"),
                           "files": names}, indent=1).encode()  # fmt: skip
    with tarfile.open(tmp, "w:gz") as tar:
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
        for p, name in zip(files, names):
            tar.add(p, arcname=name, recursive=False)
    tmp.chmod(0o600)  # it holds the password and the certificate's key
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
        allowed = [m for m in members if m.isfile() and (m.name in FILES or PurePosixPath(m.name).parts[0] in FOLDERS
                                                          or m.name in manifest.get("files", []))]  # fmt: skip
        safety = None
        if _wanted(base, None):
            safety = default_name(safety_folder).with_name(f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.tar.gz")
            make(base, safety)
            log(f"Saved the current settings first, in {safety}")
        restored = []
        for m in allowed:
            target = base / m.name
            target.parent.mkdir(parents=True, exist_ok=True)
            data = tar.extractfile(m)
            if data is None:
                continue
            tmp = target.with_name(target.name + ".restore-tmp")
            tmp.write_bytes(data.read())
            tmp.chmod(m.mode & 0o777 or 0o644)
            tmp.replace(target)
            restored.append(m.name)
    log(f"Restored {len(restored)} files from a backup made {manifest.get('made', '?')} by kia {manifest.get('kia', '?')}")
    return restored, safety
