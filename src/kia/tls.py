"""The certificate for the Dashboard's secure (HTTPS) port.

Playlists and guides stay on plain HTTP, because Kodi and most players refuse a
self-signed certificate. The Dashboard is on HTTPS too, and changes (profiles, the
password) are accepted only there.

The certificate is tls/fullchain.pem with its key, tls/privkey.pem. kia makes a
self-signed one when there's none, and renews it when it nears expiry or no longer names
the server's address. Your own certificate (e.g. from `tailscale cert`) goes in the same
two files; kia never replaces a certificate it didn't make. It recognizes its own by the
fingerprint it notes in tls/kia-generated.
"""

from __future__ import annotations


import ipaddress
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

CERT = "fullchain.pem"
KEY = "privkey.pem"
MARK = "kia-generated"
DAYS = 825  # the longest Apple devices accept
RENEW_BEFORE = timedelta(days=30)


class TlsError(Exception):
    pass


def _openssl(*args: str, data: bytes | None = None) -> str:
    if not shutil.which("openssl"):
        raise TlsError("openssl isn't installed, so kia can't make or read certificates")
    proc = subprocess.run(["openssl", *args], input=data, capture_output=True, timeout=60, check=False)
    if proc.returncode:
        raise TlsError(proc.stderr.decode(errors="replace").strip() or f"openssl {args[0]} failed")
    return proc.stdout.decode(errors="replace")


