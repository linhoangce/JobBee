"""Turn a job URL into structured posting details.

Greenhouse, Lever and Ashby postings come from their public JSON APIs. Anything
else is fetched over HTTP and read from schema.org `JobPosting` JSON-LD when the
page has it (most career sites, Workday and LinkedIn guest pages included),
falling back to the page title and visible text. Content from job pages is
untrusted: it is only ever passed to tool-less, schema-checked model calls.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx

from ..apply.ats import detect_ats

MAX_DESCRIPTION_CHARS = 12_000
_WHITESPACE_RE = re.compile(r"\s+")
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobBee/1.0"

_LOGIN_URL_HINTS = ("login", "authwall", "checkpoint", "signin", "sign-in", "uas/")
_LOGIN_TEXT_HINTS = (
    "sign in to linkedin",
    "join linkedin",
    "log in to continue",
    "sign in to view",
    "create an account",
    "verify your identity",
    "security verification",
)


class JobFetchError(RuntimeError):
    pass


class PostingClosedError(JobFetchError):
    """The board says the posting is gone (removed, filled or no longer accepting applications)."""

    def __init__(self, message: str = "That posting is closed or was taken down.") -> None:
        super().__init__(message)


_CLOSED_HINTS = (
    "no longer available",
    "no longer accepting applications",
    "no longer open",
    "position has been filled",
    "job has expired",
    "posting has expired",
    "this job is closed",
    "job you are looking for",
    "page you are looking for",
    "page not found",
)


@dataclass
class JobDetails:
    url: str
    canonical_url: str
    ats: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None
    work_mode: str | None = None  # remote | hybrid | onsite
    salary_text: str | None = None
    posted_at: datetime | None = None
    login_wall: bool = False

    @property
    def content_hash(self) -> str | None:
        if not self.description:
            return None
        basis = (
            f"{(self.company or '').lower()}|{(self.title or '').lower()}|{self.description[:2000]}"
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def normalize_text(raw: str) -> str:
    return _WHITESPACE_RE.sub(" ", raw).strip()


def looks_like_login_wall(page_url: str, body_text: str) -> bool:
    url_low = page_url.lower()
    if any(hint in url_low for hint in _LOGIN_URL_HINTS):
        return True
    text_low = body_text.lower()[:2_000]
    return sum(1 for hint in _LOGIN_TEXT_HINTS if hint in text_low) >= 2


def canonicalize_url(url: str) -> str:
    """Strip tracking params and fragments so the same posting dedupes."""

    parts = urlsplit(url.strip())
    keep = []
    for key, values in parse_qs(parts.query, keep_blank_values=False).items():
        low = key.lower()
        if low.startswith("utm_") or low in {"source", "src", "ref", "refid", "trk", "trackingid"}:
            continue
        if low in {"gh_jid", "token", "for", "currentjobid", "jobid", "id", "lever-source"}:
            keep.extend(f"{key}={v}" for v in values if key.lower() != "lever-source")
    query = "&".join(sorted(keep))
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer"}
    _BREAK = {"p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr", "section", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self.json_ld: list[str] = []
        self._in_ld = False
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self._in_ld = True
            self.json_ld.append("")
            return
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self._in_ld = False
            return
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_ld:
            self.json_ld[-1] += data
        elif self._in_title:
            self.title = (self.title or "") + data
        elif not self._skip_depth:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    lines = [normalize_text(line) for line in "".join(parser.parts).split("\n")]
    return "\n".join(line for line in lines if line)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clip(text: str | None) -> str | None:
    if not text:
        return None
    text = text.strip()
    return text[:MAX_DESCRIPTION_CHARS]


def _mode_from_text(*values: str | None) -> str | None:
    blob = " ".join(v for v in values if v).lower()
    if "hybrid" in blob:
        return "hybrid"
    if "remote" in blob or "telecommute" in blob:
        return "remote"
    if "on-site" in blob or "onsite" in blob or "in office" in blob:
        return "onsite"
    return None


async def _get_json(client: httpx.AsyncClient, url: str) -> dict | list:
    resp = await client.get(url)
    if resp.status_code in (404, 410):
        raise PostingClosedError()
    if resp.status_code >= 400:
        raise JobFetchError(f"{urlsplit(url).hostname} returned {resp.status_code}")
    return resp.json()


async def _greenhouse(client, details: JobDetails, board: str, job_id: str) -> JobDetails:
    data = await _get_json(
        client, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
    )
    details.title = data.get("title")
    details.company = data.get("company_name") or board.replace("-", " ").title()
    details.location = (data.get("location") or {}).get("name")
    details.description = _clip(html_to_text(html.unescape(data.get("content") or "")))
    details.posted_at = _parse_date(data.get("first_published") or data.get("updated_at"))
    details.work_mode = _mode_from_text(details.location, details.title)
    return details


async def _lever(client, details: JobDetails, company: str, posting_id: str) -> JobDetails:
    data = await _get_json(client, f"https://api.lever.co/v0/postings/{company}/{posting_id}")
    categories = data.get("categories") or {}
    details.title = data.get("text")
    details.company = company.replace("-", " ").title()
    details.location = categories.get("location") or ", ".join(categories.get("allLocations") or [])
    sections = [data.get("descriptionPlain") or ""]
    for block in data.get("lists") or []:
        sections.append(f"{block.get('text', '')}\n{html_to_text(block.get('content') or '')}")
    sections.append(data.get("additionalPlain") or "")
    details.description = _clip("\n\n".join(s for s in sections if s))
    workplace = data.get("workplaceType")
    details.work_mode = {
        "remote": "remote",
        "hybrid": "hybrid",
        "on-site": "onsite",
        "onsite": "onsite",
    }.get((workplace or "").lower()) or _mode_from_text(details.location)
    salary = data.get("salaryRange") or {}
    if salary.get("min") and salary.get("max"):
        currency, interval = salary.get("currency", ""), salary.get("interval", "")
        details.salary_text = f"{currency} {salary['min']:,}–{salary['max']:,} {interval}".strip()
    if data.get("createdAt"):
        details.posted_at = datetime.fromtimestamp(data["createdAt"] / 1000).astimezone()
    return details


async def _ashby(client, details: JobDetails, org: str, job_id: str) -> JobDetails:
    data = await _get_json(
        client, f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"
    )
    job = next(
        (
            j
            for j in data.get("jobs", [])
            if job_id in (j.get("id"), j.get("jobUrl", "").rsplit("/", 1)[-1])
        ),
        None,
    )
    if job is None:
        raise PostingClosedError("That posting isn't on the company's Ashby job board any more.")
    details.title = (job.get("title") or "").strip() or None
    details.company = org.replace("-", " ").title()
    details.location = job.get("location")
    details.description = _clip(
        job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or "")
    )
    workplace = (job.get("workplaceType") or "").lower()
    details.work_mode = (
        "remote"
        if job.get("isRemote") or workplace == "remote"
        else "hybrid"
        if workplace == "hybrid"
        else "onsite"
        if workplace in ("onsite", "on-site")
        else None
    )
    compensation = job.get("compensation") or {}
    details.salary_text = compensation.get("compensationTierSummary") or None
    details.posted_at = _parse_date(job.get("publishedAt"))
    return details


def workday_api_url(url: str) -> str | None:
    """The public JSON endpoint behind a myworkdayjobs.com posting page."""

    parts = urlsplit(url)
    host = parts.hostname or ""
    segments = [s for s in parts.path.split("/") if s]
    if "job" not in segments:
        return None
    i = segments.index("job")
    site = [s for s in segments[:i] if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", s)]
    if not site or i + 1 >= len(segments):
        return None
    tenant = host.split(".")[0]
    return f"https://{host}/wday/cxs/{tenant}/{site[-1]}/" + "/".join(segments[i:])


async def _workday(client, details: JobDetails) -> JobDetails:
    api = workday_api_url(details.url)
    if api is None:
        return await _generic(client, details)
    resp = await client.get(api, headers={"Accept": "application/json"})
    # Workday answers 404 for removed postings and 403 for unlisted ones.
    if resp.status_code in (403, 404, 410):
        raise PostingClosedError()
    if resp.status_code >= 400:
        raise JobFetchError(f"Workday returned {resp.status_code}")
    data = resp.json()
    info = data.get("jobPostingInfo") or {}
    if not info or not info.get("canApply", True) or info.get("posted") is False:
        raise PostingClosedError("That Workday posting is no longer taking applications.")
    org = data.get("hiringOrganization") or {}
    details.title = info.get("title")
    tenant = (urlsplit(details.url).hostname or "").split(".")[0]
    details.company = org.get("name") or tenant.title()
    details.location = info.get("location")
    details.description = _clip(html_to_text(info.get("jobDescription") or ""))
    details.posted_at = _parse_date(info.get("startDate"))
    details.work_mode = _mode_from_text(info.get("remoteType"), details.location, details.title)
    return details


def _from_json_ld(details: JobDetails, blocks: list[str]) -> bool:
    for raw in blocks:
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else data.get("@graph", [data])
        for item in candidates:
            if not isinstance(item, dict) or "JobPosting" not in str(item.get("@type")):
                continue
            details.title = details.title or item.get("title")
            org = item.get("hiringOrganization") or {}
            details.company = details.company or (
                org.get("name") if isinstance(org, dict) else None
            )
            locations = item.get("jobLocation") or []
            if isinstance(locations, dict):
                locations = [locations]
            names = []
            for loc in locations:
                address = (loc or {}).get("address") or {}
                if isinstance(address, dict):
                    names.append(
                        ", ".join(
                            p
                            for p in (
                                address.get("addressLocality"),
                                address.get("addressRegion"),
                                address.get("addressCountry")
                                if isinstance(address.get("addressCountry"), str)
                                else None,
                            )
                            if p
                        )
                    )
            details.location = details.location or "; ".join(n for n in names if n) or None
            details.description = _clip(html_to_text(html.unescape(item.get("description") or "")))
            if str(item.get("jobLocationType", "")).upper() == "TELECOMMUTE":
                details.work_mode = "remote"
            details.posted_at = _parse_date(item.get("datePosted"))
            salary = item.get("baseSalary") or {}
            value = salary.get("value") if isinstance(salary, dict) else None
            if isinstance(value, dict) and (value.get("minValue") or value.get("value")):
                low = value.get("minValue") or value.get("value")
                high = value.get("maxValue")
                unit = value.get("unitText", "")
                span = f"{low}–{high}" if high else f"{low}"
                details.salary_text = f"{salary.get('currency', '')} {span} {unit}".strip()
            return True
    return False


async def _generic(client: httpx.AsyncClient, details: JobDetails) -> JobDetails:
    try:
        resp = await client.get(details.url)
    except httpx.HTTPError as e:
        raise JobFetchError(f"Couldn't open the page ({e.__class__.__name__}).") from None
    if resp.status_code in (404, 410):
        raise PostingClosedError()
    if resp.status_code >= 400:
        raise JobFetchError(f"The page returned HTTP {resp.status_code}.")
    markup = resp.text[:2_000_000]
    parser = _TextExtractor()
    parser.feed(markup)
    if not _from_json_ld(details, parser.json_ld):
        text = html_to_text(markup)
        details.title = details.title or normalize_text(html.unescape(parser.title or "")) or None
        details.description = _clip(text)
    details.login_wall = looks_like_login_wall(str(resp.url), details.description or "")
    opening = (details.description or "")[:1500].lower()
    if not details.login_wall and any(hint in opening for hint in _CLOSED_HINTS):
        raise PostingClosedError()
    details.work_mode = details.work_mode or _mode_from_text(details.location, details.title)
    return details


async def fetch_job_details(url: str, *, client: httpx.AsyncClient | None = None) -> JobDetails:
    details = JobDetails(url=url, canonical_url=canonicalize_url(url), ats=detect_ats(url))
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    query = parse_qs(parts.query)
    own_client = client is None
    client = client or httpx.AsyncClient(
        timeout=15.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
    )
    try:
        if details.ats == "greenhouse":
            if "jobs" in segments and segments.index("jobs") >= 1:
                i = segments.index("jobs")
                if i + 1 < len(segments):
                    return await _greenhouse(client, details, segments[i - 1], segments[i + 1])
            if query.get("for") and query.get("token"):
                return await _greenhouse(client, details, query["for"][0], query["token"][0])
        if details.ats == "lever" and len(segments) >= 2:
            return await _lever(client, details, segments[0], segments[1])
        if details.ats == "ashby" and len(segments) >= 2:
            return await _ashby(client, details, segments[0], segments[1])
        if details.ats == "workday":
            return await _workday(client, details)
        return await _generic(client, details)
    except (httpx.HTTPError, ValueError) as e:
        raise JobFetchError(f"Couldn't read the posting ({e.__class__.__name__}).") from None
    finally:
        if own_client:
            await client.aclose()
