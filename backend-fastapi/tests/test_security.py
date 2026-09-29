import pytest

from app.security import (
    RateLimiter,
    TargetGuard,
    TargetNotAllowedError,
    is_public_address,
    normalize_target,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "https://example.com/"),
        ("  https://Example.com/path?q=1#frag ", "https://example.com/path?q=1"),
        ("http://example.com", "http://example.com/"),
        ("localhost:5173/app", "https://localhost:5173/app"),
    ],
)
def test_normalize_target(raw: str, expected: str) -> None:
    assert normalize_target(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://example.com",
        "javascript:alert(1)",
        "mailto:someone@example.com",
        "https://user:pw@example.com",
        "https://",
        "https://example.com:99999",
    ],
)
def test_normalize_rejects(raw: str) -> None:
    with pytest.raises(TargetNotAllowedError):
        normalize_target(raw)


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("1.1.1.1", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.16.3.4", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # the cloud metadata service
        ("100.64.0.1", False),  # carrier-grade NAT
        ("::1", False),
        ("::ffff:10.0.0.1", False),  # IPv4-mapped private address
        ("2606:4700:4700::1111", True),
    ],
)
def test_is_public_address(address: str, public: bool) -> None:
    assert is_public_address(address) is public


async def test_guard_blocks_private_and_metadata() -> None:
    guard = TargetGuard()
    for url in [
        "http://127.0.0.1:8000",
        "http://169.254.169.254/latest/meta-data",
        "http://10.1.2.3",
    ]:
        with pytest.raises(TargetNotAllowedError):
            await guard.check_url(url)
    assert await guard.is_allowed_host("metadata.google.internal") is False


async def test_guard_allows_public_literal() -> None:
    assert await TargetGuard().check_url("http://1.1.1.1") == "http://1.1.1.1/"


async def test_guard_resolves_names(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = TargetGuard()

    async def fake_resolve(host: str) -> list[str]:
        # A public-looking name that resolves inward is the classic SSRF trick.
        return {"sneaky.example": ["10.0.0.9"], "fine.example": ["93.184.216.34"]}[host]

    monkeypatch.setattr(guard, "_resolve", fake_resolve)
    assert await guard.is_allowed_host("sneaky.example") is False
    assert await guard.is_allowed_host("fine.example") is True
    assert "sneaky.example" in guard.blocked_hosts


async def test_guard_private_mode_allows_localhost() -> None:
    assert await TargetGuard(allow_private=True).is_allowed_host("localhost") is True


def test_rate_limiter() -> None:
    limiter = RateLimiter(limit=2, window_seconds=60)
    assert limiter.check("a") is None
    assert limiter.check("a") is None
    assert limiter.check("a") is not None
    assert limiter.check("b") is None
