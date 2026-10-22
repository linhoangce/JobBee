"""The user's own Chrome, launched headed on JobBee's dedicated profile."""

from __future__ import annotations

import os
from pathlib import Path

from ..config import BACKEND_DIR

PROFILE_DIR = BACKEND_DIR / "data" / "browser-chrome"


class ChromeNotFoundError(RuntimeError):
    pass


def find_chrome() -> str:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    raise ChromeNotFoundError("Google Chrome not found; install it to use the apply agent.")


def new_browser_session(
    *, profile_dir: Path = PROFILE_DIR, har_path: Path | None = None, headless: bool = False
):
    from browser_use.browser.session import BrowserSession

    options: dict = {}
    if har_path is not None:
        # browser-use records HTTPS only, and only the first tab; bodies are omitted.
        options.update(record_har_path=str(har_path), record_har_content="omit")
    return BrowserSession(
        executable_path=find_chrome(),
        headless=headless,
        user_data_dir=str(profile_dir),
        highlight_elements=False,
        window_size={"width": 1280, "height": 960},
        **options,
    )
