"""Nothing in the repository describes a real home network: examples use stand-ins.

kia's address in examples is 192.168.1.20; other addresses come from the ranges kept for
documentation (192.0.2.x, 198.51.100.x, 203.0.113.x). Emails use example.com, and host
names the few placeholders below. Images can't be checked here: open every one by eye.
"""

import ipaddress
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

LAN_ADDRESSES = {"192.168.1.20", "172.17.0.1",  # kia's stand-in, and Docker's default bridge (the same everywhere)
                 "100.64.0.0", "100.101.102.103"}  # Tailscale's range, and the address its own docs use as an example
EMAIL_DOMAINS = re.compile(r"@example\.(com|org|net)$")
EMAILS = {"you@gmail.com"}  # the SMTP example in docs/alerts.md
HOST_NAMES = {"my-server.lan", "server.lan", "kia.lan", "kia.home.lan", "tv.lan", "host.docker.internal", "nas.local",
              "my-server.tail1234.ts.net"}  # fmt: skip
HOME_PATHS = {"/home/threadfin", "/Users/Shared"}  # Threadfin's folder in its container; every Mac's shared folder


def tracked_text() -> list[tuple[str, str]]:
    if not (ROOT / ".git").exists():
        pytest.skip("needs the git copy (a downloaded archive has no list of tracked files)")
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
    # is_global, not "not is_private": Tailscale's 100.64.0.0/10 is neither, and is someone's.
    return address.is_global or address.is_loopback or address.is_unspecified or text in LAN_ADDRESSES or (
        address in ipaddress.ip_network("192.0.2.0/24")
        or address in ipaddress.ip_network("198.51.100.0/24")
        or address in ipaddress.ip_network("203.0.113.0/24")
    )


ADDRESS_RE = r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)"


def test_the_address_pattern_finds_one_ending_a_sentence():
    found = lambda text: re.findall(ADDRESS_RE, text)
    assert found("Point Kodi at 192.0.2.5.") == ["192.0.2.5"] and found("http://192.0.2.7./x") == ["192.0.2.7"]
    assert found("version 1.2.3.4.5") == [] and found("192.0.2.5, then") == ["192.0.2.5"]


def test_no_real_network_addresses():
    # Not a fifth number (a version such as 1.2.3.4.5), but an address that ends a sentence counts.
    hits = findings(ADDRESS_RE, is_allowed_address)
    assert not hits, "use 192.168.1.20 (kia) or 192.0.2.x:\n" + "\n".join(hits)


def test_no_real_ipv6_addresses_on_a_home_network():
    # Local (fc00::/7) and link-local (fe80::/10) addresses. A real network's local addresses
    # start with a random 40-bit number after fd; fd00:: with none is a stand-in, like fd00::5.
    def stand_in(text: str) -> bool:
        try:
            address = ipaddress.ip_address(text)
        except ValueError:  # fd00::5:8743, a port written after it: the same address
            return text.lower().startswith("fd00::")
        return address in ipaddress.ip_network("fd00::/48") or text == "fe80::1"

    hits = findings(r"(?<![\w:])(?:f[cd][0-9a-f]{2}|fe[89ab][0-9a-f]):[0-9a-f:]*[0-9a-f]", stand_in)
    assert not hits, "use fd00::5:\n" + "\n".join(hits)


def test_no_real_email_addresses():
    hits = findings(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", lambda e: bool(EMAIL_DOMAINS.search(e)) or e in EMAILS)
    assert not hits, "use you@example.com:\n" + "\n".join(hits)


def test_no_real_host_names():
    # Not one followed by another dot: sources.local.txt is a file.
    hits = findings(r"[\w-]+(?:\.[\w-]+)*\.(?:lan|internal|localdomain|home\.arpa|local|home|ts\.net)\b(?!\.\w)",
                    lambda h: h.lower() in HOST_NAMES)  # fmt: skip
    assert not hits, "use my-server.lan or kia.home.lan:\n" + "\n".join(hits)


def test_no_home_folders():
    hits = findings(r"/(?:Users|home)/[\w.-]+", lambda p: p in HOME_PATHS)
    assert not hits, "use ~/kodi-iptv-assistant or /path/to/...:\n" + "\n".join(hits)
