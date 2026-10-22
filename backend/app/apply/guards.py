"""Code-enforced "never submit" guard for the apply agent.

The browser driver runs `CONTROL_PROBE_JS` against the element a click (or a
key press) would hit and passes the facts it returns to
`final_submit_reason()`. A non-empty reason means the action would submit an
application, so the driver refuses it and the human clicks Submit instead.

The rules fail closed for submit-like labels inside application-looking
containers, and stay out of the way for search boxes, login/sign-up forms and
wizard navigation ("Next", "Continue", "Review").
"""

from __future__ import annotations

import re
from typing import TypedDict
from urllib.parse import urlsplit

BLOCKED_SUBMIT_MESSAGE = (
    "Blocked by JobBee: '{label}' looks like the final application submit ({reason}). "
    "JobBee never submits applications; the human does. If every field is filled, call "
    "`finish` with status 'ready_for_review'. If this control is really an intermediate "
    "step (e.g. it saves a page or signs in), call `request_human_action` with "
    "kind 'blocked_click' and explain, and the human will click it."
)


class ControlFacts(TypedDict, total=False):
    """What `CONTROL_PROBE_JS` reports about a target element and its container."""

    found: bool
    tag: str
    type: str
    role: str
    label: str
    is_clickable: bool
    in_form: bool
    container: str  # "form" | "dialog" | "page"
    container_is_search: bool
    container_file_inputs: int
    container_fillable: int
    container_password_inputs: int
    container_textareas: int