def host_of(url: str | None) -> str:
    host = re.sub(r"^\w+://", "", url or "").split("/")[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host.strip("[]").split("]")[0]


def normal(name: str) -> str:
    """A name the way kia compares them: lowercase, and an IP address in its short form.
    openssl writes an IPv6 address back in full and in capitals (FD00:0:0:0:0:0:0:5), so
    without this, kia would make a new certificate for fd00::5 at every start."""
    try:
        return str(ipaddress.ip_address(name))
    except ValueError:
        return name.lower()


def in_url(host: str) -> str:
    """A host as it goes in an address: an IPv6 address in brackets (https://[fd00::5]:8743/)."""
    return f"[{host}]" if ":" in host else host


def names(public_url: str | None, chosen: list[str] | None = None,
          extra: list[str] | None = None) -> list[str]:
    """The names the certificate should cover: the ones you chose (KIA_TLS_NAMES) or the
    public address's host, then any extra names (the front door's, so browsers trust it there
    too), plus this machine. The first is the one people should open, so extra names never
    change it."""
    out = [normal(n.strip()) for n in chosen or [] if n.strip()]
    if not out and (host := host_of(public_url)):
        out.append(normal(host))
    for name in extra or []:
        if (n := normal(name.strip())) and n not in out:
            out.append(n)
    for loopback in ("localhost", "127.0.0.1"):
        if loopback not in out:
            out.append(loopback)
    return out


def _san(name: str) -> str:
    try:
        ipaddress.ip_address(name)
        return f"IP:{name}"
    except ValueError:
        return f"DNS:{name}"


def fingerprint(cert: Path) -> str:
    return _openssl("x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256").strip().split("=", 1)[-1]


def make_self_signed(tls_dir: Path, hosts: list[str]) -> None:
    tls_dir.mkdir(parents=True, exist_ok=True)
    cert, key = tls_dir / (CERT + ".tmp"), tls_dir / (KEY + ".tmp")
    _openssl(
        "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
        "-days", str(DAYS), "-subj", f"/CN={hosts[0]}/O=kia",
        "-addext", "subjectAltName=" + ",".join(_san(h) for h in hosts),
        "-addext", "basicConstraints=critical,CA:FALSE",
        "-addext", "extendedKeyUsage=serverAuth",
        "-keyout", str(key), "-out", str(cert),
    )  # fmt: skip
    key.chmod(key_mode())
    # The mark names the new certificate before anything moves, and the old one until the new
    # one is in place: a renewal that stops part way leaves a certificate the mark still names,
    # so kia goes on treating it as its own (and remakes a key that doesn't match it).
    mark, new = tls_dir / MARK, fingerprint(cert)
    old = fingerprint(tls_dir / CERT) if (tls_dir / CERT).exists() else ""
    _write_mark(mark, [new, old] if old else [new])
    key.replace(tls_dir / KEY)
    cert.replace(tls_dir / CERT)
    _write_mark(mark, [new])


def _write_mark(mark: Path, fingerprints: list[str]) -> None:
    tmp = mark.with_name(mark.name + ".tmp")
    tmp.write_text("".join(f + "\n" for f in fingerprints))
    tmp.replace(mark)


def key_mode() -> int:
    """Only you can read kia's key; with Docker, your group too (compose sets KIA_TLS_SHARED),
    which the web server's container joins (group_add) so nginx can read it."""
    return 0o640 if os.environ.get("KIA_TLS_SHARED", "").lower() in ("1", "true", "yes") else 0o600


def tighten_key(tls_dir: Path, log=print) -> None:
    """A key from before 0.5.2 was readable by everyone on this computer (0644)."""
    key = tls_dir / KEY
    try:
        st = key.stat()
        mode = st.st_mode & 0o777
        # Only a key in kia's own group: nginx reads it through that group, and a key you
        # copied in with another group would become unreadable to it.
        if mode & 0o007 and st.st_uid == os.getuid() and st.st_gid == os.getgid():
            key.chmod(mode & key_mode() | 0o600)
            log(f"TLS: {key.name} is no longer readable by other users of this computer")
    except (OSError, AttributeError):  # no key yet, or no os.getuid (Windows)
        pass


def info(tls_dir: Path) -> dict | None:
    """What the certificate says, or None when there isn't one."""
    cert, key = tls_dir / CERT, tls_dir / KEY
    if not cert.exists():
        return None
    text = _openssl("x509", "-in", str(cert), "-noout", "-subject", "-issuer", "-enddate", "-ext", "subjectAltName")
    subject = re.search(r"^subject=\s*(.*)$", text, re.M)
    issuer = re.search(r"^issuer=\s*(.*)$", text, re.M)
    end = re.search(r"^notAfter=(.*)$", text, re.M)
    sans = [normal(n) for n in re.findall(r"(?:DNS|IP Address):([^,\s]+)", text)]
    expires = datetime.strptime(end.group(1).strip(), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc) if end else None
    try:
        mark = (tls_dir / MARK).read_text().strip()
    except OSError:
        mark = ""
    key_ok = None
    if key.exists():
        try:
            key_ok = _openssl("x509", "-in", str(cert), "-noout", "-pubkey") == _openssl("pkey", "-in", str(key), "-pubout")
        except TlsError:
            key_ok = False
    ours = fingerprint(cert) in mark.split()  # kia's own: one its mark names (two during a renewal)
    return {
        "subject": subject.group(1).strip() if subject else "",
        "issuer": issuer.group(1).strip() if issuer else "",
        "names": sans,
        "expires": expires.isoformat(timespec="seconds") if expires else None,
        "generated": ours,
        "key_matches": key_ok,
    }


def ensure(tls_dir: Path, hosts: list[str], log=print, now: datetime | None = None) -> dict | None:
    """Make or renew kia's own certificate when needed; leave anyone else's alone."""
    now = now or datetime.now(timezone.utc)
    tighten_key(tls_dir, log)
    try:
        current = info(tls_dir)
        if current and not current["generated"]:
            return current  # your own certificate
        why = None
        if not current:
            why = "made a certificate for the secure Dashboard"
        elif current["expires"] and datetime.fromisoformat(current["expires"]) - now < RENEW_BEFORE:
            why = "renewed the secure Dashboard's certificate, which was about to expire"
        elif not set(hosts) <= set(current["names"]):
            why = f"made a new certificate for the secure Dashboard, for {', '.join(hosts)}"
        elif current["key_matches"] is False:  # a renewal stopped between the key and the certificate
            why = "made a new certificate for the secure Dashboard, since its key didn't match"
        if why:
            make_self_signed(tls_dir, hosts)
            log(f"TLS: {why} (self-signed, valid {DAYS} days)")
        return info(tls_dir)
    except TlsError as exc:
        log(f"TLS: {exc}; the secure Dashboard won't work until this is fixed")
        return None
