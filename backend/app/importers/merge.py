"""Combine multiple `Draft`s into one normalized profile draft.

Strategy:
- Scalar fields (name, headline, email, location, ...) take the first
  non-empty value in the priority order the caller passed in.
- List fields (target_roles, skills, ...) union across drafts, preserving
  the order of the higher-priority draft. The source recorded is the *first*
  draft that contributed any element.
- Dict fields (links) merge key-by-key, higher priority wins on conflict.

The returned `MergedDraft` carries a per-field source map so the UI can
show a "from <source>" pill on each pre-filled input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import Draft

_SCALAR_FIELDS = {
    "name",
    "email",
    "headline",
    "location",
    "years_experience",
    "min_salary",
    "summary",
}

_LIST_FIELDS = {
    "target_roles",
    "target_locations",
    "work_modes",
    "skills",
}

_DICT_FIELDS = {"links"}


@dataclass(slots=True)
class MergedDraft:
    data: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)


def merge_drafts(drafts: list[Draft]) -> MergedDraft:
    """Merge drafts in caller-supplied priority order (highest first)."""

    merged = MergedDraft()

    for draft in drafts:
        for key, value in draft.data.items():
            if key in _SCALAR_FIELDS:
                if key not in merged.data:
                    merged.data[key] = value
                    merged.sources[key] = draft.source
            elif key in _LIST_FIELDS:
                existing: list[Any] = merged.data.get(key, [])
                # Deduplicate case-insensitively for strings; otherwise by value.
                seen = {
                    (item.lower() if isinstance(item, str) else item)
                    for item in existing
                }
                added = False
                for item in value:
                    key_item = item.lower() if isinstance(item, str) else item
                    if key_item in seen:
                        continue
                    existing.append(item)
                    seen.add(key_item)
                    added = True
                if existing:
                    merged.data[key] = existing
                    if added and key not in merged.sources:
                        merged.sources[key] = draft.source
            elif key in _DICT_FIELDS:
                existing_dict: dict[str, Any] = merged.data.get(key, {})
                added_any = False
                for k, v in value.items():
                    if k not in existing_dict and v:
                        existing_dict[k] = v
                        added_any = True
                if existing_dict:
                    merged.data[key] = existing_dict
                    if added_any and key not in merged.sources:
                        merged.sources[key] = draft.source
            # Anything else: ignore — schema mismatch with profile.

    return merged
