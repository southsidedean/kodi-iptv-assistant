"""Nothing in the repository describes a real home network: examples use stand-ins.

kia's address in examples is 192.168.1.20; other addresses come from the ranges kept for
documentation (192.0.2.x, 198.51.100.x, 203.0.113.x). Emails use example.com, and host
names the few placeholders below. Images can't be checked here: open every one by eye.
"""

import ipaddress
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LAN_ADDRESSES = {"192.168.1.20", "172.17.0.1"}  # kia's stand-in, and Docker's default bridge (the same everywhere)
EMAIL_DOMAINS = re.compile(r"@example\.(com|org|net)$")
EMAILS = {"you@gmail.com"}  # the SMTP example in docs/alerts.md
HOST_NAMES = {"my-server.lan", "server.lan", "kia.lan", "kia.home.lan", "tv.lan", "host.docker.internal"}
HOME_PATHS = {"/home/threadfin"}  # Threadfin's folder inside its container


def tracked_text() -> list[tuple[str, str]]:
    names = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    files = []
    for name in names:
        try:
            files.append((name, (ROOT / name).read_text()))
        except (UnicodeDecodeError, OSError):  # images and other binary files
            continue
    return files


def findings(pattern: str, allowed) -> list[str]:
    hits = []
    for name, text in tracked_text():
        for n, line in enumerate(text.splitlines(), 1):
            for match in re.finditer(pattern, line, flags=re.I):
                if not allowed(match.group(0)):
                    hits.append(f"{name}:{n}: {match.group(0)}")
    return hits


def is_allowed_address(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:  # a version number or the like
        return True
    return not address.is_private or address.is_loopback or address.is_unspecified or text in LAN_ADDRESSES or (
        address in ipaddress.ip_network("192.0.2.0/24")
        or address in ipaddress.ip_network("198.51.100.0/24")
        or address in ipaddress.ip_network("203.0.113.0/24")
    )


def test_no_real_network_addresses():
    hits = findings(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", is_allowed_address)
    assert not hits, "use 192.168.1.20 (kia) or 192.0.2.x:\n" + "\n".join(hits)


def test_no_real_email_addresses():
    hits = findings(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", lambda e: bool(EMAIL_DOMAINS.search(e)) or e in EMAILS)
    assert not hits, "use you@example.com:\n" + "\n".join(hits)


def test_no_real_host_names():
    hits = findings(r"[\w-]+(?:\.[\w-]+)*\.(?:lan|internal|localdomain|home\.arpa)\b", lambda h: h.lower() in HOST_NAMES)
    assert not hits, "use my-server.lan or kia.home.lan:\n" + "\n".join(hits)


def test_no_home_folders():
    hits = findings(r"/(?:Users|home)/[\w.-]+", lambda p: p in HOME_PATHS)
    assert not hits, "use ~/kodi-iptv-assistant or /path/to/...:\n" + "\n".join(hits)
