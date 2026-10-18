"""Canonical application-form answers and label → value matching."""

from __future__ import annotations

import re
from typing import Any

_LABEL_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("legally authorized", "authorized to work", "eligible to work", "right to work"),
        "work_authorization",
    ),
    (
        ("require sponsorship", "need sponsorship", "visa sponsorship", "immigration sponsorship"),
        "requires_sponsorship",
    ),
    (
        ("how did you hear", "where did you hear", "how you heard", "source of referral"),
        "referral_source",
    ),
    (
        ("willing to relocate", "open to relocation", "willing to move"),
        "willing_to_relocate",
    ),
    (
        ("years of experience", "years experience", "total experience"),
        "years_experience",
    ),
    (("phone", "mobile number", "cell phone", "telephone"), "phone"),
)

WORK_AUTH_PHRASES: dict[str, tuple[str, ...]] = {
    "yes_no_restrictions": (
        "Yes, with no restrictions",
        "Yes - I am authorized to work",
        "Yes, I am authorized",
        "Yes",
    ),
    "yes_with_restrictions": (
        "Yes, with restrictions",
        "Yes with restrictions",
    ),
    "no": ("No", "Not authorized"),
}

BINARY_PHRASES: dict[str, tuple[str, ...]] = {
    "yes": ("Yes",),
    "no": ("No",),
}


def normalize_label(text: str) -> str:
    text = re.sub(r"\*", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def fingerprint(label: str) -> str:
    return normalize_label(label)[:120]


def resolve_canonical_key(label: str) -> str | None:
    low = normalize_label(label)
    if not low:
        return None
    for hints, key in _LABEL_RULES:
        if any(h in low for h in hints):
            return key
    return None


def infer_country(location: str | None) -> str | None:
    if not location:
        return None
    low = location.lower()
    if "canada" in low or ", on" in low or ", bc" in low or ", ab" in low or ", qc" in low:
        return "Canada"
    if "united states" in low or ", us" in low or ", usa" in low:
        return "United States"
    if re.search(r",\s*[a-z]{2}$", low):
        return "United States"
    return None


def infer_city_province(location: str | None) -> tuple[str | None, str | None]:
    if not location:
        return None, None
    parts = [part.strip() for part in location.split(",") if part.strip()]
    if not parts:
        return None, None
    city = parts[0]
    province = parts[1] if len(parts) > 1 else None
    if province:
        province = province.split()[0].strip() or None
    return city, province


def default_application_defaults(*, location: str | None = None) -> dict[str, str]:
    defaults: dict[str, str] = {
        "work_authorization": "yes_no_restrictions",
        "requires_sponsorship": "no",
        "referral_source": "LinkedIn Job Posting",
    }
    if location:
        defaults["location"] = location
    city, province = infer_city_province(location)
    if city:
        defaults["city"] = city
    if province:
        defaults["province"] = province
    country = infer_country(location)
    if country:
        defaults["work_country"] = country
        defaults["country"] = country
    return defaults


def merge_application_defaults(
    stored: dict[str, Any] | None,
    *,
    location: str | None = None,
) -> dict[str, str]:
    merged = default_application_defaults(location=location)
    if not stored:
        return merged
    for key, value in stored.items():
        if key == "saved_answers" or value is None:
            continue
        text = str(value).strip()
        if text:
            merged[key] = text
    return merged


def saved_answers(stored: dict[str, Any] | None) -> dict[str, str]:
    if not stored:
        return {}
    raw = stored.get("saved_answers")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v is not None and str(v).strip()}


def resolve_value(
    key: str,
    *,
    defaults: dict[str, str],
    saved: dict[str, str],
    label: str,
    years_experience: float | None = None,
    job_url: str | None = None,
) -> str | None:
    fp = fingerprint(label)
    if fp in saved:
        return saved[fp]

    raw = defaults.get(key)
    if key == "years_experience" and years_experience is not None:
        if years_experience == int(years_experience):
            return str(int(years_experience))
        return str(years_experience)

    if key == "referral_source" and not raw and job_url and "linkedin.com" in job_url.lower():
        raw = "LinkedIn Job Posting"

    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip()


def answer_candidates(key: str, canonical_value: str) -> list[str]:
    if key == "work_authorization":
        return list(WORK_AUTH_PHRASES.get(canonical_value, (canonical_value,)))
    if key in ("requires_sponsorship", "willing_to_relocate"):
        return list(BINARY_PHRASES.get(canonical_value, (canonical_value,)))
    return [canonical_value]


def match_option(options: list[str], candidates: list[str]) -> str | None:
    norm_opts = [(orig, normalize_label(orig)) for orig in options if orig.strip()]
    for cand in candidates:
        target = normalize_label(cand)
        for orig, opt in norm_opts:
            if opt == target:
                return orig
    for cand in candidates:
        target = normalize_label(cand)
        for orig, opt in norm_opts:
            if target in opt or opt in target:
                return orig
    return None
