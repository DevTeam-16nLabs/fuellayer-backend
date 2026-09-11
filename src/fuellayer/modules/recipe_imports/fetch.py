"""Bounded public-URL fetches. DNS is checked once, then the actual socket is pinned."""

import asyncio
import http.client
import ipaddress
import re
import socket
import ssl
import time
import zlib
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_BYTES = 2 * 1024 * 1024


class ImportFailure(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        self.code, self.message, self.retryable = code, message, retryable
        super().__init__(code)


def normalize_url(value: str) -> str:
    try:
        if len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
            raise ValueError
        url = urlsplit(value)
        host = (url.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        if url.scheme not in ("http", "https") or not host or url.username or url.password:
            raise ValueError
        port = url.port or (443 if url.scheme == "https" else 80)
        if port != (443 if url.scheme == "https" else 80) or "%" in host:
            raise ValueError
        if host == "localhost" or host.endswith(
            (".localhost", ".local", ".internal", ".home", ".lan")
        ):
            raise ValueError
        try:
            public_address(host)
        except ValueError:
            # Hostnames must be unambiguous; reject legacy numeric address forms.
            if ":" in host or re.fullmatch(r"[0-9.xXa-fA-F]+", host) or "." not in host:
                raise ValueError from None
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                raise ValueError from None
        authority = f"[{host}]" if ":" in host else host
        return urlunsplit(
            (
                url.scheme,
                authority,
                quote(url.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                quote(url.query, safe="%:@!$&'()*+,;=/?-._~"),
                "",
            )
        )
    except (ValueError, UnicodeError):
        raise ImportFailure("unsafe_url", "Use a public http or https recipe link.") from None


def public_address(value: str) -> str:
    address = ipaddress.ip_address(value)
    mapped = getattr(address, "ipv4_mapped", None)
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or (mapped and not mapped.is_global)
    ):
        raise ValueError("nonpublic")
    return str(address)


async def resolve(host: str, port: int) -> str:
    try:
        answers = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), 5
        )
        addresses = {public_address(str(answer[4][0])) for answer in answers}
        if not addresses:
            raise ValueError
        return sorted(addresses)[0]
    except ValueError:
        raise ImportFailure("unsafe_url", "This link points to a non-public destination.") from None
    except (OSError, TimeoutError):
        raise ImportFailure(
            "source_unavailable", "The recipe website could not be reached.", True
        ) from None


@dataclass
class Page:
    url: str
    text: str


def read_address(url: str, address: str, deadline: float) -> tuple[int, str | None, str]:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    port = 443 if parsed.scheme == "https" else 80
    connection = http.client.HTTPConnection(
        host, port, timeout=min(5, max(0.1, deadline - time.monotonic()))
    )
    try:
        # Never let HTTPConnection resolve the hostname after our validation.
        sock = socket.create_connection((address, port), timeout=connection.timeout)
        connection.sock = sock
        if public_address(sock.getpeername()[0]) != address:
            sock.close()
            raise ImportFailure("unsafe_url", "The source connection could not be verified.")
        if parsed.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        connection.sock = sock
        connection.request(
            "GET",
            urlunsplit(("", "", parsed.path or "/", parsed.query, "")),
            headers={
                "User-Agent": "FuelLayerRecipeImporter/1.0",
                "Accept": "text/html,text/plain",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        if response.status in (301, 302, 303, 307, 308):
            return response.status, response.getheader("Location"), ""
        if response.status != 200:
            raise ImportFailure(
                "source_unavailable",
                "This page is unavailable or requires a login.",
                response.status in (429, 500, 502, 503, 504),
            )
        if response.getheader("Content-Type", "").split(";")[0].strip().lower() not in (
            "text/html",
            "application/xhtml+xml",
            "text/plain",
        ):
            raise ImportFailure(
                "unsupported_content",
                "Paste recipe text instead. This source is not an HTML recipe page.",
            )
        length = response.getheader("Content-Length")
        if length and (not length.isdigit() or int(length) > MAX_BYTES):
            raise ImportFailure(
                "source_too_large", "This page is too large. Paste the recipe text instead."
            )
        encoding = response.getheader("Content-Encoding", "identity").lower()
        if encoding not in ("identity", "gzip", "deflate"):
            raise ImportFailure(
                "unsupported_content", "This page encoding is unsupported. Paste the recipe text."
            )
        decoder = (
            zlib.decompressobj(31 if encoding == "gzip" else 15) if encoding != "identity" else None
        )
        body = bytearray()
        received = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            sock.settimeout(min(5, remaining))
            chunk = response.read1(65536)
            if not chunk:
                break
            received += len(chunk)
            if received > MAX_BYTES:
                raise ImportFailure(
                    "source_too_large", "This page is too large. Paste recipe text."
                )
            body.extend(decoder.decompress(chunk, MAX_BYTES + 1 - len(body)) if decoder else chunk)
            if len(body) > MAX_BYTES or (decoder and decoder.unconsumed_tail):
                raise ImportFailure(
                    "source_too_large", "This page is too large. Paste recipe text."
                )
        if decoder and not decoder.eof:
            raise ImportFailure("source_unavailable", "The source response was incomplete.", True)
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            return 200, None, body.decode(charset, errors="replace")
        except LookupError:
            return 200, None, body.decode("utf-8", errors="replace")
    finally:
        connection.close()


async def fetch_page(value: str) -> Page:
    url = normalize_url(value)
    deadline = time.monotonic() + 20
    try:
        async with asyncio.timeout(20):
            for hop in range(4):
                parsed = urlsplit(url)
                address = await resolve(
                    parsed.hostname or "", 443 if parsed.scheme == "https" else 80
                )
                status, location, content = await asyncio.to_thread(
                    read_address, url, address, deadline
                )
                if status == 200:
                    return Page(url, content)
                if not location or hop == 3:
                    raise ImportFailure(
                        "unsafe_redirect", "This link redirects too many times. Paste recipe text."
                    )
                url = normalize_url(urljoin(url, location))
    except ImportFailure:
        raise
    except (OSError, TimeoutError, http.client.HTTPException, zlib.error):
        raise ImportFailure(
            "timeout", "The source could not be read in time. Try again or paste text.", True
        ) from None
    raise ImportFailure("source_unavailable", "The recipe page could not be read.")
