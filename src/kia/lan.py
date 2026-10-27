"""Keeping playlists from the internet off your own network.

kia tests every stream in a playlist, so a playlist from the internet could have kia send
requests to the devices around it: a router's settings page, a printer, or the other
containers of kia's stack (Prometheus, Plex), which Docker lets it reach by name. So a
stream in such a playlist is tested only when its address is out on the internet.

Your own playlists can name anything: a file on this computer, or a playlist downloaded
from an address on your network (a tuner, Threadfin, tvheadend). The word `lan` on a
playlist's line in the sources file lets one from elsewhere do the same.

This check comes first, and gives the clearest reason in the report. On its own, someone
determined could get past it: kia looks each name up before the test and ffprobe looks it
up again, and ffprobe follows redirects and HLS pieces by itself. So the http and https
streams and guides of such a playlist also go through kia.guard, which checks every
connection they make. Whether a playlist is yours, though, is decided from its address as
written, never by a lookup (names_your_network).
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

# IPv6 addresses that carry an IPv4 one inside: NAT64 (64:ff9b::192.168.1.20 reaches that
# address on a network with NAT64) and 6to4. Python calls both global, whatever they carry.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_COMPATIBLE = ipaddress.ip_network("::/96")  # IPv4-compatible, ::192.168.1.20 (long deprecated, still parsed)


def host_of(url: str) -> str:
    """The host in an address, or "" when it has none or Python can't read it. Its parser
    raises on some ("http://[x/a", "http://[192.168.1.20]/a"), and one such line in a
    stranger's playlist mustn't stop a whole run."""
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def _addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return []  # a name that doesn't look up: the test fails on its own
    found = []
    for *_, sockaddr in infos:
        try:
            found.append(ipaddress.ip_address(str(sockaddr[0]).partition("%")[0]))  # fe80::1%eth0
        except ValueError:
            continue
    return found


def _not_global(a: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not a.is_global or a.is_multicast:  # multicast: a stream on your own network (IPTV from a router, say)
        return True
    if a.version == 6:
        # An IPv4 address inside: NAT64's, 6to4's, or the old IPv4-compatible form (::a.b.c.d).
        inside = ipaddress.IPv4Address(int(a) & 0xFFFFFFFF) if a in _NAT64 or a in _COMPATIBLE else a.sixtofour
        return inside is not None and not inside.is_global
    return False


def is_local_address(address: str) -> bool:
    """Whether an IP address, as a lookup gives it, isn't out on the internet (as _not_global
    judges it). Anything that doesn't read as an address counts as local: refused."""
    try:
        return _not_global(ipaddress.ip_address(address.partition("%")[0]))  # fe80::1%eth0
    except ValueError:
        return True


def is_local(host: str | None) -> bool:
    """Whether host is, or looks up to, an address that isn't out on the internet: this
    computer, your network, Docker's networks, link-local, or reserved addresses, or an
    IPv6 address carrying one of those (NAT64, 6to4). One such address is enough, since a
    connection could use any of them."""
    host = (host or "").strip("[]")
    return bool(host) and any(_not_global(a) for a in _addresses(host))


# Names that only make sense on your own network: one word (nas, threadfin, a container),
# localhost, and the endings home routers and Docker use.
_HOME_NAME_RE = re.compile(r"(?i)[a-z0-9-]+|localhost|.+\.(lan|local|home|home\.arpa|internal|localdomain)")


def names_your_network(host: str | None) -> bool:
    """Whether a playlist's own address says it's on your network, judged from the address
    as written: an IP address that isn't on the internet, or a name only your network has.
    Not by looking the name up: whoever runs a name's DNS could answer with a local address
    once, and every stream in their playlist would then be allowed onto your network."""
    host = (host or "").strip("[]")
    try:
        return _not_global(ipaddress.ip_address(host))
    except ValueError:
        return bool(_HOME_NAME_RE.fullmatch(host))