# "Submit application", "Send my application", "Complete application" ... never safe.
_STRONG_FINAL_LABEL = re.compile(
    r"""^\s*(?:submit|send|finish|complete|confirm)(?:\s+and)?
        (?:\s+(?:my|your|the|this))?\s+(?:job\s+)?(?:application|applicaton|candidacy)
        (?:\s+now)?\s*[!.]?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
# Bare "Submit" / "Send" / "Finish" ... final unless the container is a search,
# newsletter or sign-in form.
_WEAK_FINAL_LABEL = re.compile(
    r"^\s*(?:submit|send|finish|complete|confirm)(?:\s+now)?\s*[!.]?\s*$", re.IGNORECASE
)
_APPLY_LABEL = re.compile(r"^\s*(?:apply|apply\s+now|apply\s+for\s+this\s+job)\s*[!.]?\s*$", re.I)
_NAVIGATION_WORDS = re.compile(r"\b(next|continue|save|review|back|upload|add|sign|log)\b", re.I)
_SUBMIT_LIKE_TYPES = {"submit", "image"}


def _is_application_container(facts: ControlFacts) -> bool:
    return (
        facts.get("container_file_inputs", 0) > 0
        or facts.get("container_textareas", 0) > 0
        or facts.get("container_fillable", 0) >= 5
    )


def _is_account_or_search_container(facts: ControlFacts) -> bool:
    if facts.get("container_is_search"):
        return True
    fillable = facts.get("container_fillable", 0)
    no_application_inputs = (
        facts.get("container_file_inputs", 0) == 0 and facts.get("container_textareas", 0) == 0
    )
    if not no_application_inputs:
        return False
    if facts.get("container_password_inputs", 0) > 0 and fillable <= 4:
        return True  # sign-in / create-account form
    # A single text box in its own form: site search, newsletter, job alerts.
    return facts.get("container") == "form" and fillable == 1


def final_submit_reason(facts: ControlFacts | None) -> str | None:
    """Return why the control would submit an application, or None if it is safe to click."""

    if not facts or not facts.get("found") or not facts.get("is_clickable"):
        return None
    label = " ".join((facts.get("label") or "").split())
    control_type = (facts.get("type") or "").lower()

    if label and _STRONG_FINAL_LABEL.match(label) and not facts.get("container_is_search"):
        return "submit-application label"
    if _is_account_or_search_container(facts):
        return None
    if label and _WEAK_FINAL_LABEL.match(label):
        return "submit-style label"
    if label and _APPLY_LABEL.match(label) and _is_application_container(facts):
        return "'Apply' button inside a filled-in application form"
    if (
        control_type in _SUBMIT_LIKE_TYPES
        and facts.get("in_form")
        and _is_application_container(facts)
        and not _NAVIGATION_WORDS.search(label)
    ):
        return "form submit button of an application form"
    return None


def key_would_submit(keys: str, facts: ControlFacts | None) -> str | None:
    """Enter/Space on a submit control, or Enter in a single-line field of an application form."""

    chords = [chord.lower() for chord in keys.split()]
    presses_enter = any(chord.split("+")[-1] in {"enter", "return"} for chord in chords)
    presses_space = any(chord.split("+")[-1] in {"space", " "} for chord in chords)
    if not (presses_enter or presses_space) or not facts or not facts.get("found"):
        return None
    if facts.get("cross_origin_frame") and presses_enter:
        return "focus is inside an embedded frame JobBee cannot inspect; click instead of Enter"
    reason = final_submit_reason(facts)
    if reason:
        return reason
    tag = (facts.get("tag") or "").upper()
    control_type = (facts.get("type") or "text").lower()
    single_line = tag == "INPUT" and control_type not in {
        "checkbox",
        "radio",
        "file",
        "button",
        "submit",
        "reset",
        "image",
        "hidden",
    }
    if (
        presses_enter
        and single_line
        and facts.get("in_form")
        and _is_application_container(facts)
        and not _is_account_or_search_container(facts)
    ):
        return "Enter in a field submits the application form"
    return None


# Facts about one element (runs in the element's own frame, so it also works
# for refs inside iframes via Runtime.callFunctionOn).
_ELEMENT_FACTS_JS = r"""
(el) => {
  if (el.tagName === 'LABEL' && el.control) el = el.control;
  const clickable = el.closest(
    'button, input[type=submit], input[type=image], input[type=button], ' +
    '[role=button], a[href], [role=link]'
  );
  const target = clickable || el;
  const text = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const label = text(
    target.getAttribute('aria-label') ||
    (target.tagName === 'INPUT' ? target.value : '') ||
    target.innerText || target.textContent || target.getAttribute('title')
  ).slice(0, 120);
  const form = target.form || target.closest('form');
  const dialog = target.closest('[role=dialog], dialog, [aria-modal=true]');
  const container = form || dialog || target.ownerDocument.body;
  const visible = (e) => {
    const r = e.getBoundingClientRect();
    const st = e.ownerDocument.defaultView.getComputedStyle(e);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  };
  const fields = Array.from(container.querySelectorAll('input, select, textarea')).filter((f) =>
    !['hidden', 'submit', 'button', 'reset', 'image'].includes((f.type || '').toLowerCase())
  );
  const shown = fields.filter((f) => f.type === 'file' || visible(f));
  const searchForm = form && (
    form.getAttribute('role') === 'search' || form.querySelector('input[type=search]')
  );
  const isSearch = !!(searchForm || target.closest('[role=search]'));
  return {
    found: true,
    tag: target.tagName,
    // A <button> with no type attribute submits its form.
    type: (target.getAttribute('type') || (target.tagName === 'BUTTON' ? 'submit' : ''))
      .toLowerCase(),
    role: target.getAttribute('role') || '',
    label,
    is_clickable: !!clickable || target.tagName === 'INPUT',
    in_form: !!form,
    container: form ? 'form' : dialog ? 'dialog' : 'page',
    container_is_search: isSearch,
    container_file_inputs: shown.filter((f) => f.type === 'file').length,
    container_fillable: shown.filter((f) => f.type !== 'file').length,
    container_password_inputs: shown.filter((f) => f.type === 'password').length,
    container_textareas: shown.filter((f) => f.tagName === 'TEXTAREA').length,
  };
}
"""

# Runs in the top document. `point` is {x, y} in viewport coordinates (probe the
# element under it) or null (probe the focused element). Descends into
# same-origin iframes and shadow roots; reports `cross_origin_frame` when the
# target sits inside a frame it cannot read.
CONTROL_PROBE_JS = (
    r"""
(point) => {
  const facts = """
    + _ELEMENT_FACTS_JS.strip()
    + r""";
  const frameDoc = (f) => { try { return f.contentDocument; } catch (e) { return null; } };
  const isFrame = (e) => e && (e.tagName === 'IFRAME' || e.tagName === 'FRAME');
  let el = null;
  if (point) {
    let x = point.x, y = point.y;
    el = document.elementFromPoint(x, y);
    while (isFrame(el)) {
      const inner = frameDoc(el);
      if (!inner) return { found: true, cross_origin_frame: true };
      const r = el.getBoundingClientRect();
      x -= r.left + el.clientLeft; y -= r.top + el.clientTop;
      const next = inner.elementFromPoint(x, y);
      if (!next) break;
      el = next;
    }
    while (el && el.shadowRoot) {
      const next = el.shadowRoot.elementFromPoint(x, y);
      if (!next || next === el) break;
      el = next;
    }
  } else {
    el = document.activeElement;
    while (el) {
      if (isFrame(el)) {
        const inner = frameDoc(el);
        if (!inner) return { found: true, cross_origin_frame: true };
        if (!inner.activeElement) break;
        el = inner.activeElement;
      } else if (el.shadowRoot && el.shadowRoot.activeElement) {
        el = el.shadowRoot.activeElement;
      } else break;
    }
  }
  if (!el) return { found: false };
  return facts(el);
}
"""
)

# For Runtime.callFunctionOn with the resolved element as `this`.
ELEMENT_PROBE_FUNCTION = "function() { return (" + _ELEMENT_FACTS_JS.strip() + ")(this); }"

# Every visible clickable control in the page and its same-origin frames, with
# facts. The elements are kept on `window` so `CANDIDATE_POINT_JS(i)` can scroll
# to one and return its viewport centre for a real (trusted) mouse click.
SUBMIT_CANDIDATES_JS = (
    r"""
() => {
  const facts = """
    + _ELEMENT_FACTS_JS.strip()
    + r""";
  const selector = 'button, input[type=submit], input[type=image], input[type=button], ' +
    '[role=button], a[href]';
  const els = [];
  const out = [];
  const visit = (doc) => {
    for (const el of doc.querySelectorAll(selector)) {
      const r = el.getBoundingClientRect();
      const st = el.ownerDocument.defaultView.getComputedStyle(el);
      if (!r.width || !r.height || st.visibility === 'hidden' || st.display === 'none') continue;
      if (el.disabled) continue;
      els.push(el);
      out.push(facts(el));
    }
    for (const frame of doc.querySelectorAll('iframe, frame')) {
      let inner = null;
      try { inner = frame.contentDocument; } catch (e) { inner = null; }
      if (inner) visit(inner);
    }
  };
  visit(document);
  window.__jobbeeSubmitCandidates = els;
  return out;
}
"""
)

CANDIDATE_POINT_JS = r"""
(i) => {
  const el = (window.__jobbeeSubmitCandidates || [])[i];
  if (!el || !el.isConnected) return null;
  el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
  const r = el.getBoundingClientRect();
  let x = r.left + r.width / 2, y = r.top + r.height / 2, w = el.ownerDocument.defaultView;
  while (w !== w.top) {
    const f = w.frameElement;
    if (!f) return null;
    const b = f.getBoundingClientRect();
    x += b.left + f.clientLeft; y += b.top + f.clientTop; w = w.parent;
  }
  return {x, y};
}
"""

CONFIRMATION_RE = re.compile(
    r"thank(s| you) for (applying|your (application|interest))|application (has been |was )?"
    r"(submitted|received|sent)|we('ve| have) received your application|your application "
    r"(has been |was )?(sent|submitted|received)|successfully (applied|submitted)",
    re.IGNORECASE,
)


def pick_final_submit(candidates: list[ControlFacts]) -> tuple[int | None, str]:
    """Choose the one control that submits the application.

    Returns (index, "") or (None, why-not). Several identical copies of the same
    label (e.g. a sticky header and footer button) count as one.
    """

    matches = [(i, f) for i, f in enumerate(candidates) if final_submit_reason(f)]
    strong = [
        (i, f)
        for i, f in matches
        if _STRONG_FINAL_LABEL.match(" ".join(f.get("label", "").split()))
    ]
    pool = strong or matches
    labels = {" ".join(f.get("label", "").split()).lower() for _, f in pool}
    if not pool:
        return None, "no final submit button found"
    if len(labels) > 1:
        return None, "several different submit buttons: " + ", ".join(sorted(labels))[:120]
    return pool[0][0], ""


# --- URL policy ---------------------------------------------------------------

_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


def url_refusal_reason(url: str) -> str | None:
    """Why `navigate` must not open this URL, or None if it may."""

    raw = url.strip()
    if raw in {"back", "forward", "reload", "about:blank"}:
        return None
    # Resolve exactly as the driver does: only a scheme-less URL gets https://.
    effective = raw if urlsplit(raw).scheme else f"https://{raw}"
    parts = urlsplit(effective)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return f"scheme {parts.scheme!r} is not allowed"
    host = (parts.hostname or "").lower()
    if not host:
        return "URL has no host"
    if host in _BLOCKED_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        return "local and internal hosts are not allowed"
    octets = host.split(".")
    if len(octets) == 4 and all(o.isdigit() for o in octets):
        a, b = int(octets[0]), int(octets[1])
        if (
            a in {10, 127, 0}
            or (a == 169 and b == 254)
            or (a == 172 and 16 <= b <= 31)
            or (a == 192 and b == 168)
        ):
            return "private network addresses are not allowed"
    return None


# --- sensitive fields ----------------------------------------------------------

# Things JobBee never types into a form, whoever asks.
SENSITIVE_RE = re.compile(
    r"\b(sin|ssn|social (security|insurance)( number)?|bank|routing number|account number|"
    r"credit card|card number|cvv|password|passport number|driver'?s licen[cs]e number)\b",
    re.IGNORECASE,
)

# What a form field is called (labels, aria, placeholder, name, id) and its type.
# For Runtime.callFunctionOn with the field, or a label for it, as `this`.
FIELD_FACTS_FUNCTION = r"""function () {
  const el = this.tagName === 'LABEL' && this.control ? this.control : this;
  const text = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const doc = el.ownerDocument;
  const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map((id) => {
    const node = id && doc.getElementById(id);
    return node ? text(node.innerText) : '';
  });
  const labels = el.labels ? [...el.labels].map((l) => text(l.innerText)) : [];
  return {
    type: (el.type || '').toLowerCase(),
    autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
    name: [el.getAttribute('aria-label'), ...labelled, ...labels, el.getAttribute('placeholder'),
           el.getAttribute('name'), el.id].filter(Boolean).map(text).join(' | ').slice(0, 300),
  };
}"""

# The same facts for the focused field, through same-origin frames and shadow roots.
FOCUSED_FIELD_JS = (
    r"""(() => {
  let el = document.activeElement;
  while (el) {
    if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
      let inner = null;
      try { inner = el.contentDocument; } catch (e) {}
      if (!inner || !inner.activeElement) break;
      el = inner.activeElement;
    } else if (el.shadowRoot && el.shadowRoot.activeElement) {
      el = el.shadowRoot.activeElement;
    } else break;
  }
  return el ? ("""
    + FIELD_FACTS_FUNCTION
    + r""").call(el) : null;
})()"""
)

PASSWORD_FIELD_MESSAGE = (
    "Blocked by JobBee: that's a password field. Sign-in and sign-up passwords are filled "
    "only by `site_account`; never type one yourself."
)
SENSITIVE_FIELD_MESSAGE = (
    "Blocked by JobBee: this field asks for {what}, which JobBee never fills in. If the form "
    "can't go on without it, call `request_human_action` with kind 'other' and name the field."
)


def sensitive_field_message(field: dict | None) -> str | None:
    """Why the agent may not type into this field, or None if it may."""

    if not field:
        return None
    if field.get("type") == "password" or "password" in (field.get("autocomplete") or ""):
        return PASSWORD_FIELD_MESSAGE
    if (field.get("autocomplete") or "").startswith("cc-"):
        return SENSITIVE_FIELD_MESSAGE.format(what="card details")
    match = SENSITIVE_RE.search(field.get("name") or "")
    if match is None:
        return None
    if match.group(0).lower() == "password":
        return PASSWORD_FIELD_MESSAGE
    return SENSITIVE_FIELD_MESSAGE.format(what=match.group(0).lower())
