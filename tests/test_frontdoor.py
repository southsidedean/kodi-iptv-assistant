"""scripts/frontdoor.sh: the front door's identity provider (Authelia) and account store (LLDAP).
It writes Authelia's settings and manages the household's accounts in LLDAP, asking Authelia's own
tool only to hash the OIDC client secret (kia never hashes a password itself).

The real account work runs GraphQL and LLDAP's OPAQUE password tool inside the lldap container;
that path is exercised against a live LLDAP in the kia-box end-to-end test, not here. These tests
cover the host side: the command surface, the checks, the messages, and the rule that a password
reaches the store only on stdin, never on a command line. The docker stand-in stands in for both
Authelia's hash tool and the lldap container, keeping a small account store in a file so list,
set-admin, and remove have something to act on."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")


@pytest.fixture
def kia(tmp_path: Path) -> Path:
    """A kia folder with the script and an .env, and a docker stand-in. The stand-in answers
    Authelia's hash command with a fake hash, reports Authelia and LLDAP as running, and, for a
    call into the lldap container, reads the account protocol from stdin and keeps the accounts in
    a file (NAME:admin or NAME:member), so the account commands have a store to work against."""
    folder = tmp_path / "kia"
    (folder / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "frontdoor.sh", folder / "scripts" / "frontdoor.sh")
    shutil.copy(ROOT / "scripts" / "frontdoor-lldap.sh", folder / "scripts" / "frontdoor-lldap.sh")
    (folder / ".env").write_text("KIA_FRONTDOOR_HOST=kia.my-server.lan\n"
                                 "KIA_FRONTDOOR_AUTH_HOST=auth.my-server.lan\n")  # fmt: skip
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    store = tmp_path / "lldap-users"
    (bin_dir / "docker").write_text(
        f'#!/bin/sh\necho "$*" >> {log}\n'
        'case "$*" in\n'
        '  *"crypto hash generate pbkdf2"*) echo "Digest: \\$pbkdf2-sha512\\$310000\\$fakesalt\\$fakeclienthash"; exit 0 ;;\n'
        '  *"ps -q --status running authelia"*) [ -n "$FAKE_RUNNING" ] && echo 3715ea6ba64e; exit 0 ;;\n'
        '  *"ps -q --status running lldap"*) [ -n "$FAKE_LLDAP" ] && echo a1b2c3d4e5f6; exit 0 ;;\n'
        '  *restart*) exit 0 ;;\n'
        '  *"exec -T lldap"*)\n'
        '    IFS= read -r op; IFS= read -r adminpw; IFS= read -r uname\n'
        '    IFS= read -r email; IFS= read -r admin; IFS= read -r newpw\n'
        f'    store={store}; touch "$store"\n'
        '    case "$op" in\n'
        '      list) while IFS=: read -r n a; do [ "$a" = admin ] && echo "$n (admin)" || echo "$n"; done < "$store" ;;\n'
        '      create)\n'
        '        grep -v "^$uname:" "$store" > "$store.t" 2>/dev/null || :; mv "$store.t" "$store"\n'
        '        [ "$admin" = yes ] && echo "$uname:admin" >> "$store" || echo "$uname:member" >> "$store" ;;\n'
        '      set-admin)\n'
        '        grep -q "^$uname:" "$store" || { echo no-such-user >&2; exit 4; }\n'
        '        grep -v "^$uname:" "$store" > "$store.t" || :; mv "$store.t" "$store"\n'
        '        [ "$admin" = yes ] && echo "$uname:admin" >> "$store" || echo "$uname:member" >> "$store" ;;\n'
        '      remove)\n'
        '        grep -q "^$uname:" "$store" || { echo no-such-user >&2; exit 4; }\n'
        '        grep -v "^$uname:" "$store" > "$store.t" || :; mv "$store.t" "$store" ;;\n'
        '    esac\n'
        '    exit 0 ;;\n'
        'esac\nexit 0\n')  # fmt: skip
    (bin_dir / "docker").chmod(0o755)
    return folder


def frontdoor(kia: Path, *args: str, stdin: str = "", **env: str) -> subprocess.CompletedProcess:
    # The stack's services run while the account commands do, so the stand-in reports Authelia and
    # LLDAP as up by default; a test turns one off by passing it empty.
    full = {"PATH": f"{kia.parent / 'bin'}:/usr/bin:/bin", "HOME": str(kia.parent),
            "FAKE_RUNNING": "1", "FAKE_LLDAP": "1", **env}  # fmt: skip
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
    # Authelia checks passwords against LLDAP over LDAP (the accounts live there, not in a file).
    assert "implementation: 'lldap'" in config
    assert "address: 'ldap://lldap:3890'" in config
    assert "base_dn: 'dc=kia,dc=lan'" in config
    assert "user: 'uid=admin,ou=people,dc=kia,dc=lan'" in config
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
              "jwt-secret", "oidc.key", "client-secret.hash",
              "lldap-jwt-secret", "lldap-key-seed", "lldap-admin-pass"):  # fmt: skip
        assert (kia / "frontdoor" / "secrets" / f).stat().st_mode & 0o777 == 0o600, f
    # The account store's admin password, in an env file compose loads for it; the password
    # itself is not printed anywhere, only its value here.
    lldap_env = (kia / "frontdoor" / "lldap.env").read_text()
    assert "LLDAP_LDAP_USER_PASS=" in lldap_env and "LLDAP_LDAP_USER_EMAIL=admin@kia.lan" in lldap_env
    # The gateway's OIDC cookie key is a 64-character hex AES-256 key (what agentgateway wants),
    # not the base64 the others use.
    cookie = (kia / "frontdoor" / "secrets" / "cookie-secret").read_text().strip()
    assert len(cookie) == 64 and all(c in "0123456789abcdef" for c in cookie), cookie


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


def test_account_adds_an_admin_and_never_puts_the_password_on_a_command_line(kia: Path):
    frontdoor(kia, "write")
    r = frontdoor(kia, "account", "dad", stdin="a-good-password\n")
    assert r.returncode == 0, r.stderr
    # An account is an admin by default, so the first one can run the admin pages.
    assert frontdoor(kia, "list").stdout.strip() == "dad (admin)"
    # The password reached the store on stdin, so it must not appear in anything docker was asked
    # to run (its command line shows in a process list on the host).
    assert "a-good-password" not in (kia.parent / "docker.log").read_text()


def test_account_no_admin_makes_a_household_member(kia: Path):
    frontdoor(kia, "write")
    r = frontdoor(kia, "account", "kid", "--no-admin", stdin="a-good-password\n")
    assert r.returncode == 0, r.stderr
    assert frontdoor(kia, "list").stdout.strip() == "kid"  # no admin mark


def test_set_admin_toggles_the_role(kia: Path):
    frontdoor(kia, "write")
    frontdoor(kia, "account", "dad", "--no-admin", stdin="a-good-password\n")
    assert frontdoor(kia, "set-admin", "dad", "on").returncode == 0
    assert frontdoor(kia, "list").stdout.strip() == "dad (admin)"
    assert frontdoor(kia, "set-admin", "dad", "off").returncode == 0
    assert frontdoor(kia, "list").stdout.strip() == "dad"


def test_set_admin_needs_on_or_off(kia: Path):
    frontdoor(kia, "write")
    frontdoor(kia, "account", "dad", stdin="a-good-password\n")
    r = frontdoor(kia, "set-admin", "dad", "maybe")
    assert r.returncode != 0 and "on or off" in r.stderr


def test_set_admin_on_a_missing_account_fails(kia: Path):
    frontdoor(kia, "write")
    r = frontdoor(kia, "set-admin", "nobody", "on")
    assert r.returncode != 0 and "nobody" in r.stderr


def test_remove_deletes_the_account(kia: Path):
    frontdoor(kia, "write")
    frontdoor(kia, "account", "dad", stdin="a-good-password\n")
    assert frontdoor(kia, "remove", "dad").returncode == 0
    assert "No account" in frontdoor(kia, "list").stdout
    # Removing an account that isn't there says so instead of pretending it worked.
    assert frontdoor(kia, "remove", "dad").returncode != 0


def test_account_refuses_a_short_password(kia: Path):
    frontdoor(kia, "write")
    r = frontdoor(kia, "account", "dad", stdin="short\n")
    assert r.returncode != 0 and "at least 8" in r.stderr


def test_account_needs_the_settings_first(kia: Path):
    r = frontdoor(kia, "account", "dad", stdin="a-good-password\n")
    assert r.returncode != 0 and "frontdoor.sh write" in r.stderr


def test_account_needs_the_store_running(kia: Path):
    frontdoor(kia, "write")
    r = frontdoor(kia, "account", "dad", stdin="a-good-password\n", FAKE_LLDAP="")
    assert r.returncode != 0 and "account store isn't running" in r.stderr


def test_list_shows_the_accounts(kia: Path):
    frontdoor(kia, "write")
    assert "No account" in frontdoor(kia, "list").stdout
    frontdoor(kia, "account", "dad", stdin="a-good-password\n")
    assert frontdoor(kia, "list").stdout.strip() == "dad (admin)"


def test_a_bad_login_name_is_refused(kia: Path):
    frontdoor(kia, "write")
    r = frontdoor(kia, "account", "Dad; rm -rf", stdin="a-good-password\n")
    assert r.returncode != 0 and "lowercase letters" in r.stderr
