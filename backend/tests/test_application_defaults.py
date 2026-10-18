"""Application defaults matching tests."""

from __future__ import annotations

from app.services.application_defaults import (
    answer_candidates,
    default_application_defaults,
    infer_country,
    match_option,
    merge_application_defaults,
    resolve_canonical_key,
    resolve_value,
)


def test_resolve_canonical_key_work_auth() -> None:
    assert resolve_canonical_key("Are you legally authorized to work in Canada? *") == "work_authorization"


def test_resolve_canonical_key_sponsorship() -> None:
    assert (
        resolve_canonical_key("Do you, now or in the future, require sponsorship to work in Canada?")
        == "requires_sponsorship"
    )


def test_resolve_canonical_key_referral() -> None:
    assert resolve_canonical_key("How did you hear about PocketHealth?") == "referral_source"


def test_infer_country_from_location() -> None:
    assert infer_country("Toronto, ON") == "Canada"
    assert infer_country("Austin, TX") == "United States"


def test_merge_application_defaults_fills_sensible_defaults() -> None:
    merged = merge_application_defaults(None, location="Toronto, ON")
    assert merged["work_authorization"] == "yes_no_restrictions"
    assert merged["requires_sponsorship"] == "no"
    assert merged["work_country"] == "Canada"
    assert merged["country"] == "Canada"
    assert merged["city"] == "Toronto"
    assert merged["province"] == "ON"


def test_match_option_fuzzy() -> None:
    options = ["Yes, with no restrictions", "No"]
    picked = match_option(options, answer_candidates("work_authorization", "yes_no_restrictions"))
    assert picked == "Yes, with no restrictions"


def test_resolve_value_uses_saved_answer() -> None:
    value = resolve_value(
        "referral_source",
        defaults={"referral_source": "Other"},
        saved={"how did you hear about pockethealth?": "LinkedIn Job Posting"},
        label="How did you hear about PocketHealth?",
    )
    assert value == "LinkedIn Job Posting"


def test_resolve_value_linkedin_referral_fallback() -> None:
    value = resolve_value(
        "referral_source",
        defaults={},
        saved={},
        label="How did you hear about us?",
        job_url="https://www.linkedin.com/jobs/view/123",
    )
    assert value == "LinkedIn Job Posting"
