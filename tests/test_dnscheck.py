import struct

from kia import dnscheck


def response(qid: int, rcode: int, answers: list[tuple[int, bytes]]) -> bytes:
    question = dnscheck.build_query("tv.example.com", dnscheck.A, qid)[12:]
    header = struct.pack(">HHHHHH", qid, 0x8180 | rcode, 1, len(answers), 0, 0)
    body = b""
    for rtype, rdata in answers:
        body += b"\xc0\x0c" + struct.pack(">HHIH", rtype, 1, 60, len(rdata)) + rdata
    return header + question + body


def test_query_encoding():
    q = dnscheck.build_query("tv.example.com", dnscheck.A, 0x1234)
    assert q[:2] == b"\x12\x34"
    assert q[12:] == b"\x02tv\x07example\x03com\x00\x00\x01\x00\x01"


def test_parse_cname_then_a():
    cname = b"\x03cdn\xc0\x0c"
    data = response(7, 0, [(5, cname), (dnscheck.A, bytes([18, 64, 183, 14]))])
    assert dnscheck.parse_response(data, 7) == (0, ["18.64.183.14"])


def test_parse_nxdomain_and_nodata():
    assert dnscheck.parse_response(response(9, 3, []), 9) == (3, [])
    assert dnscheck.parse_response(response(9, 0, []), 9) == (0, [])


def test_failure_detection_and_log_hosts():
    assert dnscheck.is_resolve_failure("Failed to resolve hostname tv.x.com: nodename nor servname provided")
    assert dnscheck.is_resolve_failure("ERR: getaddrinfo ENOTFOUND tv.jsrdn.com")
    assert not dnscheck.is_resolve_failure("Server returned 404 Not Found")
    log = "ERR: getaddrinfo ENOTFOUND tv.jsrdn.com\nERR: getaddrinfo ENOTFOUND tv.jsrdn.com\nERR: getaddrinfo EAI_AGAIN a.b.org"
    assert dnscheck.hosts_in_log(log) == {"tv.jsrdn.com", "a.b.org"}


def test_check_public_falls_back_and_classifies(monkeypatch):
    calls = []

    def fake_query(host, resolver, qtype=dnscheck.A, timeout=3.0):
        calls.append((resolver, qtype))
        if resolver == "8.8.8.8":
            raise OSError("unreachable")
        return (0, []) if qtype == dnscheck.A else (0, ["2606:4700::1"])

    monkeypatch.setattr(dnscheck, "query", fake_query)
    lk = dnscheck.check_public("v6only.example")
    assert lk.verdict == "resolves" and lk.addresses == ["2606:4700::1"]
    assert calls == [("8.8.8.8", 1), ("1.1.1.1", 1), ("1.1.1.1", 28)]


def test_broken_ipv6_upstream_is_not_blamed_on_local_dns(monkeypatch):
    # Like api.prod.sngtv.magentatv.de: IPv4 works, the site's nameserver sends a malformed
    # IPv6 answer that Google rejects (SERVFAIL) and Cloudflare tolerates.
    def fake_query(host, resolver, qtype=dnscheck.A, timeout=3.0):
        if qtype == dnscheck.A:
            return 0, ["80.157.246.218"]
        return (dnscheck.SERVFAIL, []) if resolver == "8.8.8.8" else (0, [])

    monkeypatch.setattr(dnscheck, "query", fake_query)
    assert dnscheck.check_public("broken.example").verdict == "broken-ipv6"

    monkeypatch.setattr(dnscheck, "query", lambda host, resolver, qtype=1, timeout=3.0: (0, ["1.2.3.4"] if qtype == 1 else []))
    assert dnscheck.check_public("fine.example").verdict == "resolves"


def test_summaries_for_every_verdict():
    for verdict, addrs in [("resolves", ["1.2.3.4"]), ("broken-ipv6", ["1.2.3.4"]), ("nxdomain", []), ("no-records", []), ("unreachable", [])]:
        assert "h.example" in dnscheck.PublicLookup("h.example", verdict, addrs).summary


def test_international_and_invalid_names():
    assert dnscheck.encode_name("münchen.de") == b"\x0exn--mnchen-3ya\x02de\x00"
    for bad in ("a..b", "x" * 64 + ".com"):
        assert dnscheck.check_public(bad).verdict == "invalid"  # no query sent, nobody blamed


def test_local_ipv4_answer_clears_local_dns(monkeypatch):
    # Public resolvers can miss the broken IPv6 answer (Google was too slow); if this
    # machine's DNS answers the IPv4 lookup, local DNS isn't what's failing.
    lookups = {h: dnscheck.PublicLookup(h, "resolves", ["1.2.3.4"]) for h in ("v6broken.example", "down.example")}
    monkeypatch.setattr(dnscheck, "ipv4_resolves_locally", lambda h: h == "v6broken.example")
    dnscheck.blame_local_dns_carefully(lookups)
    assert lookups["v6broken.example"].verdict == "broken-ipv6"
    assert lookups["down.example"].verdict == "resolves"  # still a local DNS problem
