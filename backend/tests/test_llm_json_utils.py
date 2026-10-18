"""Tests for shared LLM JSON extraction."""

from __future__ import annotations

from app.llm.json_utils import extract_json_object


def test_extract_json_inside_closed_thought() -> None:
    payload = (
        '<thought>{"name":"Linh Hoang","email":"lin@example.com","skills":["python"]}</thought>'
    )
    parsed = extract_json_object(payload)
    assert parsed is not None
    assert parsed["name"] == "Linh Hoang"
    assert parsed["email"] == "lin@example.com"


def test_extract_truncated_gemma_style() -> None:
    truncated = (
        '{\n  "name": "Linh Hoang",\n  "headline": "Software QA Engineer & AI Developer",\n'
        '  "email": "pelinhoang.ce@gmail.com",\n  "years_experience": 6.17,\n'
        '  "summary": "QA Engineer with expertise in building automated test framework'
    )
    parsed = extract_json_object(truncated)
    assert parsed is not None
    assert parsed["name"] == "Linh Hoang"
    assert parsed["email"] == "pelinhoang.ce@gmail.com"
    assert parsed["years_experience"] == 6.17
