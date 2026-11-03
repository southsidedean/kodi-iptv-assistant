"""private/: kia's own password and tokens, kept apart from state/ (kia.files.private_file)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from kia import auth, files, mcp


def test_a_file_an_older_kia_kept_in_state_moves_to_private(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "mcp-token.txt").write_text("t" * 43 + "\n")
    os.chmod(state / "mcp-token.txt", 0o600)
    assert mcp.token(state) == "t" * 43
    moved = tmp_path / "private" / "mcp-token.txt"
    assert moved.read_text() == "t" * 43 + "\n" and not (state / "mcp-token.txt").exists()
    assert stat.S_IMODE(moved.stat().st_mode) == 0o600  # owner-only, like the original
    assert not [p for p in (tmp_path / "private").iterdir() if p.name.endswith(".tmp")]


def test_the_password_kia_made_still_works_after_the_move(tmp_path: Path):
    # Made in another kia folder, then put where an older kia kept them: straight in state/.
    elsewhere = tmp_path / "elsewhere" / "state"
    password = auth.Auth(elsewhere).reset(lambda m: None)
    state = tmp_path / "kia" / "state"
    state.mkdir(parents=True)
    for name in ("admin.json", "admin-password.txt"):
        (tmp_path / "elsewhere" / "private" / name).replace(state / name)
    a = auth.Auth(state)
    assert a.check(password) and a.source == "generated"
    private = tmp_path / "kia" / "private"
    assert a.shown == private / "admin-password.txt" and a.shown.read_text().strip() == password
    assert not (state / "admin.json").exists() and not (state / "admin-password.txt").exists()


def test_new_files_go_to_private(tmp_path: Path):
    state = tmp_path / "state"
    a = auth.Auth(state)
    a.ensure(lambda m: None)
    assert (tmp_path / "private" / "admin.json").exists() and (tmp_path / "private" / "admin-password.txt").exists()
    assert not state.exists() or not list(state.iterdir())
    mcp.make_token(state)
    assert (tmp_path / "private" / "mcp-token.txt").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write any folder")
def test_a_private_folder_kia_cant_write_leaves_the_files_in_state(tmp_path: Path):
    # Docker makes a bind folder that's missing as root: kia keeps working from state/.
    state, private = tmp_path / "state", tmp_path / "private"
    state.mkdir()
    private.mkdir(mode=0o500)
    try:
        (state / "mcp-token.txt").write_text("t" * 43 + "\n")
        assert not files.private_usable(state)
        assert mcp.token(state) == "t" * 43 and (state / "mcp-token.txt").exists()
        assert mcp.token_path(state) == state / "mcp-token.txt"
    finally:
        private.chmod(0o700)


def test_a_file_already_in_private_wins_over_one_left_in_state(tmp_path: Path):
    state, private = tmp_path / "state", tmp_path / "private"
    state.mkdir()
    private.mkdir()
    (private / "mcp-token.txt").write_text("new" * 15 + "\n")
    (state / "mcp-token.txt").write_text("old" * 15 + "\n")  # a restored old backup, say
    assert mcp.token(state) == "new" * 15


def test_another_kia_process_moving_it_first_isnt_a_missing_file(tmp_path: Path, monkeypatch):
    # Two kia processes start together; the other one moves the file between this one seeing
    # it in state/ and reading it. A missing file here would make the Dashboard a new password.
    state, private = tmp_path / "state", tmp_path / "private"
    state.mkdir()
    (state / "admin.json").write_text("{}")
    real = Path.read_bytes

    def moved_meanwhile(self):
        if self == state / "admin.json":
            private.mkdir(exist_ok=True)
            (private / "admin.json").write_text("{}")
            self.unlink()
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", moved_meanwhile)
    assert files.private_file(state, "admin.json") == private / "admin.json"


def test_backups_keep_private_but_not_the_mcp_token(tmp_path: Path):
    from kia import backup

    kia = tmp_path / "kia"
    auth.Auth(kia / "state").ensure(lambda m: None)
    mcp.make_token(kia / "state")
    names = backup.make(kia, tmp_path / "b.tar.gz")
    assert "private/admin.json" in names and "private/admin-password.txt" in names
    assert "private/mcp-token.txt" not in names
