"""A page that never finishes loading is explained, not reported as a bare timeout.

Runs in real Chromium; skipped where Playwright's browser is not installed.
"""

import asyncio

import pytest
from playwright.async_api import Error as PlaywrightError

from app.scanner import browser
from app.scanner.browser import BrowserSession
from app.scanner.observations import ObservationLog
from app.security import TargetGuard


async def test_a_page_stuck_on_a_file_names_the_file(monkeypatch) -> None:
    release = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        if b"GET /hang.js" in head:
            await release.wait()  # the file never arrives while the page waits for it
        else:
            body = b'<html><head><script src="/hang.js"></script></head><body>hi</body></html>'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(browser, "NAV_TIMEOUT_MS", 2_000)
    session = BrowserSession(
        TargetGuard(allow_private=True), ObservationLog(), accessibility=False, axe_path="none"
    )
    try:
        await session.__aenter__()
    except PlaywrightError as exc:
        server.close()
        pytest.skip(f"Chromium is not installed here: {str(exc).splitlines()[0]}")
    try:
        with pytest.raises(PlaywrightError) as caught:
            await session.open(f"http://127.0.0.1:{port}/")
    finally:
        release.set()
        await session.__aexit__(None, None, None)
        server.close()

    message = str(caught.value)
    assert "did not finish loading within 2 seconds" in message
    assert "still waiting for /hang.js" in message
