"""Download a pinned axe-core build to vendor/axe.min.js.

Run once for local development; the Dockerfile runs it at build time so the
running service never depends on a CDN being reachable.
"""

import sys
import urllib.request
from pathlib import Path

VERSION = sys.argv[1] if len(sys.argv) > 1 else "4.10.2"
URL = f"https://cdn.jsdelivr.net/npm/axe-core@{VERSION}/axe.min.js"
DEST = Path(__file__).resolve().parents[1] / "vendor" / "axe.min.js"


def main() -> None:
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL, timeout=30) as response:  # noqa: S310 - fixed https URL
        body = response.read()
    if b"axe" not in body[:2000] or len(body) < 100_000:
        raise SystemExit(f"Unexpected response from {URL}")
    DEST.write_bytes(body)
    print(f"axe-core {VERSION} -> {DEST} ({len(body) // 1024} KB)")


if __name__ == "__main__":
    main()
