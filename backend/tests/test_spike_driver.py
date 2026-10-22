"""The guarded Browser Use driver refuses final submits in a real (headless) Chrome.

Skipped when Google Chrome isn't installed.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("browser_use.integrations.anthropic")

from anthropic.tools import ToolError  # noqa: E402
from anthropic.tools.browser import BetaToolsetCallContext  # noqa: E402
from anthropic.types.beta import (  # noqa: E402
    BetaBrowserFileUploadInput,
    BetaBrowserFormInputInput,
    BetaBrowserGetPageTextInput,
    BetaBrowserKeyInput,
    BetaBrowserLeftClickInput,
    BetaBrowserReadPageInput,
    BetaBrowserTypeInput,
)
from browser_use.browser.session import BrowserSession  # noqa: E402

from app.apply.spike_driver import FILE_PICKER_NOTE, GuardedBrowserUse  # noqa: E402

FORM_PAGE = """<!doctype html><html><body>
<form id="app" onsubmit="document.title='SUBMITTED'; return false;">
  <label>First name <input name="first" id="first"></label>
  <label>Last name <input name="last"></label>
  <label>Email <input name="email" type="email"></label>
  <label>Resume <input type="file" name="resume"></label>
  <label>Why us? <textarea name="why"></textarea></label>
  <button id="next" type="button" onclick="document.title='NEXT'">Next</button>
  <button id="submit">Submit application</button>
</form>
</body></html>"""

SIGNUP_PAGE = """<!doctype html><html><body>
<form onsubmit="return false;">
  <label>Email Address <input id="email" type="email"></label>
  <label>Password <input id="pw" type="password"></label>
  <label>Verify New Password <input id="pw2" type="password"></label>
  <label>Website <input id="site" type="text"></label>
  <button type="button">Create Account</button>
</form>
</body></html>"""

# Workday-style: the resume input is hidden behind "Select files"; the cover letter
# button makes its input on the fly, so it's never in the page.
UPLOAD_PAGE = """<!doctype html><html><body>
<section><h3>Resume/CV</h3>
  <div class="drop-zone"><p>Drop files here</p><span>or</span>
    <button type="button" onclick="document.getElementById('resume').click()">Select files</button>
    <input type="file" id="resume" multiple style="display:none"
           onchange="document.title = 'RESUME:' + this.files[0].name">
  </div>
</section>
<section><h3>Cover letter</h3>
  <button type="button" onclick="const i = document.createElement('input'); i.type = 'file';
    i.onchange = () => { document.title = 'COVER:' + i.files[0].name }; window.cover = i;
    i.click();">Attach cover letter</button>
