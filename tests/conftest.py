"""Settings for the whole test suite."""

import functools
import socketserver

import pytest

from kia import pluto


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """Test servers stop within 50 ms instead of half a second, and the Pluto relay
    retries without waiting a second."""
    original = socketserver.BaseServer.serve_forever
    monkeypatch.setattr(socketserver.BaseServer, "serve_forever",
                        functools.partialmethod(original, poll_interval=0.05))  # fmt: skip
    monkeypatch.setattr(pluto, "RETRY_DELAY", 0)
