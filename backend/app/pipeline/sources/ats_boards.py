"""Company watchlist: read the public Greenhouse, Lever and Ashby job boards.

A company is added by careers URL (the board is read off the URL) or by name
(we try the likely board slugs on all three). Board listings only carry title,
location and link; discovery fetches the full posting for the ones it keeps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from .base import Candidate, SourceError

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobBee/1.0"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}$", re.IGNORECASE)

_BOARD_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "boards-api.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "api.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
}


@dataclass
class Board:
    ats: str  # greenhouse | lever | ashby
    slug: str
    name: str


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=15.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
    )


def board_from_url(url: str) -> tuple[str, str] | None:
    """(ats, slug) for a Greenhouse/Lever/Ashby board or posting URL."""

    parts = urlsplit(url.strip() if "://" in url else f"https://{url.strip()}")
    ats = _BOARD_HOSTS.get((parts.hostname or "").lower())
    segments = [s for s in parts.path.split("/") if s]
    if ats is None or not segments:
        return None
    slug = segments[0]
    if ats == "greenhouse" and slug in ("v1", "embed"):
        if slug == "v1" and len(segments) >= 3 and segments[1] == "boards":
            slug = segments[2]
        else:
            return None
    if ats == "lever" and slug == "v0" and len(segments) >= 3:
        slug = segments[2]
    return (ats, slug) if _SLUG_RE.match(slug) else None


def slug_guesses(name: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", name.lower())
    words = [w for w in words if w not in {"inc", "ltd", "llc", "corp", "co", "the"}] or words
    guesses = ["".join(words), "-".join(words)]
    return [g for i, g in enumerate(guesses) if g and g not in guesses[:i]]


async def _board_name(client: httpx.AsyncClient, ats: str, slug: str) -> str | None:
    """The board's company name if the board exists and has postings, else None."""

    if ats == "greenhouse":
        resp = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
        if resp.status_code == 200:
            return resp.json().get("name") or slug
        return None
    if ats == "lever":
        resp = await client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1")
        if resp.status_code == 200 and isinstance(resp.json(), list):
            return slug.replace("-", " ").title()
        return None
    resp = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if resp.status_code == 200 and isinstance(resp.json().get("jobs"), list):
        return slug.replace("-", " ").title()
    return None


async def find_board(name_or_url: str, *, client: httpx.AsyncClient | None = None) -> Board | None:
    """Resolve a company name or careers URL to a public job board."""

    own = client is None
    client = client or _client()
    try:
        text = name_or_url.strip()
        direct = board_from_url(text) if ("." in text or "/" in text) else None
        if direct is not None:
            ats, slug = direct
            name = await _board_name(client, ats, slug)
            return Board(ats, slug, name) if name else None
        for slug in slug_guesses(text):
            for ats in ("greenhouse", "lever", "ashby"):
                try:
                    found = await _board_name(client, ats, slug)
                except (httpx.HTTPError, ValueError):
                    continue
                if found:
                    return Board(ats, slug, text if ats != "greenhouse" else found)
        return None
    except (httpx.HTTPError, ValueError) as e:
        raise SourceError(f"Couldn't reach the job board ({e.__class__.__name__}).") from None
    finally:
        if own:
            await client.aclose()


def _date(value) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000).astimezone()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


async def list_board(
    ats: str, slug: str, company: str, *, client: httpx.AsyncClient | None = None
) -> list[Candidate]:
    """Every open posting on one board."""

    own = client is None
    client = client or _client()
    try:
        if ats == "greenhouse":
            resp = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
            resp.raise_for_status()
            return [
                Candidate(
                    url=job["absolute_url"],
                    source="watchlist",
                    title=job.get("title"),
                    company=company,
                    location=(job.get("location") or {}).get("name"),
                    posted_at=_date(job.get("first_published") or job.get("updated_at")),
                )
                for job in resp.json().get("jobs", [])
                if job.get("absolute_url")
            ]
        if ats == "lever":
            resp = await client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            resp.raise_for_status()
            return [
                Candidate(
                    url=job["hostedUrl"],
                    source="watchlist",
                    title=job.get("text"),
                    company=company,
                    location=(job.get("categories") or {}).get("location"),
                    posted_at=_date(job.get("createdAt")),
                )
                for job in resp.json()
                if job.get("hostedUrl")
            ]
        if ats == "ashby":
            resp = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            resp.raise_for_status()
            return [
                Candidate(
                    url=job["jobUrl"],
                    source="watchlist",
                    title=job.get("title"),
                    company=company,
                    location=job.get("location"),
                    posted_at=_date(job.get("publishedAt")),
                )
                for job in resp.json().get("jobs", [])
                if job.get("jobUrl") and job.get("isListed", True)
            ]
        raise SourceError(f"Unsupported job board {ats!r}.")
    except (httpx.HTTPError, ValueError, KeyError) as e:
        raise SourceError(
            f"Couldn't read {company}'s job board ({e.__class__.__name__})."
        ) from None
    finally:
        if own:
            await client.aclose()


_GENERIC_WORDS = {
    "senior", "junior", "staff", "principal", "lead", "sr", "jr", "ii", "iii", "iv", "i",
    "the", "and", "of", "for", "with", "a", "an", "to", "in", "remote", "hybrid",
}  # fmt: skip


# Role nouns say little on their own ("Sales Engineer" vs "Backend Engineer").
_WEAK_WORDS = {"engineer", "developer", "manager", "analyst", "specialist", "associate"}


def role_relevance(title: str | None, roles: list[str]) -> int:
    """Cheap relevance score of a posting title against the target roles (0 = unrelated).

    Used to pick which board postings are worth fetching and screening.
    """

    if not roles:
        return 1
    words = set(re.findall(r"[a-z0-9+#]+", (title or "").lower())) - _GENERIC_WORDS
    best = 0
    for role in roles:
        wanted = set(re.findall(r"[a-z0-9+#]+", role.lower())) - _GENERIC_WORDS
        common = words & wanted
        strong = len(common - _WEAK_WORDS)
        weak = len(common & _WEAK_WORDS)
        if strong or common == wanted:
            best = max(best, strong * 2 + weak)
        elif weak:
            best = max(best, 1)
    return best
