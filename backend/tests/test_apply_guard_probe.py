"""Run CONTROL_PROBE_JS in a real (headless) Chrome against small fixture pages.

Skipped when no Chrome/Chromium is available to Playwright.
"""

from __future__ import annotations

import pytest

from app.apply.guards import (
    CONTROL_PROBE_JS,
    ELEMENT_PROBE_FUNCTION,
    final_submit_reason,
    key_would_submit,
)

playwright_api = pytest.importorskip("playwright.async_api")

APPLICATION_FORM = """
<form id="app">
  <label>First name <input name="first"></label>
  <label>Last name <input name="last"></label>
  <label>Email <input name="email" type="email"></label>
  <label>Phone <input name="phone"></label>
  <label>Resume <input type="file" name="resume"></label>
  <label>Why us? <textarea name="why"></textarea></label>
  <button id="next" type="button">Next</button>
  <button id="submit"><span>Submit application</span></button>
</form>
"""

SEARCH_PAGE = """
<form id="search"><input name="q" placeholder="Search jobs"><button id="go">Submit</button></form>
<a id="apply" href="#apply">Apply now</a>
"""

OPAQUE_IFRAME_PAGE = """
<iframe id="opaque" style="width:600px;height:300px;border:0" src="data:text/html,<form>
  <input id=a autofocus><button>Submit application</button></form>"></iframe>
"""

IFRAME_PAGE = """
<iframe id="embed" style="width:600px;height:400px;border:0"
  srcdoc='<form><input name=a><input name=b><input type=file name=r>
  <button id=send style="margin-top:200px">Send application</button></form>'></iframe>
"""


async def _launch():
    pw = await playwright_api.async_playwright().start()
    for kwargs in ({"channel": "chrome"}, {"channel": "msedge"}, {}):
        try:
            browser = await pw.chromium.launch(headless=True, **kwargs)
            return pw, browser
        except Exception:
            continue
    await pw.stop()
    pytest.skip("No Chromium-based browser available for Playwright")


async def _probe_at(page, selector: str, frame=None):
    target = (frame or page).locator(selector)
    box = await target.bounding_box()
    assert box is not None
    point = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
    return await page.evaluate(CONTROL_PROBE_JS, point)


@pytest.mark.asyncio
async def test_probe_classifies_controls_in_a_real_page() -> None:
    pw, browser = await _launch()
    try:
        page = await browser.new_page(viewport={"width": 1000, "height": 800})

        await page.set_content(APPLICATION_FORM)
        submit = await _probe_at(page, "#submit")
        assert submit["label"] == "Submit application"
        assert submit["container_file_inputs"] == 1
        assert final_submit_reason(submit) is not None
        assert final_submit_reason(await _probe_at(page, "#next")) is None

        await page.focus("input[name=first]")
        focused = await page.evaluate(CONTROL_PROBE_JS, None)
        assert focused["tag"] == "INPUT"
        assert key_would_submit("Enter", focused) is not None

        await page.set_content(SEARCH_PAGE)
        assert final_submit_reason(await _probe_at(page, "#go")) is None
        assert final_submit_reason(await _probe_at(page, "#apply")) is None

        await page.set_content(IFRAME_PAGE)
        frame = page.frame_locator("#embed")
        inner = await _probe_at(page, "#send", frame=frame)
        assert inner["label"] == "Send application"
        assert final_submit_reason(inner) is not None

        # Ref clicks probe the resolved element itself, inside its own frame.
        handle = await frame.locator("#send").element_handle()
        by_ref = await handle.evaluate(f"(el) => ({ELEMENT_PROBE_FUNCTION}).call(el)")
        assert final_submit_reason(by_ref) is not None

        await page.set_content(OPAQUE_IFRAME_PAGE)
        opaque = await _probe_at(page, "#opaque")
        assert opaque.get("cross_origin_frame") is True
        await page.click("#opaque")
        focused_in_frame = await page.evaluate(CONTROL_PROBE_JS, None)
        assert key_would_submit("Enter", focused_in_frame) is not None
    finally:
        await browser.close()
        await pw.stop()
