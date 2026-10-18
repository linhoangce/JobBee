"""Shared types for the importer pipeline.

A `Draft` is everything an importer can contribute to a profile, plus the
source label we'll show in the review UI ("from resume", "from github",
etc). `data` mirrors `ProfileUpdate` field names so the merge layer doesn't
have to do any translation.

Importers are intentionally async so a future URL importer (or anything that
fans out HTTP) can run alongside the LLM call without blocking the request
loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ImporterError(RuntimeError):
    """Raised when an importer can't produce any useful data.

    The router catches this and surfaces it to the user without aborting
    other importers; one bad source shouldn't take down the whole import.
    """


@dataclass(slots=True)
class Draft:
    source: str
    data: dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> None:
        """Set a field but skip empty strings / empty containers / None."""

        if value is None:
            return
        if isinstance(value, str) and not value.strip():
            return
        if isinstance(value, (list, dict)) and len(value) == 0:
            return
        self.data[key] = value
