"""Tell local DNS problems apart from hostnames that are really gone.

Everything else in kia resolves through the system's (local) DNS. Only when a lookup fails
there do we ask these backup resolvers, to verify whether the hostname really is gone.
"""

from __future__ import annotations

import ipaddress
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


def encode_name(host: str) -> bytes:
    """A hostname in DNS wire format. International labels become xn-- (punycode) labels;
    raises ValueError for names no resolver could look up."""
    name = b""
    for label in host.rstrip(".").split("."):
        raw = label.encode("idna")  # also checks the label isn't empty or too long
        if not 0 < len(raw) <= 63:
            raise ValueError(f"bad DNS label {label!r}")
        name += bytes([len(raw)]) + raw
    return name + b"\0"


def build_query(host: str, qtype: int, qid: int) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # recursion desired, one question
    return header + encode_name(host) + struct.pack(">HH", qtype, 1)


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
    try:
        family = socket.AF_INET6 if ipaddress.ip_address(resolver).version == 6 else socket.AF_INET
    except ValueError:  # a name, not an address
        family = socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(build_query(host, qtype, qid), (resolver, 53))
        data, _ = sock.recvfrom(4096)
    return parse_response(data, qid)


@dataclass
class PublicLookup:
    host: str
    verdict: str  # "resolves" | "broken-ipv6" | "nxdomain" | "no-records" | "invalid" | "unreachable"
    addresses: list[str]

    @property
    def summary(self) -> str:
        if self.verdict == "resolves":
            return f"{self.host} resolves on public DNS ({self.addresses[0]}), so the local DNS is the problem"
        if self.verdict == "broken-ipv6":
            return (
                f"{self.host}: only its IPv6 (AAAA) lookup fails; its IPv4 address resolves here. Usually the "
                "site's own DNS servers send broken IPv6 answers, so it's not a problem with your network"
            )
        if self.verdict == "nxdomain":
            return f"{self.host} does not exist on public DNS either"
        if self.verdict == "no-records":
            return f"{self.host} has no address records on public DNS either"
        if self.verdict == "invalid":
            return f"{self.host} is not a valid hostname"
        return f"{self.host}: public DNS servers unreachable, couldn't double-check"


_QUERY_ERRORS = (OSError, ValueError, struct.error, IndexError)


def check_public(host: str, resolvers: tuple[str, ...] = PUBLIC_RESOLVERS) -> PublicLookup:
    """Look host up on public resolvers (A, then AAAA), trying each resolver until one answers."""
    try:
        encode_name(host)
    except ValueError:  # UnicodeError is a ValueError
        # Not a name any resolver can look up (an empty or overlong label, a bad
        # international name), so no DNS server is to blame.
        return PublicLookup(host, "invalid", [])
    for resolver in resolvers:
        try:
            rcode, addrs = query(host, resolver, A)
            ipv4 = bool(addrs)
            if not addrs and rcode == 0:
                rcode, addrs = query(host, resolver, AAAA)
        except _QUERY_ERRORS:
            continue
        if not addrs and rcode not in (0, 3):
            continue  # this resolver failed (SERVFAIL, refused): ask the next one
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
            # A resolver can take a few seconds to give up on a broken nameserver.
            rcode, _ = query(host, resolver, AAAA, timeout=8.0)
        except _QUERY_ERRORS:
            continue
        if rcode == SERVFAIL:
            return True
    return False


def ipv4_resolves_locally(host: str) -> bool:
    """True if this machine's DNS can look up host's IPv4 address on its own."""
    try:
        return bool(socket.getaddrinfo(host, None, socket.AF_INET))
    except (OSError, UnicodeError):
        return False


def blame_local_dns_carefully(lookups: dict[str, PublicLookup]) -> None:
    """Change "resolves" to "broken-ipv6" for hosts whose IPv4 address resolves here.

    If local DNS answers the IPv4 lookup, it's working; only the IPv6 half failed, which
    is how a site with a broken IPv6 nameserver shows up. Public resolvers don't always
    catch that in time (Google takes seconds to give up), so this is the reliable check.
    """
    for host, lookup in lookups.items():
        if lookup.verdict == "resolves" and ipv4_resolves_locally(host):
            lookups[host] = PublicLookup(host, "broken-ipv6", lookup.addresses)
