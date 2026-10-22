"""Spike scorecard shared by CLI and UI runs: one JSON line per reviewed application."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from ..config import BACKEND_DIR

SPIKE_DIR = BACKEND_DIR / "data" / "spike"
SCORECARD = SPIKE_DIR / "scorecard.jsonl"


def append_scorecard(row: dict) -> None:
    SPIKE_DIR.mkdir(parents=True, exist_ok=True)
    with SCORECARD.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def read_scorecard() -> list[dict]:
    if not SCORECARD.is_file():
        return []
    return [json.loads(line) for line in SCORECARD.read_text(encoding="utf-8").splitlines() if line]


def submit_request_rows(observed: list[dict]) -> list[dict]:
    """Dedupe POST/PUT/PATCH requests (from the driver's network log) to method/host/path."""

    rows: list[dict] = []
    seen: set[tuple] = set()
    for row in observed:
        parts = urlsplit(row.get("url", ""))
        key = (row.get("method"), parts.hostname, parts.path)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "method": row.get("method"),
                "host": parts.hostname,
                "path": parts.path,
                "status": row.get("status"),
            }
        )
    return rows
