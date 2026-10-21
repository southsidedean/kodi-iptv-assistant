"""The certificate for the status page's secure (HTTPS) port.

Playlists and guides stay on plain HTTP, because Kodi and most players refuse a
self-signed certificate. The status page is on HTTPS too, and changes (profiles, the
password) are accepted only there.

The certificate is tls/fullchain.pem with its key, tls/privkey.pem. kia makes a
self-signed one when there's none, and renews it when it nears expiry or no longer names
the server's address. Your own certificate (e.g. from `tailscale cert`) goes in the same
two files; kia never replaces a certificate it didn't make. It recognizes its own by the
fingerprint it notes in tls/kia-generated.
"""

from __future__ import annotations

import ipaddress
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


def names(public_url: str | None, chosen: list[str] | None = None) -> list[str]:
    """The names the certificate should cover: the ones you chose (KIA_TLS_NAMES) or the
    public address's host, plus this machine. The first is the one people should open."""
    out = [n.strip() for n in chosen or [] if n.strip()]
    if not out and (host := host_of(public_url)):
        out.append(host)
    for extra in ("localhost", "127.0.0.1"):
        if extra not in out:
            out.append(extra)
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
    key.chmod(0o644)  # nginx runs as another user and must read it; the folder isn't served
    key.replace(tls_dir / KEY)
    cert.replace(tls_dir / CERT)
    (tls_dir / MARK).write_text(fingerprint(tls_dir / CERT) + "\n")


def info(tls_dir: Path) -> dict | None:
    """What the certificate says, or None when there isn't one."""
    cert, key = tls_dir / CERT, tls_dir / KEY
    if not cert.exists():
        return None
    text = _openssl("x509", "-in", str(cert), "-noout", "-subject", "-issuer", "-enddate", "-ext", "subjectAltName")
    subject = re.search(r"^subject=\s*(.*)$", text, re.M)
    issuer = re.search(r"^issuer=\s*(.*)$", text, re.M)
    end = re.search(r"^notAfter=(.*)$", text, re.M)
    sans = re.findall(r"(?:DNS|IP Address):([^,\s]+)", text)
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
    return {
        "subject": subject.group(1).strip() if subject else "",
        "issuer": issuer.group(1).strip() if issuer else "",
        "names": sans,
        "expires": expires.isoformat(timespec="seconds") if expires else None,
        "generated": bool(mark) and mark == fingerprint(cert),
        "key_matches": key_ok,
    }


def ensure(tls_dir: Path, hosts: list[str], log=print, now: datetime | None = None) -> dict | None:
    """Make or renew kia's own certificate when needed; leave anyone else's alone."""
    now = now or datetime.now(timezone.utc)
    try:
        current = info(tls_dir)
        if current and not current["generated"]:
            return current  # your own certificate
        why = None
        if not current:
            why = "made a certificate for the secure status page"
        elif current["expires"] and datetime.fromisoformat(current["expires"]) - now < RENEW_BEFORE:
            why = "renewed the secure status page's certificate, which was about to expire"
        elif not set(hosts) <= set(current["names"]):
            why = f"made a new certificate for the secure status page, for {', '.join(hosts)}"
        if why:
            make_self_signed(tls_dir, hosts)
            log(f"TLS: {why} (self-signed, valid {DAYS} days)")
        return info(tls_dir)
    except TlsError as exc:
        log(f"TLS: {exc}; the secure status page won't work until this is fixed")
        return None
