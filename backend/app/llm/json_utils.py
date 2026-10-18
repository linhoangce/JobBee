"""Robust JSON extraction from LLM text (truncation, fences, thinking tags)."""

from __future__ import annotations

import json
import re
from typing import Any

from .base import strip_model_artifacts

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_THOUGHT_BLOCK_RE = re.compile(r"<thought>([\s\S]*?)</thought>", re.IGNORECASE)
_THOUGHT_OPEN_RE = re.compile(r"<thought>([\s\S]*)", re.IGNORECASE)


def _unescape_json_string(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")


def salvage_string_field(text: str, key: str) -> str | None:
    """Read a JSON string field from complete or truncated model output."""

    chunk = text[text.find("{") :] if "{" in text else text
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"(.*)', chunk, re.DOTALL)
    if not match:
        return None

    raw = match.group(1)
    end = 0
    escaped = False
    for i, ch in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            end = i
            break

    value = _unescape_json_string(raw[:end] if end else raw).strip()
    return value or None


def _payloads_to_try(text: str) -> list[str]:
    """Candidate strings to parse, in priority order."""

    seen: set[str] = set()
    out: list[str] = []

    def add(candidate: str | None) -> None:
        if not candidate:
            return
        cleaned = candidate.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)

    add(text)

    for match in _THOUGHT_BLOCK_RE.finditer(text):
        add(match.group(1))

    open_thought = _THOUGHT_OPEN_RE.search(text)
    if open_thought:
        add(open_thought.group(1))

    add(strip_model_artifacts(text))

    fence = _JSON_FENCE_RE.search(text)
    if fence:
        add(fence.group(1).strip())

    # Open code fence without a closing ```
    open_fence = re.search(r"```(?:json)?\s*([\s\S]*)", text, re.IGNORECASE)
    if open_fence:
        add(open_fence.group(1).strip())

    return out


def _close_truncated_json(chunk: str) -> dict[str, Any] | None:
    """Try closing an truncated object by appending quote/brace suffixes."""

    if "{" not in chunk:
        return None

    body = chunk[chunk.find("{") :]
    suffixes = (
        '"}',
        '"]}',
        '"]]}',
        '"}]}',
        '"}]}',
        "}",
        'null}',
        '""}',
        '"]',
        '"',
    )
    for suffix in suffixes:
        try:
            obj = json.loads(body + suffix)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def salvage_json_object(
    text: str,
    *,
    string_fields: tuple[str, ...] = (),
    list_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Field-by-field recovery when the payload is truncated or malformed."""

    closed = _close_truncated_json(text)
    if closed:
        return closed

    chunk = text[text.find("{") :] if "{" in text else text
    out: dict[str, Any] = {}

    keys = string_fields or (
        "name",
        "headline",
        "email",
        "location",
        "summary",
    )
    for key in keys:
        value = salvage_string_field(chunk, key)
        if value is not None:
            out[key] = value

    num = re.search(r'"years_experience"\s*:\s*(\d+(?:\.\d+)?)', chunk)
    if num:
        out["years_experience"] = float(num.group(1))

    for key in list_fields or ("target_roles", "skills"):
        match = re.search(rf'"{re.escape(key)}"\s*:\s*\[(.*?)\]', chunk, re.DOTALL)
        if match:
            out[key] = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))

    links_match = re.search(r'"links"\s*:\s*\{(.*?)\}', chunk, re.DOTALL)
    if links_match:
        links: dict[str, str] = {}
        for k, v in re.findall(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"', links_match.group(1)):
            links[k] = v.replace('\\"', '"')
        out["links"] = links

    return out


def extract_json_object(
    text: str,
    *,
    string_fields: tuple[str, ...] = (),
    list_fields: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Best-effort parse of a JSON object from arbitrary LLM output."""

    for candidate in _payloads_to_try(text):
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        fence = _JSON_FENCE_RE.search(candidate)
        if fence:
            inner = fence.group(1).strip()
            try:
                result = json.loads(inner)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                salvaged = salvage_json_object(
                    inner, string_fields=string_fields, list_fields=list_fields
                )
                if salvaged:
                    return salvaged

        start = candidate.find("{")
        if start == -1:
            continue

        depth = 0
        for i in range(start, len(candidate)):
            ch = candidate[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    fragment = candidate[start : i + 1]
                    try:
                        result = json.loads(fragment)
                        if isinstance(result, dict):
                            return result
                    except json.JSONDecodeError:
                        break

        salvaged = salvage_json_object(
            candidate, string_fields=string_fields, list_fields=list_fields
        )
        if salvaged:
            return salvaged

    return None


def json_looks_truncated(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and not stripped.endswith("}")
