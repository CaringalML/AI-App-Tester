"""The egress proxy: the one way out for the scanner's browser.

The socket-level tests run anywhere. The browser test drives real Chromium and
is skipped where Playwright's browser is not installed (CI installs none).
"""

import asyncio
import socket

import pytest
from playwright.async_api import Error as PlaywrightError

from app.scanner.browser import BrowserSession
from app.scanner.egress import EgressProxy
from app.scanner.observations import ObservationLog
from app.security import TargetGuard


async def _origin(hits: list[bytes]):
    """A tiny HTTP server that records each request head and answers by path."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        hits.append(head)
        path = head.split(b" ", 2)[1].decode()
        port = writer.get_extra_info("sockname")[1]
        if path == "/redirect":
            reply = f"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{port}/secret\r\n"
            reply += "Content-Length: 0\r\nConnection: close\r\n\r\n"
            writer.write(reply.encode())
        elif path == "/linkcheck":
            reply = f"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{port}/secret-link\r\n"
            reply += "Content-Length: 0\r\nConnection: close\r\n\r\n"
            writer.write(reply.encode())
        else:
            if path == "/iframe":
                body = f'<h1>page</h1><iframe src="http://localhost:{port}/redirect"></iframe>'
            else:
                body = "<h1>hello</h1>"
            data = body.encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                + f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                + data
            )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _ask(proxy_url: str, request: str) -> bytes:
    port = int(proxy_url.rsplit(":", 1)[1])
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request.encode())
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.read(), timeout=5)
    finally:
        writer.close()


async def test_private_destinations_are_refused_without_being_contacted() -> None:
    hits: list[bytes] = []
    server, port = await _origin(hits)
    proxy = EgressProxy(allow_private=False)
    url = await proxy.start()
    try:
        plain = await _ask(url, f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\nHost: x\r\n\r\n")
        tunnel = await _ask(url, "CONNECT 169.254.170.2:443 HTTP/1.1\r\nHost: x\r\n\r\n")
    finally:
        await proxy.close()
        server.close()

    assert plain == b""  # closed with no response, so Chromium fails the request
    assert tunnel.startswith(b"HTTP/1.1 403")
    assert hits == []
    assert proxy.refused == ["127.0.0.1", "169.254.170.2"]
    assert proxy.is_refused("[169.254.170.2]")


async def test_allowed_traffic_is_forwarded_and_tunnelled() -> None:
    hits: list[bytes] = []
    server, port = await _origin(hits)
    proxy = EgressProxy(allow_private=True)
    url = await proxy.start()
    try:
        plain = await _ask(
            url,
            f"GET http://127.0.0.1:{port}/page?q=1 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            "Proxy-Connection: keep-alive\r\n\r\n",
        )
        # CONNECT opens a raw tunnel; whatever goes in comes out the other side.
        proxy_port = int(url.rsplit(":", 1)[1])
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\n".encode())
        established = await reader.readuntil(b"\r\n\r\n")
        writer.write(b"GET /tunnelled HTTP/1.1\r\nHost: x\r\n\r\n")
        tunnelled = await asyncio.wait_for(reader.read(), timeout=5)
        writer.close()
    finally:
        await proxy.close()
        server.close()

    assert plain.startswith(b"HTTP/1.1 200") and plain.endswith(b"<h1>hello</h1>")
    forwarded = hits[0].decode()
    assert forwarded.startswith("GET /page?q=1 HTTP/1.1\r\n")  # origin form, not absolute
    assert "Proxy-Connection" not in forwarded and "Connection: close" in forwarded
    assert established.startswith(b"HTTP/1.1 200")
    assert tunnelled.endswith(b"<h1>hello</h1>")
    assert hits[1].startswith(b"GET /tunnelled ")


async def test_each_host_is_resolved_once_and_every_address_must_be_public(monkeypatch) -> None:
    answers = {
        # A rebinding name: public on the first lookup, the metadata service after.
        "rebind.example": [["93.184.216.34"], ["169.254.170.2"]],
        "mixed.example": [["93.184.216.34", "10.0.0.5"]],
    }
    lookups: list[str] = []

    async def fake_getaddrinfo(host, port, **kwargs):  # noqa: ANN001, ANN202
        lookups.append(host)
        if host not in answers:
            raise socket.gaierror("no such host")
        addresses = answers[host].pop(0) if len(answers[host]) > 1 else answers[host][0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addresses]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    proxy = EgressProxy(allow_private=False)

    assert await proxy.addresses("rebind.example") == ["93.184.216.34"]
    # Asking again returns the address that was checked, not a fresh answer.
    assert await proxy.addresses("REBIND.example.") == ["93.184.216.34"]
    assert lookups.count("rebind.example") == 1

    assert await proxy.addresses("mixed.example") is None
    assert await proxy.addresses("metadata.google.internal") is None
    assert await proxy.addresses("gone.example") is None

    assert proxy.refused == ["mixed.example", "metadata.google.internal"]
    # A name that does not resolve is a failure to report, not a refusal.
    assert proxy.failures == {"gone.example": "net::ERR_NAME_NOT_RESOLVED"}


# ---- end to end, in Chromium ------------------------------------------------


class _LocalhostIsPublic(TargetGuard):
    """Plays the internet: 'localhost' is the public site, everything else is internal."""

    async def is_allowed_host(self, host: str | None) -> bool:
        return host == "localhost"


class _LocalhostEgress(EgressProxy):
    async def _allowed_addresses(self, host: str) -> list[str] | None:
        return ["127.0.0.1"] if host == "localhost" else None


async def test_redirects_frames_and_link_checks_cannot_reach_internal_addresses() -> None:
    hits: list[bytes] = []
    server, port = await _origin(hits)
    session = BrowserSession(
        _LocalhostIsPublic(), ObservationLog(), accessibility=False, axe_path="none"
    )
    session.egress = _LocalhostEgress()
    try:
        await session.__aenter__()
    except PlaywrightError as exc:
        server.close()
        pytest.skip(f"Chromium is not installed here: {str(exc).splitlines()[0]}")

    try:
        # A normal page loads through the proxy.
        assert await session.open(f"http://localhost:{port}/ok") == 200

        # 1. The page itself redirects inward.
        with pytest.raises(PlaywrightError, match="redirected to 127.0.0.1"):
            await session.open(f"http://localhost:{port}/redirect")

        # 2. A public page frames a redirector that points inward.
        await session.page.goto(f"http://localhost:{port}/iframe")
        await asyncio.sleep(1)

        # 3. The link checker meets a redirect that points inward.
        session.visited = []
        session.links = {f"http://localhost:{port}/linkcheck": f"http://localhost:{port}/ok"}
        await session.check_links(5)
    finally:
        await session.__aexit__(None, None, None)
        server.close()

    paths = [h.split(b" ", 2)[1].decode() for h in hits]
    assert "/secret" not in paths and "/secret-link" not in paths
    assert {"/ok", "/redirect", "/iframe", "/linkcheck"} <= set(paths)
    assert "127.0.0.1" in session.egress.refused
    # Refusals are recorded as the scanner's own safety rule, never as site defects.
    assert session.log.of_kind("blocked-request")
    assert not session.log.of_kind("request-failed")
