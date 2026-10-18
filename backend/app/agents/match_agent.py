"""Match agent: score a job description against the user's profile + resume.

Uses the configured LLM with a strict JSON schema. Keeps prompts small so
Gemma/Gemini free-tier stays fast.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..config import get_settings
from ..llm.base import ChatMessage, LLMError, LLMProvider, strip_model_artifacts
from ..models import Document, Job, User
from .match_fit import apply_fit_adjustments

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You score how well a job fits a candidate. Reply with JSON only — no prose, "
    "no code fences, no <thought> tags."
)

_PROMPT = """\
Score this job for the candidate on a 0-100 scale (100 = perfect fit).

Scoring rubric (apply strictly):
- Heavily weight target roles, skills, and seniority fit.
- Heavily penalize location mismatch: if the job is on-site in a region the
  candidate did NOT list, score below 40 even if skills match.
- Reward remote jobs when the candidate accepts remote or lists matching regions.
- Respect work modes: remote-only jobs score lower if the candidate wants on-site only.

Return JSON:
{{
  "score": <number 0-100>,
  "reason": "<=240 chars explaining the score>"
}}

Candidate profile:
{profile_block}

Job:
Title: {title}
Company: {company}
Location: {location}
Description (excerpt):
{description}
"""


class MatchResult(BaseModel):
    score: float = Field(ge=0, le=100)
    reason: str = Field(max_length=500)


def _profile_block(user: User, resume: Document | None) -> str:
    lines = [
        f"Name: {user.name or '(unknown)'}",
        f"Headline: {user.headline or '(none)'}",
        f"Current location: {user.location or '(none)'}",
        f"Target roles: {', '.join(user.target_roles) or '(none)'}",
        f"Target locations: {', '.join(user.target_locations) or '(any)'}",
        f"Skills: {', '.join(user.skills) or '(none)'}",
        f"Work modes: {', '.join(user.work_modes) or '(any)'}",
    ]
    if user.summary:
        lines.append(f"Summary: {user.summary[:200]}")
    if resume and resume.content:
        lines.append(f"Resume excerpt: {resume.content[:300]}")
    return "\n".join(lines)


def _parse_json(text: str) -> dict[str, Any] | None:
    text = strip_model_artifacts(text)
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


async def score_job(
    job: Job,
    user: User,
    *,
    provider: LLMProvider,
    resume: Document | None = None,
) -> MatchResult:
    desc = (job.description or "")[:600]
    if not desc.strip():
        desc = f"{job.title} at {job.company or 'unknown company'}."

    prompt = _PROMPT.format(
        profile_block=_profile_block(user, resume),
        title=job.title,
        company=job.company or "Unknown",
        location=job.location or "Unknown",
        description=desc,
    )

    try:
        settings = get_settings()
        resp = await provider.chat(
            [
                ChatMessage(role="system", content=_SYSTEM),
                ChatMessage(role="user", content=prompt),
            ],
            temperature=0.1,
            max_tokens=160,
            response_format={"type": "json_object"},
            thinking=True,
            timeout_s=settings.llm_timeout_s,
        )
    except LLMError as e:
        raise LLMError(f"Match scoring failed: {e}") from e

    parsed = _parse_json(resp.content)
    if parsed is None:
        logger.warning("Match LLM non-JSON: %r", resp.content[:200])
        final_score, final_reason = apply_fit_adjustments(
            user, job, 50.0, "Could not parse model score; defaulting to neutral."
        )
        return MatchResult(score=final_score, reason=final_reason)

    try:
        base = MatchResult.model_validate(parsed)
    except ValidationError:
        score = parsed.get("score")
        reason = parsed.get("reason") or "Partial model response."
        try:
            base = MatchResult(score=float(score), reason=str(reason)[:500])
        except (TypeError, ValueError):
            base = MatchResult(score=50.0, reason="Invalid model score; defaulting to neutral.")

    final_score, final_reason = apply_fit_adjustments(user, job, base.score, base.reason)
    return MatchResult(score=final_score, reason=final_reason)


def score_job_heuristic(job: Job, user: User) -> MatchResult:
    """Fast keyword + location/work-mode score without calling the LLM."""

    blob = f"{job.title} {(job.description or '')[:1200]}".lower()
    hits = 0
    score = 30.0

    for role in (user.target_roles or [])[:6]:
        role_l = role.lower().strip()
        if not role_l:
            continue
        if role_l in blob or any(part in blob for part in role_l.split() if len(part) > 3):
            hits += 1
            score += 14.0

    for skill in (user.skills or [])[:24]:
        skill_l = skill.lower().strip()
        if skill_l and skill_l in blob:
            hits += 1
            score += 5.0

    if hits == 0:
        reason = "Limited overlap with your roles and skills."
        score = 22.0
    else:
        reason = f"Matched {hits} role/skill keyword(s)."
        score = min(85.0, score)

    final_score, final_reason = apply_fit_adjustments(user, job, score, reason)
    return MatchResult(score=final_score, reason=final_reason)
