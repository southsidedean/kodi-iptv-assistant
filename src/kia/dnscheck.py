"""Tell local DNS problems apart from hostnames that are really gone.

Everything else in kia resolves through the system's (local) DNS. Only when a lookup fails
there do we ask these backup resolvers, to verify whether the hostname really is gone.
"""

from __future__ import annotations

import random
import re
import socket
import struct
from dataclasses import dataclass

PUBLIC_RESOLVERS = ("8.8.8.8", "1.1.1.1")

# Messages ffmpeg / Node / glibc / macOS use when a hostname doesn't resolve.
_RESOLVE_FAILURE_RE = re.compile(
    r"Failed to resolve hostname|Name or service not known|nodename nor servname|"
    r"No address associated with hostname|Temporary failure in name resolution|ENOTFOUND|EAI_AGAIN",
    re.IGNORECASE,
)
_ENOTFOUND_HOST_RE = re.compile(r"(?:ENOTFOUND|EAI_AGAIN)\s+([A-Za-z0-9.-]+)")

A, AAAA = 1, 28
SERVFAIL = 2


def is_resolve_failure(message: str | None) -> bool:
    return bool(message and _RESOLVE_FAILURE_RE.search(message))


def hosts_in_log(text: str) -> set[str]:
    """Hostnames from Node's "getaddrinfo ENOTFOUND host" errors."""
    return set(_ENOTFOUND_HOST_RE.findall(text))


def build_query(host: str, qtype: int, qid: int) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # recursion desired, one question
    name = b"".join(bytes([len(p)]) + p.encode("idna") for p in host.rstrip(".").split(".")) + b"\0"
    return header + name + struct.pack(">HH", qtype, 1)


def _skip_name(data: bytes, pos: int) -> int:
    while True:
        length = data[pos]
        if length == 0:
            return pos + 1
        if length & 0xC0 == 0xC0:  # compression pointer
            return pos + 2
        pos += 1 + length


def parse_response(data: bytes, qid: int) -> tuple[int, list[str]]:
    """(rcode, addresses) from a DNS response; CNAMEs are skipped, A/AAAA kept."""
    rid, flags, qdcount, ancount = struct.unpack(">HHHH", data[:8])
    if rid != qid:
        raise ValueError("DNS response id mismatch")
    pos = 12
    for _ in range(qdcount):
        pos = _skip_name(data, pos) + 4
    addresses = []
    for _ in range(ancount):
        pos = _skip_name(data, pos)
        rtype, _cls, _ttl, rdlength = struct.unpack(">HHIH", data[pos : pos + 10])
        pos += 10
        rdata = data[pos : pos + rdlength]
        if rtype == A and rdlength == 4:
            addresses.append(socket.inet_ntop(socket.AF_INET, rdata))
        elif rtype == AAAA and rdlength == 16:
            addresses.append(socket.inet_ntop(socket.AF_INET6, rdata))
        pos += rdlength
    return flags & 0x000F, addresses


def query(host: str, resolver: str, qtype: int = A, timeout: float = 3.0) -> tuple[int, list[str]]:
    qid = random.randrange(1 << 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(build_query(host, qtype, qid), (resolver, 53))
        data, _ = sock.recvfrom(4096)
    return parse_response(data, qid)


@dataclass
class PublicLookup:
    host: str
    verdict: str  # "resolves" | "broken-ipv6" | "nxdomain" | "no-records" | "unreachable"
    addresses: list[str]

    @property
    def summary(self) -> str:
        if self.verdict == "resolves":
            return f"{self.host} resolves on public DNS ({self.addresses[0]}), so the local DNS is the problem"
        if self.verdict == "broken-ipv6":
            return (
                f"{self.host}: its own DNS servers send broken IPv6 (AAAA) answers, which public DNS can't "
                "resolve either; the site's problem, not your network's"
            )
        if self.verdict == "nxdomain":
            return f"{self.host} does not exist on public DNS either"
        if self.verdict == "no-records":
            return f"{self.host} has no address records on public DNS either"
        return f"{self.host}: public DNS servers unreachable, couldn't double-check"


_QUERY_ERRORS = (OSError, ValueError, struct.error, IndexError)


def check_public(host: str, resolvers: tuple[str, ...] = PUBLIC_RESOLVERS) -> PublicLookup:
    """Look host up on public resolvers (A, then AAAA), trying each resolver until one answers."""
    for resolver in resolvers:
        try:
            rcode, addrs = query(host, resolver, A)
            ipv4 = bool(addrs)
            if not addrs and rcode == 0:
                rcode, addrs = query(host, resolver, AAAA)
        except _QUERY_ERRORS:
            continue
        if not addrs:
            return PublicLookup(host, "nxdomain" if rcode == 3 else "no-records", [])
        if ipv4 and ipv6_fails_publicly(host, resolvers):
            return PublicLookup(host, "broken-ipv6", addrs)
        return PublicLookup(host, "resolves", addrs)
    return PublicLookup(host, "unreachable", [])


def ipv6_fails_publicly(host: str, resolvers: tuple[str, ...]) -> bool:
    """True if a public resolver also fails host's IPv6 lookup.

    Programs ask for IPv4 and IPv6 addresses together, and some (Alpine Linux, so kia's
    container) give up if either fails. When a site's own nameservers send a broken IPv6
    answer, local DNS correctly fails it, the lookup fails, and the IPv4 check alone would
    wrongly blame local DNS.
    """
    for resolver in resolvers:
        try:
            rcode, _ = query(host, resolver, AAAA)
        except _QUERY_ERRORS:
            continue
        if rcode == SERVFAIL:
            return True
    return False
