"""Protections for running a public service that opens arbitrary URLs.

Two threats matter here:

1. Server-side request forgery. The scanner runs inside AWS, so a request to
   169.254.169.254 or a 10.x address would reach the metadata service or the
   VPC instead of the internet. `TargetGuard` resolves every hostname and
   refuses anything that is not a public address. The browser consults it for
   every request it makes, not just the first URL, so a redirect or a link
   pointing inward is blocked too.

2. Cost abuse. Each scan spends real Claude tokens. `RateLimiter` caps scans
   per client address, and the runner caps concurrent scans.
"""

import asyncio
import ipaddress
import re
import socket
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit, urlunsplit

_BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata", "instance-data"}


class TargetNotAllowedError(ValueError):
    """The URL is malformed or points somewhere the scanner must not go."""


_SCHEME_PREFIX = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):(.*)$")


def normalize_target(raw: str) -> str:
    candidate = raw.strip()
    if "://" not in candidate:
        # "javascript:alert(1)" or "mailto:x" carry a scheme without "//"; only a
        # host:port such as "localhost:5173" may look like that and still be a host.
        match = _SCHEME_PREFIX.match(candidate)
        if match and not match.group(2).split("/")[0].isdigit():
            raise TargetNotAllowedError("Only http and https addresses can be tested.")
        candidate = f"https://{candidate}"

    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https"):
        raise TargetNotAllowedError("Only http and https addresses can be tested.")
    if not parts.hostname:
        raise TargetNotAllowedError("That address has no host name.")
    try:
        _ = parts.port
    except ValueError as exc:
        raise TargetNotAllowedError("That address has an invalid port.") from exc
    if parts.username or parts.password:
        raise TargetNotAllowedError("Addresses with embedded credentials are not accepted.")

    path = parts.path or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, parts.query, ""))


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class TargetGuard:
    def __init__(self, allow_private: bool = False) -> None:
        self.allow_private = allow_private
        self._cache: dict[str, bool] = {}
        self.blocked_hosts: set[str] = set()

    async def _resolve(self, host: str) -> list[str]:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return sorted({info[4][0] for info in infos})

    async def is_allowed_host(self, host: str | None) -> bool:
        if not host:
            return False
        host = host.lower().strip("[]")
        if host in self._cache:
            return self._cache[host]

        if self.allow_private:
            allowed = True
        elif host in _BLOCKED_HOSTNAMES or host.endswith(".internal"):
            allowed = False
        else:
            try:
                addresses = [host] if _is_ip_literal(host) else await self._resolve(host)
                allowed = bool(addresses) and all(is_public_address(a) for a in addresses)
            except (OSError, ValueError):
                allowed = False

        self._cache[host] = allowed
        if not allowed:
            self.blocked_hosts.add(host)
        return allowed

    async def check_url(self, url: str) -> str:
        normalized = normalize_target(url)
        host = urlsplit(normalized).hostname
        if not await self.is_allowed_host(host):
            raise TargetNotAllowedError(
                f"{host} resolves to a private or reserved network address, "
                "or does not resolve at all, so it cannot be scanned."
            )
        return normalized


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class RateLimiter:
    """Sliding-window limit per client. In-process on purpose: the service runs one task."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, client_id: str) -> float | None:
        """Record a hit. Returns None if allowed, else seconds until the next slot frees up."""
        now = time.monotonic()
        hits = self._hits[client_id]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return self.window - (now - hits[0])
        hits.append(now)
        return None
