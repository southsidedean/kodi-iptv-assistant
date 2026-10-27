"""The tests' own fence (conftest's network fixture): no test reaches the internet or the
real DNS, so none passes or fails with what a site or a router says that day."""

import socket
import urllib.request

import pytest


def test_a_name_doesnt_look_up_and_nothing_outside_connects():
    with pytest.raises(OSError):
        urllib.request.urlopen("http://example.com/", timeout=3)
    with pytest.raises(OSError):
        socket.create_connection(("192.0.2.1", 80), timeout=3)


def test_a_test_gives_its_own_names(hosts):
    hosts["guide.example"] = ["192.0.2.5"]
    assert {a[4][0] for a in socket.getaddrinfo("guide.example", 80)} == {"192.0.2.5"}
    assert socket.getaddrinfo("127.0.0.1", 80)  # addresses still read as themselves
