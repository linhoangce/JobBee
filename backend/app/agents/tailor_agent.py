"""Tailor agent: rewrite resume and cover letter for a specific job."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..config import get_settings
from ..llm.base import ChatMessage, LLMError, LLMProvider, strip_model_artifacts
from ..models import Document, Job, User

logger = logging.getLogger(__name__)

_MAX_RESUME_CHARS = 6_000
_MAX_JD_CHARS = 3_000
_MIN_FIELD_CHARS = 80

_SYSTEM = (
    "You tailor job application documents. Reply with JSON only — no prose, "
    "no code fences, no <thought> tags."
)

_RESUME_PROMPT = """\
Tailor this resume for the job below. Keep all facts truthful; emphasize relevant skills.

Return JSON:
{{"resume_text": "<full tailored resume plain text>"}}

Candidate:
{profile_block}

Source resume:
{resume_text}

Job:
Title: {title}
Company: {company}
Location: {location}
Description:
{description}
"""

_COVER_PROMPT = """\
Write a cover letter for this job using the tailored resume below.

Return JSON:
{{"cover_text": "<3-4 paragraph cover letter>"}}

Candidate: {name}
Job: {title} at {company}
Location: {location}

Tailored resume excerpt:
{resume_excerpt}
"""


@dataclass(slots=True)
class TailorResult:
    resume_text: str
    cover_text: str
    warnings: list[str] = field(default_factory=list)


def _profile_block(user: User) -> str:
    lines = [
        f"Name: {user.name or '(unknown)'}",
        f"Headline: {user.headline or '(none)'}",
        f"Email: {user.email or '(none)'}",
        f"Skills: {', '.join(user.skills[:20]) or '(none)'}",
    ]
    if user.summary:
        lines.append(f"Summary: {user.summary[:300]}")
    return "\n".join(lines)


def _unescape_json_string(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")


def _salvage_string_field(text: str, key: str) -> str | None:
    """Pull one JSON string field from complete or truncated output."""

    chunk = text[text.find("{") :] if "{" in text else text
    match = re.search(rf'"{key}"\s*:\s*\\?"(.*)', chunk, re.DOTALL)
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


def _salvage_tailor_json(text: str, *keys: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in keys:
        value = _salvage_string_field(text, key)
        if value:
            out[key] = value
    return out


def _extract_json_object(text: str, *keys: str) -> dict[str, Any] | None:
    text = strip_model_artifacts(text)
    if not text:
        return None

    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        inner = fence.group(1).strip()
        try:
            result = json.loads(inner)
            return result if isinstance(result, dict) else None
        except json.JSONDecodeError:
            salvaged = _salvage_tailor_json(inner, *keys)
            return salvaged or None

    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        result = json.loads(candidate)
                        return result if isinstance(result, dict) else None
                    except json.JSONDecodeError:
                        break

    salvaged = _salvage_tailor_json(text, *keys)
    return salvaged or None


def _fallback_cover_letter(user: User, job: Job) -> str:
    name = user.name or "Candidate"
    company = job.company or "your company"
    title = job.title
    skills = ", ".join((user.skills or [])[:6]) or "my technical background"
    return (
        f"Dear {company} hiring team,\n\n"
        f"I am writing to apply for the {title} position. "
        f"My background in {skills} aligns well with what you are looking for, "
        f"and I am excited about the opportunity to contribute to your team.\n\n"
        f"In my recent roles I have focused on delivering reliable, high-quality work "
        f"and collaborating across engineering and product. I would welcome the chance "
        f"to bring that experience to {company}.\n\n"
        f"Thank you for your time and consideration. I look forward to hearing from you.\n\n"
        f"Sincerely,\n{name}"
    )


async def _llm_json_field(
    provider: LLMProvider,
    *,
    system: str,
    prompt: str,
    field: str,
    max_tokens: int,
    timeout_s: float,
) -> tuple[str | None, bool]:
    """Return (field value, truncated)."""

    resp = await provider.chat(
        [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=prompt),
        ],
        temperature=0.2,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        thinking=False,
        timeout_s=timeout_s,
    )
    content = resp.content or ""
    truncated = not content.rstrip().endswith("}")
    parsed = _extract_json_object(content, field)
    if not parsed:
        logger.warning("Tailor LLM non-JSON for %s: %r", field, content[:400])
        return None, truncated

    value = str(parsed.get(field) or "").strip()
    if len(value) < _MIN_FIELD_CHARS:
        salvaged = _salvage_string_field(content, field)
        if salvaged and len(salvaged) >= _MIN_FIELD_CHARS:
            return salvaged, True
        return None, truncated
    return value, truncated


async def tailor_for_job(
    user: User,
    job: Job,
    *,
    resume: Document,
    provider: LLMProvider,
    cover_letter: Document | None = None,
) -> TailorResult:
    """Generate tailored resume + cover letter text for one application."""

    resume_text = (resume.content or "").strip()
    if not resume_text:
        raise ValueError("Base resume has no text content.")

    desc = (job.description or "").strip()
    if not desc:
        desc = f"{job.title} at {job.company or 'the company'}."

    warnings: list[str] = []
    if cover_letter and cover_letter.content:
        warnings.append("Existing cover letter was ignored; a new one was generated for this job.")

    settings = get_settings()
    resume_prompt = _RESUME_PROMPT.format(
        profile_block=_profile_block(user),
        resume_text=resume_text[:_MAX_RESUME_CHARS],
        title=job.title,
        company=job.company or "Unknown",
        location=job.location or "Unknown",
        description=desc[:_MAX_JD_CHARS],
    )

    try:
        tailored_resume, resume_truncated = await _llm_json_field(
            provider,
            system=_SYSTEM,
            prompt=resume_prompt,
            field="resume_text",
            max_tokens=3072,
            timeout_s=settings.llm_long_timeout_s,
        )
    except LLMError as e:
        raise LLMError(f"Resume tailoring failed: {e}") from e

    if not tailored_resume:
        raise LLMError(
            "The model didn't return a usable tailored resume. "
            "Try a different model in Settings, or retry tailoring."
        )

    if resume_truncated:
        warnings.append("Tailored resume may be truncated; review before applying.")

    cover_prompt = _COVER_PROMPT.format(
        name=user.name or "Candidate",
        title=job.title,
        company=job.company or "the company",
        location=job.location or "Unknown",
        resume_excerpt=tailored_resume[:1500],
    )

    try:
        cover_text, cover_truncated = await _llm_json_field(
            provider,
            system=_SYSTEM,
            prompt=cover_prompt,
            field="cover_text",
            max_tokens=1024,
            timeout_s=settings.llm_timeout_s,
        )
    except LLMError as e:
        logger.warning("Cover letter LLM failed, using template: %s", e)
        cover_text = None
        cover_truncated = False

    if not cover_text:
        cover_text = _fallback_cover_letter(user, job)
        warnings.append("Cover letter used a template fallback; edit it before applying.")
    elif cover_truncated:
        warnings.append("Cover letter may be truncated; review before applying.")

    return TailorResult(
        resume_text=tailored_resume,
        cover_text=cover_text,
        warnings=warnings,
    )
