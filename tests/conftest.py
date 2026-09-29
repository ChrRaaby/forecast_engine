"""Tests are offline: any attempt to reach a non-local host fails the test (nostreambot practice, research/04).

Loopback stays allowed because asyncio on Windows builds its internal self-pipe from a localhost socket pair.
"""
import socket

import pytest

_LOCAL = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def guarded_connect(self, address, *args, **kwargs):
        if isinstance(address, tuple) and address[0] not in _LOCAL:
            raise RuntimeError(f"network access is not allowed in tests: {address}")
        return real_connect(self, address, *args, **kwargs)

    def guarded_create(address, *args, **kwargs):
        if address[0] not in _LOCAL:
            raise RuntimeError(f"network access is not allowed in tests: {address}")
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create)
