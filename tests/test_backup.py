import io
import json
import tarfile
from pathlib import Path

import pytest

from kia import backup, cli


def kia_folder(base: Path) -> Path:
    (base / "state").mkdir(parents=True)
    (base / "profiles").mkdir()
    (base / "playlists").mkdir()
    (base / "output").mkdir()
    (base / ".env").write_text("KIA_PUBLIC_URL=http://192.168.1.20:8780\n")
    (base / "kia.toml").write_text("")
    (base / "sources.txt").write_text("https://example.org/a.m3u\n")
    (base / "state" / "channels.json").write_text('{"favorites": ["A.us"]}')
    (base / "state" / "x.tmp").write_text("half written")
    (base / "profiles" / "profiles.toml").write_text("[kids]\n")
    (base / "playlists" / "mine.m3u").write_text("#EXTM3U\n")
    (base / "output" / "curated.m3u").write_text("#EXTM3U\n")
    return base


def test_backup_and_restore_round_trip(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    home = kia_folder(tmp_path / "home")
    assert cli.main(["backup", "-c", str(home / "kia.toml"), "-o", str(tmp_path / "b.tar.gz")]) == 0
    names = backup.read_manifest(tmp_path / "b.tar.gz")["files"]
    assert ".env" in names and "state/channels.json" in names and "playlists/mine.m3u" in names
    assert not any(n.startswith("output/") or n.endswith(".tmp") for n in names)
    assert (tmp_path / "b.tar.gz").stat().st_mode & 0o077 == 0  # private

    other = tmp_path / "new"
    other.mkdir()
    (other / ".env").write_text("old\n")
    assert cli.main(["restore", str(tmp_path / "b.tar.gz"), "--to", str(other)]) == 0
    assert (other / ".env").read_text() == "KIA_PUBLIC_URL=http://192.168.1.20:8780\n"
    assert json.loads((other / "state" / "channels.json").read_text())["favorites"] == ["A.us"]
    saved = list((other / "work" / "backups").glob("before-restore-*.tar.gz"))
    assert len(saved) == 1 and ".env" in backup.read_manifest(saved[0])["files"]  # the old one is kept


def evil(path: Path, name: str, manifest: bool = True) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for n, data in ([(backup.MANIFEST, b'{"files": []}')] if manifest else []) + [(name, b"x")]:
            info = tarfile.TarInfo(n)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_restore_refuses_strange_archives(tmp_path: Path):
    with pytest.raises(ValueError, match="isn't a kia backup"):
        backup.restore(evil(tmp_path / "a.tar.gz", ".env", manifest=False), tmp_path, tmp_path / "b")
    with pytest.raises(ValueError, match="won't restore"):
        backup.restore(evil(tmp_path / "c.tar.gz", "../outside.txt"), tmp_path / "kia", tmp_path / "b")
    assert not (tmp_path / "outside.txt").exists()
    restored, _ = backup.restore(evil(tmp_path / "d.tar.gz", "vendor/epg/x.js"), tmp_path / "kia", tmp_path / "b")
    assert restored == []  # only kia's own settings folders are restored


def test_the_safety_copy_keeps_a_sources_file_of_your_own(tmp_path: Path):
    home = kia_folder(tmp_path / "home")
    (home / "my-sources.txt").write_text("https://example.org/mine.m3u\n")
    backup.make(home, tmp_path / "b.tar.gz", sources_file=home / "my-sources.txt")
    (home / "my-sources.txt").write_text("https://example.org/changed-since.m3u\n")
    _, safety = backup.restore(tmp_path / "b.tar.gz", home, tmp_path / "safety", lambda m: None)
    assert "my-sources.txt" in backup.read_manifest(safety)["files"]  # the restore can be undone
    assert (home / "my-sources.txt").read_text() == "https://example.org/mine.m3u\n"


def test_backup_and_restore_work_without_a_readable_kia_toml(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    home = kia_folder(tmp_path / "home")
    (home / "kia.toml").write_text("[check\n")  # broken
    assert cli.main(["backup", "-c", str(home / "kia.toml"), "-o", str(tmp_path / "b.tar.gz")]) == 0
    assert "kia.toml" in backup.read_manifest(tmp_path / "b.tar.gz")["files"]

    empty = tmp_path / "empty"
    empty.mkdir()  # no kia.toml at all: the restore brings it back
    assert cli.main(["restore", str(tmp_path / "b.tar.gz"), "-c", str(empty / "kia.toml")]) == 0
    assert (empty / "kia.toml").read_text() == "[check\n"


def test_restore_leaves_linked_certificates_alone(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    home = kia_folder(tmp_path / "home")
    (home / "tls").mkdir()
    (home / "tls" / "cert.pem").write_text("old certificate")
    assert cli.main(["backup", "-c", str(home / "kia.toml"), "-o", str(tmp_path / "b.tar.gz")]) == 0

    renewed = tmp_path / "letsencrypt" / "fullchain.pem"
    renewed.parent.mkdir()
    renewed.write_text("renewed certificate")
    (home / "tls" / "cert.pem").unlink()
    (home / "tls" / "cert.pem").symlink_to(renewed)
    said = []
    restored, _ = backup.restore(tmp_path / "b.tar.gz", home, tmp_path / "safety", said.append)
    assert "tls/cert.pem" not in restored and "state/channels.json" in restored
    assert (home / "tls" / "cert.pem").is_symlink() and renewed.read_text() == "renewed certificate"
    assert any("tls/cert.pem" in s and "link" in s for s in said)
