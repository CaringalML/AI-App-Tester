"""Contrast is measured like a person sees it, and traced to the CSS that sets it.

Runs in real Chromium with axe-core; skipped where either is missing (CI has
neither: the browser is not installed and vendor/ is fetched at image build).
"""

from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError

from app.scanner.browser import _AXE_JS, BrowserSession
from app.scanner.observations import ObservationLog
from app.security import TargetGuard

AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe.min.js"


async def _session() -> BrowserSession:
    if not AXE.exists():
        pytest.skip("axe-core is not vendored here")
    session = BrowserSession(
        TargetGuard(allow_private=True), ObservationLog(), accessibility=True, axe_path=str(AXE)
    )
    try:
        await session.__aenter__()
    except PlaywrightError as exc:
        pytest.skip(f"Chromium is not installed here: {str(exc).splitlines()[0]}")
    return session


async def _contrast_failures(html: str) -> list[str]:
    session = await _session()
    try:
        await session.page.set_content(html)
        await session.page.add_script_tag(content=AXE.read_text(encoding="utf-8"))
        violations = await session.page.evaluate(_AXE_JS)
    finally:
        await session.__aexit__(None, None, None)
    contrast = next((v for v in violations if v["id"] == "color-contrast"), None)
    return [n["text"] for n in contrast["nodes"]] if contrast else []


async def test_text_is_judged_after_it_has_faded_in() -> None:
    # Measured mid-fade, the first line would look faint; once it has faded in it is not.
    failures = await _contrast_failures(
        """
        <style>
          @keyframes rise { from { opacity: 0.15; } to { opacity: 1; } }
          body { background: #ffffff; }
          .fade { color: #333333; animation: rise 1.2s ease-out both; }
          .faint { color: #bbbbbb; }
        </style>
        <p class="fade">Fades in, then reads clearly</p>
        <p class="faint">Always too faint to read</p>
        """
    )
    assert failures == ["Always too faint to read"]


async def test_a_colour_that_changes_between_measurements_is_not_reported() -> None:
    # Like a carousel or a late script: faint when first measured, dark a moment later.
    failures = await _contrast_failures(
        """
        <style>body { background: #ffffff; } .late { color: #cccccc; }</style>
        <p class="late">Changes colour shortly after loading</p>
        <script>
          setTimeout(() => { document.querySelector('.late').style.color = '#222222'; }, 600);
        </script>
        """
    )
    assert failures == []


async def test_style_sources_name_the_rule_and_where_it_lives() -> None:
    session = await _session()
    try:
        await session.page.set_content(
            """
            <style>
              body { background: #ffffff; }
              .card { background-color: #f4f1ea; padding: 8px; }
              .text-gold { color: #cbb9a4; }
            </style>
            <section class="card"><span class="text-gold">NZ</span></section>
            """
        )
        sources = await session.style_sources(".text-gold")
        leftovers = await session.page.evaluate(
            "() => document.querySelectorAll('[data-aat-bg]').length"
        )
    finally:
        await session.__aexit__(None, None, None)

    colour, background = sources
    assert (colour["name"], colour["value"], colour["rule"]) == ("color", "#cbb9a4", ".text-gold")
    assert colour["source"].startswith("<style> in the page") and not colour["inherited"]
    # The background is painted by the section around the text, not by the text itself.
    assert (background["name"], background["value"], background["rule"]) == (
        "background-color",
        "#f4f1ea",
        ".card",
    )
    assert background["element"] == "section.card"
    assert leftovers == 0  # the temporary marker is cleaned up
