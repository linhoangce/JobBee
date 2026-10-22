"""Phase 1 spike driver: Browser Use's Claude toolset driver plus JobBee's submit guard.

This exists to measure model capability quickly. The production driver
(Phase 2) is JobBee's own Playwright driver, which can also enforce the guard
at the network layer and inside cross-origin frames.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import anthropic.tools.browser as _sdk_browser
from anthropic.tools import ToolError
from anthropic.tools.browser import BetaBrowserState, BetaScreenshotResult

# browser-use 0.13.11 was written against a pre-release Anthropic SDK that used
# these older names for the same types (same fields). Alias them until
# browser-use catches up; drop this when its driver imports cleanly.
for _old, _new in (
    ("BetaBrowserScreenshotResult", BetaScreenshotResult),
    ("BrowserState", BetaBrowserState),
    ("ToolError", ToolError),
):
    if not hasattr(_sdk_browser, _old):
        setattr(_sdk_browser, _old, _new)

from anthropic.tools.browser import BetaToolsetCallContext  # noqa: E402
from anthropic.types.beta import (  # noqa: E402
    BetaBrowserFormInputInput,
    BetaBrowserLeftClickInput,
    BetaBrowserNavigateInput,
)
from browser_use.integrations.anthropic import BrowserUse  # noqa: E402

from .guards import (  # noqa: E402
    BLOCKED_SUBMIT_MESSAGE,
    CANDIDATE_POINT_JS,
    CONTROL_PROBE_JS,
    ELEMENT_PROBE_FUNCTION,
    FIELD_FACTS_FUNCTION,
    FOCUSED_FIELD_JS,
    SUBMIT_CANDIDATES_JS,
    final_submit_reason,
    key_would_submit,
    pick_final_submit,
    sensitive_field_message,
)

# Members the apply agent never needs; fewer ways around the guard.
DISABLED_MEMBERS = (
    "left_mouse_down",
    "left_mouse_up",
    "left_click_drag",
    "hold_key",
    "javascript_exec",
    "read_console",
    "read_network",
)

EventSink = Callable[[str, dict[str, Any]], None]

# Sites hide the real file input behind a styled button, link or drop zone. This finds
# the input a control stands for. Levels: 0 is the control itself, its label's control,
# or the one file input inside it; 4 also looks in the enclosing elements up to four
# levels out, nearest first, giving up at one that holds several file inputs.
FIND_FILE_INPUT_FUNCTION = """function (levels) {
  const usable = (el) => !!el && el.tagName === 'INPUT' && el.type === 'file' && !el.disabled;
  if (usable(this)) return this;
  const label = this.closest ? this.closest('label') : null;
  if (label && usable(label.control)) return label.control;
  let node = this;
  for (let depth = 0; node && depth <= levels; depth++) {
    if (node.querySelectorAll) {
      const inputs = [...node.querySelectorAll('input[type=file]')].filter(usable);
      if (inputs.length === 1) return inputs[0];
      if (inputs.length > 1) return null;
    }
    node = node.parentElement || (node.getRootNode && node.getRootNode().host) || null;
  }
  return null;
}"""
# Only controls that look like uploads get clicked to see which file input they open.
UPLOAD_LIKE_FUNCTION = r"""function () {
  const text = [this.innerText, this.getAttribute('aria-label'), this.title, this.value]
    .filter((t) => typeof t === 'string').join(' ').slice(0, 200);
  return /upload|attach|browse|choose|select|drop|\bfiles?\b|r[ée]sum[ée]|\bcv\b|cover/i
    .test(text);
}"""

FILE_PICKER_NOTE = (
    "That opened a file picker. JobBee kept the system file dialog closed because nobody is at "
    "the computer to use it. To upload, call `file_upload` on this same control (or the file "
    "input) with the file's path from the dossier."
)
NO_FILE_INPUT_MESSAGE = (
    "No file input found behind that control, and clicking it didn't open a file picker JobBee "
    "can fill. Target the upload button, the drop zone or the file input itself (read_page with "
    "filter 'all' shows hidden inputs). If there's really no way, call `request_human_action` "
    "with kind 'other' and ask the human to upload the file."
)
# How long a click may take to open a file picker: longer when the page has a file input.
CHOOSER_WAIT = 0.05
CHOOSER_WAIT_WITH_INPUT = 0.25


def _same_page(a: str, b: str) -> bool:
    return a.split("#", 1)[0].rstrip("/") == b.split("#", 1)[0].rstrip("/")


class GuardedBrowserUse(BrowserUse):
    """Refuses clicks and key presses that would submit an application.

    The only way past the guard is `click_final_submit()`, which JobBee's server
    calls after the user taps Submit: it grants a one-shot allowance bound to
    this tab and the exact page the user reviewed. The model can't reach it.
    """

    def __init__(self, *args: Any, on_event: EventSink | None = None, **kwargs: Any) -> None:
        configs = dict(kwargs.pop("configs", None) or {})
        for name in DISABLED_MEMBERS:
            configs.setdefault(name, {"enabled": False})
        super().__init__(*args, configs=configs, **kwargs)
        self._on_event = on_event or (lambda kind, data: None)
        self.guard_trips = 0
        self.uninspected_clicks = 0
        self._foreign_tabs: set[str] = set()
        self.home_tab: str | None = None
        self._allowance: tuple[str, str] | None = None
        self._allowance_refused: str | None = None
        self.submit_used = False
        self._choosers: dict[str, dict] = {}  # tab -> file picker the last action opened
        self._page_domain: set[str] = set()  # observer sessions with the Page domain on
        self._chooser_observer = None

    # --- tab isolation ------------------------------------------------------------------

    def pin_to_tab(self, tab_id: str) -> None:
        """Tabs open right now (other applications' parked forms) are off-limits."""

        self.home_tab = tab_id
        self._foreign_tabs = {t["tab_id"] for t in self._tabs()} - {tab_id}

    async def _page(self, tab_id=None):
        page = await super()._page(tab_id)
        if page._target_id in self._foreign_tabs:
            raise ToolError(
                "That tab belongs to another application JobBee is working on. Use switch_tab "
                f"to go back to your tab ({self.home_tab}) and stay there."
            )
        return page

    # --- the one-shot submit allowance -------------------------------------------------------

    async def _allowed_once(self, page) -> bool:
        allowance, self._allowance = self._allowance, None
        if allowance is None:
            return False
        tab, url = allowance
        current = await page.get_url()
        if page._target_id != tab or not _same_page(current, url):
            self._on_event(
                "submit_refused", {"reason": "page changed since review", "url": current}
            )
            self._allowance_refused = "the page changed since you reviewed it"
            return False
        self.submit_used = True
        self._on_event("submit_released", {"url": current})
        return True

    async def screenshot_png(self, tab_id: str, path) -> str:
        page = await self._page(tab_id)
        image = await self._screenshot(page)
        image.save(path, format="PNG")
        return await page.get_url()

    async def click_final_submit(self, tab_id: str, reviewed_url: str) -> tuple[bool, str]:
        """Click the page's single final-submit control once. Returns (clicked, detail)."""

        page = await self._page(tab_id)
        candidates = await self._eval(page, f"({SUBMIT_CANDIDATES_JS})()")
        index, why = pick_final_submit(candidates or [])
        if index is None:
            return False, why
        point = await self._eval(page, f"({CANDIDATE_POINT_JS})({index})")
        if not point:
            return False, "the submit button disappeared"
        self._allowance = (tab_id, reviewed_url)
        self._allowance_refused = None
        try:
            await self.left_click(
                BetaToolsetCallContext(),
                BetaBrowserLeftClickInput(
                    target={"type": "coordinate", "x": round(point["x"]), "y": round(point["y"])},
                    tab_id=tab_id,
                ),
            )
        except ToolError as e:
            return False, self._allowance_refused or str(e)
        finally:
            self._allowance = None
        if not self.submit_used:
            return False, "the page changed since you reviewed it"
        return True, candidates[index].get("label", "Submit")

    async def page_text(self, tab_id: str) -> tuple[str, str]:
        page = await self._page(tab_id)
        text = await self._eval(page, "document.body ? document.body.innerText : ''")
        return await page.get_url(), text or ""

    def network_mark(self) -> dict[str, int]:
        """Position in each observed tab's request log; pass to `requests_since` later."""

        return {tab: len(rows) for tab, rows in self._network.items()}

    def requests_since(
        self, mark: dict[str, int], methods: frozenset[str] = frozenset({"POST", "PUT", "PATCH"})
    ) -> list[dict]:
        """Completed requests (method/url/status) logged after `mark`, across every tab."""

        out = []
        for tab, rows in self._network.items():
            for line in list(rows)[mark.get(tab, 0) :]:
                row = json.loads(line)
                if row.get("method") in methods:
                    out.append({"tab": tab, **row})
        return out

    async def _facts_at_point(self, page, x: float, y: float) -> dict:
        return await self._eval(page, f"({CONTROL_PROBE_JS})({json.dumps({'x': x, 'y': y})})")

    async def _facts_for_ref(self, page, ref: str) -> dict:
        _, object_id = await self._resolve(page, ref)
        try:
            return await self._call_node(page, object_id, ELEMENT_PROBE_FUNCTION)
        finally:
            await self._release_object(page, object_id)

    def _refuse(self, action: str, facts: dict, reason: str) -> None:
        self.guard_trips += 1
        label = facts.get("label") or "this control"
        self._on_event("guard_block", {"action": action, "label": label, "reason": reason})
        raise ToolError(BLOCKED_SUBMIT_MESSAGE.format(label=label, reason=reason))

    async def _click(self, input, button, count=1):
        page = await self._page(input.tab_id)
        if button == "left":
            if input.target.type == "ref":
                facts = await self._facts_for_ref(page, input.target.ref)
            else:
                facts = await self._facts_at_point(page, input.target.x, input.target.y)
            if facts.get("cross_origin_frame"):
                self.uninspected_clicks += 1
                self._on_event("guard_uninspected", {"target": input.target.model_dump()})
            reason = final_submit_reason(facts)
            if reason and not await self._allowed_once(page):
                self._refuse("click", facts, reason)
        return await self._without_file_dialog(page, super()._click(input, button, count))

    async def key(self, context, input):
        page = await self._page(input.tab_id)
        facts = await self._eval(page, f"({CONTROL_PROBE_JS})(null)")
        reason = key_would_submit(input.text, facts)
        if reason:
            self._refuse(f"key {input.text!r}", facts, reason)
        await self._without_file_dialog(page, super().key(context, input))
        if page._target_id in self._choosers:
            return FILE_PICKER_NOTE

    async def left_click(self, context, input):
        await super().left_click(context, input)
        page = await self._page(input.tab_id)
        if page._target_id in self._choosers:
            return FILE_PICKER_NOTE

    # --- sensitive fields and site passwords ------------------------------------------------

    async def _field_facts(self, page, ref: str) -> dict:
        _, object_id = await self._resolve(page, ref)
        try:
            return await self._call_node(page, object_id, FIELD_FACTS_FUNCTION) or {}
        finally:
            await self._release_object(page, object_id)

    def _refuse_sensitive(self, field: dict | None) -> None:
        message = sensitive_field_message(field)
        if message:
            self._on_event("sensitive_blocked", {"field": (field or {}).get("name", "")[:120]})
            raise ToolError(message)

    async def tab_host(self, tab_id: str | None = None) -> str:
        page = await self._page(tab_id)
        return (urlsplit(await page.get_url()).hostname or "").lower()

    async def ref_host(self, ref: str) -> str:
        """Host of the page an element ref belongs to."""

        reference = self._refs.get(ref)
        if reference is None:
            raise ToolError(f"{ref} is stale or unknown. Call read_page or find again.")
        return await self.tab_host(reference.tab)

    async def fill_credentials(
        self,
        host: str,
        *,
        login_ref: str | None,
        login: str,
        password_refs: list[str],
        password: str,
    ) -> None:
        """Type a site account's login and password into the page; the model never sees them.

        Only while the page is still on `host`, and the password only into real
        password inputs, so a page can't steer it into a field it could read back.
        Every field is checked before any is filled.
        """

        fills = [(ref, password, True) for ref in password_refs]
        if login_ref:
            fills.insert(0, (login_ref, login, False))
        for ref, _, secret in fills:
            if await self.ref_host(ref) != host:
                raise ToolError(
                    "The page is no longer on the site this account belongs to; re-read the page."
                )
            field = await self._field_facts(await self._page(self._refs[ref].tab), ref)
            if secret != (field.get("type") == "password"):
                raise ToolError(
                    f"{ref} isn't a password field."
                    if secret
                    else f"{ref} is a password field; pass the email or username field."
                )
        for ref, value, _ in fills:
            await super().form_input(
                BetaToolsetCallContext(),
                BetaBrowserFormInputInput(
                    target={"type": "ref", "ref": ref}, value=value, tab_id=self._refs[ref].tab
                ),
            )

    async def open_url(self, url: str) -> None:
        """Open a link in the agent's active tab (the caller has applied the URL policy)."""

        await self.navigate(BetaToolsetCallContext(), BetaBrowserNavigateInput(url=url))

    # --- uploads without the system file dialog ----------------------------------------------

    async def _ensure_observer(self):
        await super()._ensure_observer()
        if self._observer is not None and self._observer is not self._chooser_observer:
            self._chooser_observer = self._observer
            self._observer.register.Page.fileChooserOpened(self._on_file_chooser)

    def _on_file_chooser(self, event, session_id=None) -> None:
        tab = self._session_tabs.get(session_id)
        if tab:
            self._choosers[tab] = dict(event)
            self._on_event("file_picker_blocked", {"mode": event.get("mode")})

    async def _intercept_file_dialog(self, tab: str, enabled: bool) -> bool:
        """Catch file pickers in this tab instead of showing them; False if that failed."""

        sid = self._observer_sessions.get(tab)
        if self._observer is None or sid is None:
            return False
        try:
            if sid not in self._page_domain:
                await self._observer.send.Page.enable(session_id=sid)
                self._page_domain.add(sid)
            await self._observer.send.Page.setInterceptFileChooserDialog(
                params={"enabled": enabled}, session_id=sid
            )
        except Exception:
            return False
        return True

    async def _without_file_dialog(self, page, action):
        """Run one agent click or key press with file pickers caught, not shown.

        Nobody is at the computer to use the system file dialog, so a picker the
        action opens is recorded in `_choosers` instead. Between the agent's
        actions the dialog works as usual, so the human can upload by hand during
        a hand-off or the review.
        """

        tab = page._target_id
        self._choosers.pop(tab, None)
        intercepting = await self._intercept_file_dialog(tab, True)
        try:
            result = await action
            if intercepting:
                await self._wait_for_chooser(page)
            return result
        finally:
            if intercepting:
                await self._intercept_file_dialog(tab, False)

    async def _wait_for_chooser(self, page) -> dict | None:
        tab = page._target_id
        try:
            has_input = await self._eval(page, "!!document.querySelector('input[type=file]')")
        except Exception:  # the action navigated away
            has_input = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (CHOOSER_WAIT_WITH_INPUT if has_input else CHOOSER_WAIT)
        while tab not in self._choosers and loop.time() < deadline:
            await asyncio.sleep(0.02)
        return self._choosers.get(tab)

    async def _file_input_near(self, page, ref: str, levels: int) -> int | None:
        """Backend node id of the file input `ref` stands for (see FIND_FILE_INPUT_FUNCTION)."""

        _, object_id = await self._resolve(page, ref)
        try:
            result = await self._cdp.send.Runtime.callFunctionOn(
                params={
                    "objectId": object_id,
                    "functionDeclaration": FIND_FILE_INPUT_FUNCTION,
                    "arguments": [{"value": levels}],
                },
                session_id=await page.session_id,
            )
            self._raise_js(result)
            found = result.get("result", {}).get("objectId")
            if not found:
                return None
            try:
                node = await self._cdp.send.DOM.describeNode(
                    params={"objectId": found}, session_id=await page.session_id
                )
                return node["node"]["backendNodeId"]
            finally:
                await self._release_object(page, found)
        finally:
            await self._release_object(page, object_id)

    async def _file_input_opened_by(self, page, input) -> int | None:
        """Click an upload-looking control with the dialog caught; the input it opened."""

        _, object_id = await self._resolve(page, input.target.ref)
        try:
            upload_like = await self._call_node(page, object_id, UPLOAD_LIKE_FUNCTION)
        finally:
            await self._release_object(page, object_id)
        if not upload_like:
            return None
        click = BetaBrowserLeftClickInput(target=input.target.model_dump(), tab_id=input.tab_id)
        await self._click(click, "left")
        return (self._choosers.get(page._target_id) or {}).get("backendNodeId")

    async def file_upload(self, context, input):
        """Upload through the file input or the button, link or drop zone in front of it.

        In order: the input the control is or contains; the input the page opens
        when the control is clicked (system dialog caught); one input close by.
        Clicking comes before looking around, so a cover letter button never
        lands its file in the resume input next to it.
        """

        if input.document_ids or not input.paths:
            raise ToolError("Upload by path: use the file paths listed in the dossier.")
        page = await self._page(input.tab_id)
        ref = input.target.ref
        backend = (
            await self._file_input_near(page, ref, 0)
            or await self._file_input_opened_by(page, input)
            or await self._file_input_near(page, ref, 4)
        )
        if backend is None:
            raise ToolError(NO_FILE_INPUT_MESSAGE)
        # Set directly: an input a script made on the fly isn't in the page, which
        # the base driver's ref check would refuse. Paths were vetted by the file policy.
        await self._cdp.send.DOM.setFileInputFiles(
            params={"files": list(input.paths), "backendNodeId": backend},
            session_id=await page.session_id,
        )

    async def type(self, context, input):
        page = await self._page(input.tab_id)
        self._refuse_sensitive(await self._eval(page, FOCUSED_FIELD_JS))
        return await super().type(context, input)

    async def form_input(self, context, input):
        page = await self._page(input.tab_id)
        self._refuse_sensitive(await self._field_facts(page, input.target.ref))
        facts = await self._facts_for_ref(page, input.target.ref)
        reason = final_submit_reason(facts)
        if reason and (
            facts.get("tag") in {"BUTTON", "A"} or facts.get("type") in {"submit", "image"}
        ):
            self._refuse("form_input", facts, reason)
        return await super().form_input(context, input)
