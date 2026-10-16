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


def test_summaries_for_every_verdict():
    for verdict, addrs in [("resolves", ["1.2.3.4"]), ("nxdomain", []), ("no-records", []), ("unreachable", [])]:
        assert "h.example" in dnscheck.PublicLookup("h.example", verdict, addrs).summary
