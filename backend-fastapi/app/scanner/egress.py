"""The only way out for the scanner's browser.

Route interception (`BrowserSession._guard_route`) sees only the first URL of a
request. Chromium follows redirects by itself, loads a frame's redirect target
by itself, and opens WebSockets outside routing, so none of those pass that
check. A public page that redirects to 169.254.170.2 would otherwise be opened,
rendered and screenshotted.

So Chromium is launched with this local forward proxy, and every connection it
makes arrives here first. Each destination is resolved once per scan, refused
unless every address is public, and connected to by the exact address that was
checked. A redirect, a frame, a WebSocket or a DNS answer that changes between
the check and the connection (rebinding) cannot reach a private address.

HTTPS arrives as CONNECT and is tunnelled byte for byte, so Chromium still does
its own TLS end to end; plain HTTP is forwarded one request per connection.
"""

import asyncio
import contextlib
import logging
import socket
from urllib.parse import urlsplit

from ..security import BLOCKED_HOSTNAMES, is_ip_literal, is_public_address

log = logging.getLogger(__name__)

_HEAD_TIMEOUT = 30
_CONNECT_TIMEOUT = 15
_HOP_BY_HOP = (b"proxy-connection:", b"connection:", b"keep-alive:", b"proxy-authorization:")


class _Refused(Exception):
    """The destination is private, reserved or otherwise off limits."""


class _Unreachable(Exception):
    """The destination is allowed but could not be reached."""


class EgressProxy:
    def __init__(self, allow_private: bool = False) -> None:
        self.allow_private = allow_private
        # Hosts refused, in order, and why allowed hosts failed, in Chromium's words,
        # so the browser session can report them the way it reports its own errors.
        self.refused: list[str] = []
        self.failures: dict[str, str] = {}
        self._addresses: dict[str, list[str] | None] = {}
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> str:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
        for writer in list(self._writers):
            writer.close()
        if self._server is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), timeout=5)

    def is_refused(self, host: str | None) -> bool:
        return _clean(host) in self.refused

    async def addresses(self, host: str) -> list[str] | None:
        """Addresses to connect to for `host`, resolved once per scan; None if not allowed."""
        host = _clean(host)
        if host not in self._addresses:
            allowed = await self._allowed_addresses(host)
            self._addresses[host] = allowed
            if allowed is None and host not in self.failures and host not in self.refused:
                self.refused.append(host)
                log.info("egress refused: %s", host)
        return self._addresses[host]

    async def _allowed_addresses(self, host: str) -> list[str] | None:
        if self.allow_private:
            return [host]
        if not host or host in BLOCKED_HOSTNAMES or host.endswith(".internal"):
            return None
        if is_ip_literal(host):
            candidates = [host]
        else:
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(
                    host, None, type=socket.SOCK_STREAM
                )
            except OSError:
                self.failures[host] = "net::ERR_NAME_NOT_RESOLVED"
                return None
            # IPv4 first: the scanner's network has no IPv6 route.
            candidates = sorted({info[4][0] for info in infos}, key=lambda a: ":" in a)
        try:
            public = bool(candidates) and all(is_public_address(a) for a in candidates)
        except ValueError:
            public = False
        return candidates if public else None

    async def _open(
        self, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        addresses = await self.addresses(host)
        if addresses is None:
            if _clean(host) in self.failures:
                raise _Unreachable(host)
            raise _Refused(host)
        last: BaseException | None = None
        for address in addresses:
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection(address, port), timeout=_CONNECT_TIMEOUT
                )
            except (OSError, TimeoutError) as exc:
                last = exc
        reason = (
            "net::ERR_CONNECTION_TIMED_OUT"
            if isinstance(last, TimeoutError)
            else (
                "net::ERR_CONNECTION_REFUSED"
                if isinstance(last, ConnectionRefusedError)
                else "net::ERR_CONNECTION_FAILED"
            )
        )
        self.failures[_clean(host)] = reason
        raise _Unreachable(host)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _HEAD_TIMEOUT)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, OSError):
                return
            request_line, _, header_block = head.partition(b"\r\n")
            try:
                method, target, version = request_line.decode("latin-1").split(" ", 2)
            except ValueError:
                return
            if method.upper() == "CONNECT":
                await self._tunnel(target, reader, writer)
            else:
                await self._forward(method, target, version, header_block, reader, writer)
        except Exception as exc:  # one bad connection must never take the proxy down
            log.debug("egress connection failed: %s", exc)
        finally:
            self._writers.discard(writer)
            writer.close()

    async def _tunnel(
        self, target: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        host, port = _split_authority(target, default_port=443)
        try:
            upstream_reader, upstream_writer = await self._open(host, port)
        except _Refused:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        except _Unreachable:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await _relay(reader, writer, upstream_reader, upstream_writer)

    async def _forward(
        self,
        method: str,
        target: str,
        version: str,
        header_block: bytes,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        parts = urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            return
        try:
            upstream_reader, upstream_writer = await self._open(parts.hostname, parts.port or 80)
        except (_Refused, _Unreachable):
            return  # closing without a response makes Chromium fail the request
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        headers = [
            line
            for line in header_block.split(b"\r\n")
            if line and not line.lower().startswith(_HOP_BY_HOP)
        ]
        # One request per connection keeps the proxy simple: no request framing to parse.
        upstream_writer.write(
            f"{method} {path} {version}\r\n".encode("latin-1")
            + b"\r\n".join([*headers, b"Connection: close"])
            + b"\r\n\r\n"
        )
        await upstream_writer.drain()
        await _relay(reader, writer, upstream_reader, upstream_writer)


def _clean(host: str | None) -> str:
    return (host or "").lower().strip("[]").rstrip(".")


def _split_authority(authority: str, default_port: int) -> tuple[str, int]:
    """'example.com:443' or '[::1]:443' -> (host, port)."""
    parts = urlsplit(f"//{authority}")
    return parts.hostname or "", parts.port or default_port


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(ConnectionError, OSError, asyncio.IncompleteReadError):
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()


async def _relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    """Copy both ways until either side closes, then close both."""
    pumps = [
        asyncio.create_task(_pump(client_reader, upstream_writer)),
        asyncio.create_task(_pump(upstream_reader, client_writer)),
    ]
    try:
        await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for pump in pumps:
            pump.cancel()
        upstream_writer.close()
        client_writer.close()
        await asyncio.gather(*pumps, return_exceptions=True)
