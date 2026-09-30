"""What Claude is told about the page: element names as a screen reader announces them.

Reading only innerText made every image-only button look nameless, and Claude
reported working buttons as inaccessible. Runs in real Chromium; skipped where
Playwright's browser is not installed (CI installs none).
"""

import pytest
from playwright.async_api import Error as PlaywrightError

from app.scanner.agent import format_page_state
from app.scanner.browser import BrowserSession
from app.scanner.observations import ObservationLog
from app.security import TargetGuard

SIZE = 'style="display:inline-block;width:40px;height:40px"'

PAGE = f"""
<button {SIZE}><img src="data:," alt="Glass Skin Peel"></button>
<span id="t1">Close dialog</span><button {SIZE} aria-labelledby="t1"></button>
<a href="/cart" {SIZE}><svg aria-label="Cart" width="20" height="20"></svg></a>
<label for="email">Email address</label><input id="email" name="email">
<label>Remember me <input type="checkbox" name="remember"></label>
<input type="submit" value="Sign in">
<input name="q" placeholder="Search products">
<button {SIZE} data-case="empty"></button>
"""


async def test_elements_are_named_the_way_a_screen_reader_names_them() -> None:
    session = BrowserSession(
        TargetGuard(allow_private=True), ObservationLog(), accessibility=False, axe_path="none"
    )
    try:
        await session.__aenter__()
    except PlaywrightError as exc:
        pytest.skip(f"Chromium is not installed here: {str(exc).splitlines()[0]}")
    try:
        await session.page.set_content(PAGE)
        state = await session.snapshot()
    finally:
        await session.__aexit__(None, None, None)

    names = [(e["tag"], e["label"]) for e in state["elements"]]
    assert names == [
        ("button", "Glass Skin Peel"),  # named by the image inside it
        ("button", "Close dialog"),  # aria-labelledby
        ("a", "Cart"),  # a labelled icon
        ("input", "Email address"),  # <label for>
        ("input", "Remember me"),  # a wrapping <label>
        ("input", "Sign in"),  # a submit button's value
        ("input", "Search products"),  # placeholder as the last resort
        ("button", ""),  # truly nameless stays nameless
    ]
    listed = format_page_state(state)
    # The name attribute is shown as its own field, never passed off as a label.
    assert 'input "Email address" field=email' in listed
    assert "the name a screen reader announces" in listed
