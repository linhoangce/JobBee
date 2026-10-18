"""Unit tests for tailor_agent JSON salvage."""

from __future__ import annotations

from app.agents.tailor_agent import (
    _extract_json_object,
    _fallback_cover_letter,
    _salvage_string_field,
)


def test_extract_json_from_code_fence() -> None:
    text = """```json
{"resume_text": "Lin Ho\\nBackend engineer with Python, FastAPI, and SQLAlchemy experience across multiple production systems and teams."}
```"""
    parsed = _extract_json_object(text, "resume_text")
    assert parsed is not None
    assert len(parsed["resume_text"]) >= 80


def test_salvage_truncated_resume_json() -> None:
    truncated = (
        '{\n  "resume_text": "Linh Hoang\\nSoftware QA Engineer with Playwright, Jenkins, '
        "and Python experience across multiple production systems and teams."
    )
    value = _salvage_string_field(truncated, "resume_text")
    assert value is not None
    assert len(value) >= 80


def test_salvage_malformed_opening_quote() -> None:
    text = (
        '{\\n  "resume_text": \\"Linh Hoang\\nSoftware QA Engineer with Playwright, Jenkins, '
        "and Python experience across multiple production systems."
    )
    value = _salvage_string_field(text, "resume_text")
    assert value is not None
    assert "Linh" in value


def test_fallback_cover_letter() -> None:
    from app.models import Job, User

    user = User(name="Linh Hoang", skills=["python", "playwright"])
    job = Job(title="QA Engineer", company="Acme", url="https://example.com", source="test")
    letter = _fallback_cover_letter(user, job)
    assert len(letter) >= 80
    assert "Acme" in letter
