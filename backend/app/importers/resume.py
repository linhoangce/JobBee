"""Extract structured profile data from a resume's plain text.

The pipeline:

  1. Caller has already turned the uploaded file into plain text (via
     `app.services.text_extract`).
  2. We hand that text to the configured `LLMProvider` with a JSON-only
     prompt and `response_format={"type": "json_object"}`. The Ollama
     provider translates that to `format: "json"` natively.
  3. We parse + validate the response with `pydantic`, drop any fields the
     model invented, and return a `Draft`.

The prompt is intentionally narrow: we only ask for fields that fit the
profile schema. Anything the LLM hallucinates beyond that is discarded by
the Pydantic validator.

Resilience: truncated or partially invalid JSON still yields whatever fields
we could salvage — the user can fill any gap by hand. We retry once when
the first response looks truncated.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..config import get_settings
from ..llm.base import ChatMessage, LLMError, LLMProvider
from ..llm.json_utils import extract_json_object, json_looks_truncated
from .base import Draft, ImporterError

logger = logging.getLogger(__name__)

_MAX_RESUME_CHARS = 20_000
_MEANINGFUL_FIELDS = ("name", "email", "headline", "summary", "skills", "target_roles")


class _ResumeFields(BaseModel):
    """The shape we accept back from the LLM. All fields optional."""

    name: str | None = None
    headline: str | None = None
    email: str | None = None
    location: str | None = None
    years_experience: float | None = Field(default=None, ge=0, le=80)
    summary: str | None = None
    target_roles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)


_PROMPT_SYSTEM = (
    "You extract structured candidate-profile data from a resume. "
    "Reply with a single minified JSON object ONLY. No prose, no code fences, "
    "no commentary, and no <thought> tags. Keep summary under 240 characters."
)

_PROMPT_USER_TEMPLATE = """\
Extract these fields from the resume below. Output one JSON object matching this shape (omit fields you can't fill):

{{
  "name": "Full name",
  "headline": "One-line professional title",
  "email": "Primary email if present",
  "location": "City, region/country if present",
  "years_experience": <number>,
  "summary": "<=240 chars: strengths and target next role",
  "target_roles": ["2-4 plausible next job titles"],
  "skills": ["lowercased hard skills only"],
  "links": {{ "linkedin": "...", "github": "...", "portfolio": "..." }}
}}

Rules:
- skills: lowercase, deduplicated, no soft skills.
- target_roles: title-cased, search-friendly.
- Output ONLY the JSON object. Do not wrap it in markdown or thinking tags.

Resume:
{resume_text}
"""

_RETRY_USER_TEMPLATE = """\
The previous response was truncated. Return ONLY a compact JSON object (no extra whitespace) with as many of these fields as you can fit:

{{"name":"","headline":"","email":"","location":"","years_experience":0,"summary":"","target_roles":[],"skills":[],"links":{{}}}}

Keep summary <=160 characters. Resume:
{resume_text}
"""


def _fields_from_parsed(parsed: dict[str, Any]) -> _ResumeFields:
    try:
        return _ResumeFields.model_validate(parsed)
    except ValidationError as e:
        logger.warning("Resume LLM returned partially invalid JSON: %s", e)
        safe: dict[str, Any] = {}
        for key, value in parsed.items():
            if key not in _ResumeFields.model_fields:
                continue
            try:
                _ResumeFields.model_validate({key: value})
                safe[key] = value
            except ValidationError:
                continue
        return _ResumeFields.model_validate(safe)


def _has_meaningful_fields(parsed: dict[str, Any]) -> bool:
    for key in _MEANINGFUL_FIELDS:
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and any(str(v).strip() for v in value):
            return True
    return False


def _parse_llm_content(content: str) -> dict[str, Any] | None:
    return extract_json_object(
        content,
        string_fields=(
            "name",
            "headline",
            "email",
            "location",
            "summary",
        ),
        list_fields=("target_roles", "skills"),
    )


def _draft_from_fields(fields: _ResumeFields, *, source: str) -> Draft:
    draft = Draft(source=source)
    draft.set("name", fields.name)
    draft.set("headline", fields.headline)
    draft.set("email", fields.email)
    draft.set("location", fields.location)
    draft.set("years_experience", fields.years_experience)
    draft.set("summary", fields.summary)
    draft.set(
        "target_roles",
        _dedupe_preserve_order([r.strip() for r in fields.target_roles if r.strip()]),
    )
    draft.set(
        "skills",
        _dedupe_preserve_order([s.strip().lower() for s in fields.skills if s.strip()]),
    )
    draft.set("links", {k: v for k, v in fields.links.items() if v})
    return draft


async def import_resume(
    text: str,
    *,
    provider: LLMProvider,
    label: str = "resume",
) -> Draft:
    """Run the LLM extraction and return a `Draft` sourced from the resume."""

    text = (text or "").strip()
    if not text:
        raise ImporterError("Resume is empty after text extraction.")
    if len(text) > _MAX_RESUME_CHARS:
        text = text[:_MAX_RESUME_CHARS]

    settings = get_settings()
    messages = [
        ChatMessage(role="system", content=_PROMPT_SYSTEM),
        ChatMessage(role="user", content=_PROMPT_USER_TEMPLATE.format(resume_text=text)),
    ]

    try:
        resp = await provider.chat(
            messages,
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
            thinking=False,
            timeout_s=settings.llm_long_timeout_s,
        )
    except LLMError as e:
        raise ImporterError(f"LLM extraction failed: {e}") from e

    content = resp.content or ""
    parsed = _parse_llm_content(content)

    if (not parsed or not _has_meaningful_fields(parsed)) and json_looks_truncated(content):
        logger.info("Resume LLM output looks truncated; retrying with compact JSON prompt.")
        try:
            retry = await provider.chat(
                [
                    ChatMessage(role="system", content=_PROMPT_SYSTEM),
                    ChatMessage(
                        role="user",
                        content=_RETRY_USER_TEMPLATE.format(resume_text=text),
                    ),
                ],
                temperature=0.0,
                max_tokens=8192,
                response_format={"type": "json_object"},
                thinking=False,
                timeout_s=settings.llm_long_timeout_s,
            )
        except LLMError as e:
            logger.warning("Resume LLM retry failed: %s", e)
        else:
            retry_parsed = _parse_llm_content(retry.content or "")
            if retry_parsed and _has_meaningful_fields(retry_parsed):
                parsed = retry_parsed
                content = retry.content or content

    if not parsed or not _has_meaningful_fields(parsed):
        logger.warning("Resume LLM returned non-JSON: %r", content[:300])
        raise ImporterError(
            "The model didn't return JSON we could parse. "
            "Try a different model in Settings, or fill the profile manually."
        )

    if json_looks_truncated(content):
        logger.warning(
            "Resume LLM JSON looks truncated; salvaged %d field(s) from partial output.",
            len(parsed),
        )

    fields = _fields_from_parsed(parsed)
    return _draft_from_fields(fields, source=label)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