</section>
<button type="button" onclick="document.title = 'NEXT'">Next</button>
</body></html>"""


def _chrome_path() -> str | None:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ]
    return next((str(p) for p in candidates if p.is_file()), None)


@pytest.fixture
def form_server(tmp_path: Path):
    (tmp_path / "form.html").write_text(FORM_PAGE, encoding="utf-8")
    (tmp_path / "upload.html").write_text(UPLOAD_PAGE, encoding="utf-8")
    (tmp_path / "signup.html").write_text(SIGNUP_PAGE, encoding="utf-8")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/form.html"
    server.shutdown()


@pytest.mark.asyncio
async def test_guarded_driver_blocks_submit_but_allows_navigation(form_server, tmp_path) -> None:
    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Google Chrome not installed")

    session = BrowserSession(
        executable_path=chrome,
        headless=True,
        user_data_dir=str(tmp_path / "profile"),
        highlight_elements=False,
    )
    await session.start()
    events: list[tuple[str, dict]] = []
    try:
        await session.navigate_to(form_server)
        async with GuardedBrowserUse(
            session, on_event=lambda k, d: events.append((k, d))
        ) as driver:
            ctx = BetaToolsetCallContext()
            page = await driver._page()

            tree = await driver.read_page(ctx, BetaBrowserReadPageInput(filter="interactive"))
            submit_ref = re.search(r"\[(ref_\d+)\][^\n]*Submit application", tree).group(1)
            next_ref = re.search(r"\[(ref_\d+)\][^\n]*Next", tree).group(1)

            with pytest.raises(ToolError, match="Blocked by JobBee"):
                await driver.left_click(
                    ctx,
                    BetaBrowserLeftClickInput(target={"type": "ref", "ref": submit_ref}),
                )

            box = await driver._eval(
                page,
                "(() => { const r = document.querySelector('#submit').getBoundingClientRect();"
                " return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()",
            )
            with pytest.raises(ToolError, match="Blocked by JobBee"):
                await driver.left_click(
                    ctx,
                    BetaBrowserLeftClickInput(
                        target={"type": "coordinate", "x": round(box["x"]), "y": round(box["y"])}
                    ),
                )

            await driver._eval(page, "document.querySelector('#first').focus()")
            with pytest.raises(ToolError, match="Blocked by JobBee"):
                await driver.key(ctx, BetaBrowserKeyInput(text="Enter"))

            assert await driver._eval(page, "document.title") != "SUBMITTED"

            await driver.left_click(
                ctx, BetaBrowserLeftClickInput(target={"type": "ref", "ref": next_ref})
            )
            assert await driver._eval(page, "document.title") == "NEXT"
            assert driver.guard_trips == 3
            assert [kind for kind, _ in events] == ["guard_block"] * 3

            # After the agent stops, the human's submit request is captured for endpoint learning.
            mark = driver.network_mark()
            await driver._eval(page, "fetch('/apply', {method: 'POST', body: 'x'}).catch(() => 0)")
            for _ in range(50):
                captured = driver.requests_since(mark)
                if captured:
                    break
                await asyncio.sleep(0.1)
            assert [(r["method"], r["url"].rsplit("/", 1)[-1]) for r in captured] == [
                ("POST", "apply")
            ]

            # The server-side submit path: refused if the page changed since review...
            tab = page._target_id
            clicked, detail = await driver.click_final_submit(tab, form_server + "?other")
            assert not clicked and "changed" in detail
            assert await driver._eval(page, "document.title") != "SUBMITTED"

            # ...allowed exactly once on the reviewed page...
            clicked, label = await driver.click_final_submit(tab, form_server)
            assert clicked and label == "Submit application"
            assert await driver._eval(page, "document.title") == "SUBMITTED"

            # ...and the agent still can't submit afterwards.
            await driver._eval(page, "document.title = 'reset'")
            with pytest.raises(ToolError, match="Blocked by JobBee"):
                await driver.left_click(
                    ctx, BetaBrowserLeftClickInput(target={"type": "ref", "ref": submit_ref})
                )
            assert await driver._eval(page, "document.title") == "reset"
    finally:
        await session.kill()


def _ref(tree: str, label: str) -> str:
    return re.search(rf"\[(ref_\d+)\][^\n]*{label}", tree).group(1)


@pytest.mark.asyncio
async def test_uploads_never_open_the_system_file_dialog(form_server, tmp_path) -> None:
    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Google Chrome not installed")

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 resume")
    letter = tmp_path / "letter.pdf"
    letter.write_bytes(b"%PDF-1.4 letter")
    session = BrowserSession(
        executable_path=chrome,
        headless=True,
        user_data_dir=str(tmp_path / "profile"),
        highlight_elements=False,
    )
    await session.start()
    events: list[str] = []
    try:
        await session.navigate_to(form_server.replace("form.html", "upload.html"))
        async with GuardedBrowserUse(session, on_event=lambda k, d: events.append(k)) as driver:
            ctx = BetaToolsetCallContext()
            page = await driver._page()
            tree = await driver.read_page(ctx, BetaBrowserReadPageInput(filter="interactive"))
            select, cover, next_ = (_ref(tree, x) for x in ("Select files", "cover letter", "Next"))

            # Clicking an upload button opens no dialog; the agent is told to use file_upload.
            note = await driver.left_click(
                ctx, BetaBrowserLeftClickInput(target={"type": "ref", "ref": select})
            )
            assert note == FILE_PICKER_NOTE and events == ["file_picker_blocked"]
            assert await driver.left_click(
                ctx, BetaBrowserLeftClickInput(target={"type": "ref", "ref": next_})
            ) is None

            # file_upload on the button finds the hidden input behind it...
            await driver.file_upload(
                ctx,
                BetaBrowserFileUploadInput(
                    target={"type": "ref", "ref": select}, paths=[str(resume)]
                ),
            )
            assert await driver._eval(page, "document.title") == "RESUME:resume.pdf"

            # ...and the one a script creates when the button is clicked.
            await driver.file_upload(
                ctx,
                BetaBrowserFileUploadInput(
                    target={"type": "ref", "ref": cover}, paths=[str(letter)]
                ),
            )
            assert await driver._eval(page, "document.title") == "COVER:letter.pdf"
            names = "[...document.getElementById('resume').files].map(f => f.name)"
            assert await driver._eval(page, names) == ["resume.pdf"]

            assert events == ["file_picker_blocked"] * 3

            # Between the agent's actions the dialog is left alone, so the human can upload.
            box = await driver._eval(
                page,
                "(() => { const r = [...document.querySelectorAll('button')][0]"
                ".getBoundingClientRect(); return {x: r.left + 5, y: r.top + 5}; })()",
            )
            for kind in ("mousePressed", "mouseReleased"):
                await driver._mouse(page, kind, box["x"], box["y"], button="left", count=1)
            await asyncio.sleep(0.5)
            assert events == ["file_picker_blocked"] * 3
    finally:
        await session.kill()


@pytest.mark.asyncio
async def test_site_passwords_go_only_into_password_fields_on_their_site(form_server, tmp_path):
    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Google Chrome not installed")

    session = BrowserSession(
        executable_path=chrome,
        headless=True,
        user_data_dir=str(tmp_path / "profile"),
        highlight_elements=False,
    )
    await session.start()
    secret = "Zq8!pW2r#Tx5mN7a@Lk3"
    events: list[str] = []
    try:
        await session.navigate_to(form_server.replace("form.html", "signup.html"))
        async with GuardedBrowserUse(session, on_event=lambda k, d: events.append(k)) as driver:
            ctx = BetaToolsetCallContext()
            page = await driver._page()
            tree = await driver.read_page(ctx, BetaBrowserReadPageInput(filter="interactive"))
            email, pw, pw2, site = (
                _ref(tree, label)
                for label in ("Email Address", '"Password', "Verify New Password", "Website")
            )
            value = "(id) => document.getElementById(id).value"

            # Checked before anything is typed: wrong site, or a text field posing as one.
            with pytest.raises(ToolError, match="no longer on the site"):
                await driver.fill_credentials(
                    "acme.wd5.myworkdayjobs.com",
                    login_ref=email,
                    login="lin@example.com",
                    password_refs=[pw],
                    password=secret,
                )
            with pytest.raises(ToolError, match="isn't a password field"):
                await driver.fill_credentials(
                    "127.0.0.1",
                    login_ref=email,
                    login="lin@example.com",
                    password_refs=[pw, site],
                    password=secret,
                )
            assert await driver._eval(page, f"({value})('email')") == ""
            assert await driver._eval(page, f"({value})('site')") == ""

            assert await driver.ref_host(pw) == "127.0.0.1"
            await driver.fill_credentials(
                "127.0.0.1",
                login_ref=email,
                login="lin@example.com",
                password_refs=[pw, pw2],
                password=secret,
            )
            assert await driver._eval(page, f"({value})('email')") == "lin@example.com"
            assert await driver._eval(page, f"({value})('pw')") == secret
            assert await driver._eval(page, f"({value})('pw2')") == secret
            tree = await driver.read_page(ctx, BetaBrowserReadPageInput(filter="all"))
            text = await driver.get_page_text(ctx, BetaBrowserGetPageTextInput())
            assert secret not in tree and secret not in (text or "")

            # The agent itself can't type into password or ID fields.
            with pytest.raises(ToolError, match="password field"):
                await driver.form_input(
                    ctx,
                    BetaBrowserFormInputInput(target={"type": "ref", "ref": pw}, value="guess"),
                )
            await driver._eval(page, "document.getElementById('pw2').focus()")
            with pytest.raises(ToolError, match="password field"):
                await driver.type(ctx, BetaBrowserTypeInput(text="guess"))
            await driver.form_input(
                ctx, BetaBrowserFormInputInput(target={"type": "ref", "ref": site}, value="x.dev")
            )
            assert await driver._eval(page, f"({value})('site')") == "x.dev"
            assert events == ["sensitive_blocked", "sensitive_blocked"]
    finally:
        await session.kill()
