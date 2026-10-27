"""scripts/frontdoor.sh: the front door's identity provider (Authelia). It writes Authelia's
settings and the one account, keeps every secret readable only by the owner, and asks Authelia's
own tool to hash passwords (kia never hashes one itself). docker is a stand-in that returns a
fake hash, so the test needs no real Authelia."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")


@pytest.fixture
def kia(tmp_path: Path) -> Path:
    """A kia folder with the script and an .env, and a docker stand-in that logs what it was
    asked and answers Authelia's hash command with a fake hash of the right kind."""
    folder = tmp_path / "kia"
    (folder / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "frontdoor.sh", folder / "scripts" / "frontdoor.sh")
    (folder / ".env").write_text("KIA_FRONTDOOR_HOST=kia.my-server.lan\n"
                                 "KIA_FRONTDOOR_AUTH_HOST=auth.my-server.lan\n")  # fmt: skip
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    (bin_dir / "docker").write_text(
        f'#!/bin/sh\necho "$*" >> {log}\n'
        'case "$*" in\n'
        '  *"crypto hash generate pbkdf2"*) echo "Digest: \\$pbkdf2-sha512\\$310000\\$fakesalt\\$fakeclienthash"; exit 0 ;;\n'
        '  *"crypto hash generate argon2"*) echo "Digest: \\$argon2id\\$v=19\\$fakesalt\\$fakepasswordhash"; exit 0 ;;\n'
        '  *" ps "*) [ -n "$FAKE_RUNNING" ] && echo 3715ea6ba64e; exit 0 ;;\n'
        '  *restart*) exit 0 ;;\n'
        'esac\nexit 0\n')  # fmt: skip
    (bin_dir / "docker").chmod(0o755)
    return folder


def frontdoor(kia: Path, *args: str, stdin: str = "", **env: str) -> subprocess.CompletedProcess:
    full = {"PATH": f"{kia.parent / 'bin'}:/usr/bin:/bin", "HOME": str(kia.parent), **env}
    return subprocess.run(["sh", "scripts/frontdoor.sh", *args], cwd=kia, env=full, input=stdin,
                          capture_output=True, text=True, check=False)  # fmt: skip


def test_write_makes_authelia_settings_and_keeps_every_secret_private(kia: Path):
    r = frontdoor(kia, "write")
    assert r.returncode == 0, r.stderr
    config = (kia / "frontdoor" / "configuration.yml").read_text()
    # The two host names and port reach the issuer, the login, and the redirect back.
    assert "authelia_url: 'https://auth.my-server.lan:8444'" in config
    assert "default_redirection_url: 'https://kia.my-server.lan:8444'" in config
    assert "- 'https://kia.my-server.lan:8444/oidc/callback'" in config
    assert "domain: 'my-server.lan'" in config  # the shared parent, so one cookie covers both
    # The first-party client, with the settings the gateway's login needs.
    assert "client_id: 'agentgateway'" in config
    assert "consent_mode: 'implicit'" in config
    assert "token_endpoint_auth_method: 'client_secret_post'" in config
    assert "id_token: ['groups', 'email', 'preferred_username', 'name']" in config
    assert "-----BEGIN" in config  # the signing key, inline
    # The client secret is stored only as the hash Authelia made; the plaintext the gateway
    # sends stays in its own file and never reaches the settings.
    assert "client_secret: '$pbkdf2-sha512$310000$fakesalt$fakeclienthash'" in config
    plaintext = (kia / "frontdoor" / "secrets" / "client-secret").read_text().strip()
    assert plaintext and plaintext not in config
    # Everything in frontdoor/ is readable only by the owner: the settings (they hold the
    # session, storage, hmac, and signing secrets inline) and every file in secrets/.
    assert (kia / "frontdoor" / "configuration.yml").stat().st_mode & 0o777 == 0o600
    for f in ("client-secret", "cookie-secret", "session-secret", "storage-key", "hmac-secret",
              "jwt-secret", "oidc.key", "client-secret.hash"):  # fmt: skip
        assert (kia / "frontdoor" / "secrets" / f).stat().st_mode & 0o777 == 0o600, f
    # An empty accounts file, so Authelia can start before anyone's account is set.
    assert (kia / "frontdoor" / "users_database.yml").read_text().strip().endswith("users: {}")


def test_writing_again_keeps_the_same_secrets(kia: Path):
    frontdoor(kia, "write")
    before = (kia / "frontdoor" / "secrets" / "client-secret").read_text()
    hash_before = (kia / "frontdoor" / "secrets" / "client-secret.hash").read_text()
    frontdoor(kia, "write")
    # Rotating these would log everyone out or stop the gateway, so a second write keeps them.
    assert (kia / "frontdoor" / "secrets" / "client-secret").read_text() == before
    assert (kia / "frontdoor" / "secrets" / "client-secret.hash").read_text() == hash_before


def test_write_needs_both_host_names(kia: Path):
    (kia / ".env").write_text("KIA_FRONTDOOR_HOST=kia.my-server.lan\n")  # no auth host
    r = frontdoor(kia, "write")
    assert r.returncode != 0 and "KIA_FRONTDOOR_AUTH_HOST" in r.stderr


def test_host_names_must_share_a_parent_domain(kia: Path):
    (kia / ".env").write_text("KIA_FRONTDOOR_HOST=kia.my-server.lan\n"
                              "KIA_FRONTDOOR_AUTH_HOST=kia.home.lan\n")  # a different parent domain
    r = frontdoor(kia, "write")
    assert r.returncode != 0 and "parent domain" in r.stderr


def test_a_bare_name_without_a_domain_is_refused(kia: Path):
    (kia / ".env").write_text("KIA_FRONTDOOR_HOST=kia\nKIA_FRONTDOOR_AUTH_HOST=auth\n")
    r = frontdoor(kia, "write")
    assert r.returncode != 0 and "parent domain" in r.stderr


def test_account_sets_a_hashed_password_and_never_keeps_the_plaintext(kia: Path):
    frontdoor(kia, "write")
    r = frontdoor(kia, "account", "dad", stdin="a-good-password\n")
    assert r.returncode == 0, r.stderr
    users = (kia / "frontdoor" / "users_database.yml").read_text()
    assert "\n  dad:" in users and "displayname: 'dad'" in users
    assert "password: '$argon2id$v=19$fakesalt$fakepasswordhash'" in users
    assert "email: 'dad@my-server.lan'" in users
    assert "- 'admins'" in users and "- 'household'" in users
    # The password itself is nowhere in the file (only the hash Authelia made).
    assert "a-good-password" not in users
    assert (kia / "frontdoor" / "users_database.yml").stat().st_mode & 0o777 == 0o600


def test_account_refuses_a_short_password(kia: Path):
    frontdoor(kia, "write")
    r = frontdoor(kia, "account", "dad", stdin="short\n")
    assert r.returncode != 0 and "at least 8" in r.stderr


def test_account_needs_the_settings_first(kia: Path):
    r = frontdoor(kia, "account", "dad", stdin="a-good-password\n")
    assert r.returncode != 0 and "frontdoor.sh write" in r.stderr


def test_list_shows_the_account(kia: Path):
    frontdoor(kia, "write")
    assert "No account" in frontdoor(kia, "list").stdout
    frontdoor(kia, "account", "dad", stdin="a-good-password\n")
    assert frontdoor(kia, "list").stdout.strip() == "dad"


def test_a_bad_login_name_is_refused(kia: Path):
    frontdoor(kia, "write")
    r = frontdoor(kia, "account", "Dad; rm -rf", stdin="a-good-password\n")
    assert r.returncode != 0 and "lowercase letters" in r.stderr
