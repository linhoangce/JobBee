"""The applicant dossier: everything the apply agent may use to answer a form."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..models import Document, Job, User
from ..pipeline.prefs import load_preferences
from ..services.application_defaults import merge_application_defaults, saved_answers

_JD_MAX_CHARS = 6000


@dataclass
class UploadFile:
    kind: str  # "resume" | "cover_letter"
    path: Path


@dataclass
class Dossier:
    profile: dict
    application_defaults: dict[str, str]
    saved_answers: dict[str, str]
    resume_text: str
    cover_letter_text: str | None = None
    job: dict | None = None
    uploads: list[UploadFile] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)

    def render(self) -> str:
        sections = [
            "## Applicant profile\n" + json.dumps(self.profile, indent=2, ensure_ascii=False),
            "## Standard answers (canonical values; map them to the form's wording)\n"
            + json.dumps(self.application_defaults, indent=2, ensure_ascii=False),
        ]
        if self.saved_answers:
            sections.append(
                "## Saved answers from earlier applications (question → answer)\n"
                + json.dumps(self.saved_answers, indent=2, ensure_ascii=False)
            )
        if self.permissions:
            sections.append("## Permissions\n" + "\n".join(f"- {p}" for p in self.permissions))
        if self.uploads:
            sections.append(
                "## Files to upload\n" + "\n".join(f"- {u.kind}: {u.path}" for u in self.uploads)
            )
        sections.append("## Resume text\n" + self.resume_text.strip())
        if self.cover_letter_text:
            sections.append(
                "## Cover letter text (paste only if the form has a cover letter text box)\n"
                + self.cover_letter_text.strip()
            )
        if self.job:
            sections.append("## Job\n" + json.dumps(self.job, indent=2, ensure_ascii=False))
        return "\n\n".join(sections)


def build_dossier(
    user: User,
    *,
    resume: Document,
    cover_letter: Document | None = None,
    job: Job | None = None,
    uploads: list[UploadFile] | None = None,
    extra_saved_answers: dict[str, str] | None = None,
    site_account: str | None = None,
    inbox: bool | None = None,
) -> Dossier:
    """Everything the agent may use. `site_account`: the status of JobBee's account on the
    job's site, or "none". `inbox`: whether an inbox is connected for the site's emails.
    None means unknown for either."""

    profile = {
        "name": user.name,
        "email": user.email,
        "headline": user.headline,
        "location": user.location,
        "years_experience": user.years_experience,
        "links": user.links or {},
        "skills": user.skills or [],
        "target_roles": user.target_roles or [],
        "work_modes": user.work_modes or [],
        "summary": user.summary,
    }
    if user.min_salary:
        profile["minimum_salary"] = user.min_salary
    answers = saved_answers(user.application_defaults)
    answers.update(extra_saved_answers or {})
    job_info = None
    if job is not None:
        job_info = {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "url": job.url,
            "description": (job.description or "")[:_JD_MAX_CHARS],
        }
    return Dossier(
        profile={k: v for k, v in profile.items() if v not in (None, "", [], {})},
        application_defaults=merge_application_defaults(
            user.application_defaults, location=user.location
        ),
        saved_answers=answers,
        resume_text=resume.content or "",
        cover_letter_text=cover_letter.content if cover_letter else None,
        job=job_info,
        uploads=list(uploads or []),
        permissions=_permissions(user, site_account, inbox),
    )


_ACCOUNT_LINES = {
    "none": "JobBee has no account on this job's site yet: create one if it asks.",
    "rejected": (
        "JobBee's saved password for this job's site was refused last time (the applicant may "
        "have made the account themselves). Don't try it: use the site's 'Forgot password' "
        "link to set a new one."
    ),
}


def _permissions(user: User, site_account: str | None, inbox: bool | None) -> list[str]:
    prefs = load_preferences(user)
    today = date.today()
    lines = [
        "Create job-site accounts and sign in with `site_account`: "
        + ("allowed" if prefs.agent_accounts else "NOT allowed"),
        "Accept terms and privacy statements, and sign attestations with the applicant's name: "
        + ("allowed" if prefs.agent_consents else "NOT allowed"),
        f"Today's date, for signatures: {today:%B} {today.day}, {today.year}",
    ]
    if site_account is not None and prefs.agent_accounts:
        lines.append(
            _ACCOUNT_LINES.get(
                site_account, "JobBee already has an account on this job's site: sign in."
            )
        )
    if inbox is not None and prefs.agent_accounts:
        lines.append(
            "Inbox for the site's emails (verification, password reset): connected"
            if inbox
            else "Inbox for the site's emails: NOT connected, so an emailed link or code can't "
            "be read"
        )
    return lines
