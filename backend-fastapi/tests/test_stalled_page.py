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

# The script is requested and never arrives. In the head it holds back the whole
# page; at the end of the body (where the-internet.herokuapp.com puts its own) the
# content is already on screen and a visitor could use it.
BLANK = b'<html><head><script src="/hang.js"></script></head><body>hi</body></html>'
USABLE = b'<html><body><input name="user">hi<script src="/hang.js"></script></body></html>'


async def _open(monkeypatch, page: bytes):  # noqa: ANN202
    release = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        if b"GET /hang.js" in head:
            await release.wait()  # the file never arrives while the page waits for it
        else:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n"
                + f"Content-Length: {len(page)}\r\n\r\n".encode()
                + page
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
        try:
            return session, await session.open(f"http://127.0.0.1:{port}/")
        except PlaywrightError as exc:
            return session, exc
    finally:
        release.set()
        await session.__aexit__(None, None, None)
        server.close()


async def test_a_page_stuck_on_a_file_names_the_file(monkeypatch) -> None:
    _, outcome = await _open(monkeypatch, BLANK)
    assert isinstance(outcome, PlaywrightError)
    message = str(outcome)
    assert "did not finish loading within 2 seconds" in message
    assert "still waiting for /hang.js" in message


async def test_a_slow_page_that_shows_its_content_is_still_tested(monkeypatch) -> None:
    session, outcome = await _open(monkeypatch, USABLE)
    assert not isinstance(outcome, PlaywrightError)
    assert session.site is not None  # the scan carries on from here
    assert len(session.slow_pages) == 1
    assert "still waiting for /hang.js" in session.slow_pages[0]
    assert "testing carried on" in session.slow_pages[0]
    assert session.log.of_kind("page-stalled")
