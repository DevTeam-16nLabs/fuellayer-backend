import asyncio
import gzip
import socket
from email.message import Message

import pytest

from fuellayer.modules.recipe_imports import fetch


@pytest.mark.asyncio
async def test_dns_rejects_mixed_public_and_private(monkeypatch):
    async def answers(*args, **kwargs):
        return [(socket.AF_INET, 1, 6, "", (ip, 443)) for ip in ["93.184.216.34", "10.0.0.1"]]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", answers)
    with pytest.raises(fetch.ImportFailure, match="unsafe_url"):
        await fetch.resolve("recipe.example.com", 443)


@pytest.mark.asyncio
async def test_redirect_revalidated_before_any_private_socket(monkeypatch):
    calls = []

    async def resolve(*args):
        return "93.184.216.34"

    def read(url, address, deadline):
        calls.append(address)
        return 302, "http://169.254.169.254/latest/meta-data", ""

    monkeypatch.setattr(fetch, "resolve", resolve)
    monkeypatch.setattr(fetch, "read_address", read)
    with pytest.raises(fetch.ImportFailure, match="unsafe_url"):
        await fetch.fetch_page("https://example.com/recipe")
    assert calls == ["93.184.216.34"]


@pytest.mark.parametrize("compressed", [False, True])
def test_response_size_limits_and_pinned_socket(monkeypatch, compressed):
    address = "93.184.216.34"
    destinations = []

    class Sock:
        def getpeername(self):
            return (address, 80)

        def settimeout(self, value):
            pass

        def close(self):
            pass

    def connect(destination, **kwargs):
        destinations.append(destination)
        return Sock()

    class Response:
        status = 200
        headers = Message()
        data = (
            gzip.compress(b"a" * (fetch.MAX_BYTES + 1))
            if compressed
            else b"a" * (fetch.MAX_BYTES + 1)
        )

        def getheader(self, name, default=None):
            return {
                "Content-Type": "text/html",
                "Content-Encoding": "gzip" if compressed else "identity",
            }.get(name, default)

        def read1(self, size):
            value, self.data = self.data[:size], self.data[size:]
            return value

    class Connection:
        timeout = 5
        sock = None

        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            assert self.sock is not None

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(fetch.socket, "create_connection", connect)
    monkeypatch.setattr(fetch.http.client, "HTTPConnection", Connection)
    with pytest.raises(fetch.ImportFailure, match="source_too_large"):
        fetch.read_address("http://example.com/recipe", address, fetch.time.monotonic() + 20)
    assert destinations == [(address, 80)]
