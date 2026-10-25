"""Keeping playlists from the internet off your own network.

kia tests every stream in a playlist, so a playlist from the internet could have kia send
requests to the devices around it: a router's settings page, a printer, or the other
containers of kia's stack (Prometheus, Plex), which Docker lets it reach by name. So a
stream in such a playlist is tested only when its address is out on the internet.

Your own playlists can name anything: a file on this computer, or a playlist downloaded
from an address on your network (a tuner, Threadfin, tvheadend). The word `lan` on a
playlist's line in the sources file lets one from elsewhere do the same.

It's a guard for the ordinary case, and someone determined can get past it: kia looks
each name up before the test and ffprobe looks it up again, so a DNS server that answers
differently the second time gets through, and so does a stream that redirects. SECURITY.md
says so too.
"""

from __future__ import annotations

import ipaddress
import socket


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


def is_local(host: str | None) -> bool:
    """Whether host is, or looks up to, an address that isn't out on the internet: this
    computer, your network, Docker's networks, link-local, or reserved addresses. One such
    address is enough, since a connection could use any of them."""
    host = (host or "").strip("[]")
    return bool(host) and any(not a.is_global for a in _addresses(host))
