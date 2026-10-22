"""Render a structured CV and cover letter to PDF with headless Chrome, in the user's style.

Every style is one column of real text (no images or tables) with standard
section headings, so applicant tracking systems read it cleanly; styles differ
only in type, colour and spacing. Every value is HTML-escaped; nothing from a
job posting is rendered here.

A CV keeps to the user's own page count: when it runs over, the PDF is printed
again with tighter spacing, margins and then type (the `fit` level, 0 to 1)
until it fits, or as tight as stays readable.
"""

from __future__ import annotations

import io
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

from pypdf import PdfReader

from ..pipeline.tailor import CvEntry, StructuredCv


@dataclass(frozen=True)
class Style:
    id: str
    name: str
    description: str
    margin: str  # page margin, CSS shorthand
    css: str


_BASE_CSS = """
@page { size: Letter; }
* { box-sizing: border-box; }
html { background: #fff; }
body { margin: 0; color: var(--ink); font-family: var(--font);
       font-size: calc(var(--size) * var(--fit-font, 1));
       line-height: max(1.12, calc(var(--leading) * var(--fit-leading, 1)));
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1, h2, p, ul { margin: 0; }
a { color: inherit; text-decoration: none; }
h1 { font-size: calc(var(--name-size) * var(--fit-font, 1)); line-height: 1.1; color: var(--name); }
.headline { margin-top: 3px; font-size: 1.1em; color: var(--accent); }
.contact { margin-top: 5px; font-size: 0.92em; color: var(--muted); }
.contact span { white-space: nowrap; }
.contact span + span::before { content: var(--sep); padding: 0 0.5em; color: var(--faint); }
.sec { margin-top: calc(var(--gap) * var(--fit-space, 1)); }
h2 { font-size: calc(var(--h2-size) * var(--fit-font, 1)); color: var(--heading);
     margin-bottom: calc(6px * var(--fit-space, 1)); }
.entry { break-inside: avoid; }
.entry + .entry { margin-top: calc(var(--entry-gap, 8px) * var(--fit-space, 1)); }
.entry-head { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; }
.entry-title { font-weight: 600; }
.entry-sub { display: block; color: var(--muted); }
.entry-sub a { white-space: nowrap; }
.entry-dates { flex: none; white-space: nowrap; font-size: 0.92em; color: var(--muted); }
.bullets { margin-top: 3px; padding-left: 1.2em; }
.bullets li + li { margin-top: 2px; }
.bullets li::marker { color: var(--marker); }
.skills div + div { margin-top: 2px; }
.skill-label { font-weight: 600; }
.letter { margin-top: calc(28px * var(--fit-space, 1)); line-height: 1.45;
          font-size: calc(var(--letter-size) * var(--fit-font, 1)); }
.letter p + p { margin-top: calc(12px * var(--fit-space, 1)); }
.letter .re { font-weight: 600; }
"""

STYLES: dict[str, Style] = {
    s.id: s
    for s in (
        Style(
            id="modern",
            name="Modern",
            description="Clean sans-serif with a blue accent. Works for most tech roles.",
            margin="0.6in 0.65in",
            css="""
:root { --ink: #1f2933; --muted: #52606d; --faint: #9aa5b1; --accent: #1c6e8c; --name: #102a43;
  --heading: #1c6e8c; --marker: #1c6e8c; --font: "Segoe UI", Calibri, Arial, sans-serif;
  --size: 10pt; --leading: 1.38; --name-size: 25pt; --h2-size: 9.5pt; --gap: 14px; --sep: "·";
  --letter-size: 10.5pt; }
h1 { font-weight: 700; letter-spacing: -0.3px; }
.headline { font-weight: 600; }
h2 { display: flex; align-items: center; gap: 10px; text-transform: uppercase;
     letter-spacing: 1.6px; font-weight: 700; }
h2::after { content: ""; flex: 1; border-top: 1px solid #d9e2ec; }
.entry-sub { color: var(--accent); font-weight: 600; font-size: 0.95em; }
""",
        ),
        Style(
            id="classic",
            name="Classic",
            description="Serif type, centred header and small-caps headings. Safe for any field.",
            margin="0.6in 0.7in",
            css="""
:root { --ink: #1b1b1b; --muted: #4d4d4d; --faint: #8a8a8a; --accent: #3a3a3a; --name: #111;
  --heading: #111; --marker: #333; --font: Cambria, Georgia, "Times New Roman", serif;
  --size: 10.8pt; --leading: 1.3; --name-size: 24pt; --h2-size: 12pt; --gap: 13px; --sep: "|";
  --letter-size: 11pt; }
.head { text-align: center; }
h1 { font-variant: small-caps; letter-spacing: 1.5px; font-weight: 700; }
.headline { font-style: italic; }
h2 { font-variant: small-caps; letter-spacing: 1px; font-weight: 700; padding-bottom: 2px;
     border-bottom: 0.8px solid #777; }
.entry-sub { font-style: italic; }
""",
        ),
        Style(
            id="minimal",
            name="Minimal",
            description="Light type, lots of white space and headings in the margin.",
            margin="0.6in 0.65in",
            css="""
:root { --ink: #222; --muted: #666; --faint: #b0b0b0; --accent: #666; --name: #111;
  --heading: #8a8a8a; --marker: #a0a0a0; --font: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --size: 9.8pt; --leading: 1.42; --name-size: 28pt; --h2-size: 8pt; --gap: 0px; --sep: "/";
  --letter-size: 10.3pt; }
h1 { font-weight: 300; letter-spacing: -0.5px; }
.head { padding-bottom: calc(14px * var(--fit-space, 1)); border-bottom: 1px solid #e3e3e3; }
.sec { display: grid; grid-template-columns: 1.15in 1fr; column-gap: 0.25in;
       padding: calc(11px * var(--fit-space, 1)) 0; border-bottom: 1px solid #efefef; }
.sec:last-child { border-bottom: none; }
h2 { margin: 2px 0 0; text-transform: uppercase; letter-spacing: 2px; font-weight: 600; }
""",
        ),
        Style(
            id="compact",
            name="Compact",
            description="Tighter spacing that fits more on one page.",
            margin="0.45in 0.55in",
            css="""
:root { --ink: #111; --muted: #444; --faint: #999; --accent: #333; --name: #000; --heading: #000;
  --marker: #444; --font: Calibri, "Segoe UI", Arial, sans-serif; --size: 10.3pt;
  --leading: 1.22; --name-size: 19pt; --h2-size: 10.5pt; --gap: 8px; --sep: "•";
  --letter-size: 11pt; --entry-gap: 5px; }
h1 { font-weight: 700; }
.headline { margin-top: 1px; font-size: 1em; }
.contact { margin-top: 2px; }
h2 { margin-bottom: 4px; padding-bottom: 1px; border-bottom: 1.2px solid #111;
     text-transform: uppercase; letter-spacing: 0.6px; font-weight: 700; }
.entry-sub { display: inline; }
.entry-sub::before { content: " — "; }
.bullets { margin-top: 1px; }
.bullets li + li { margin-top: 0; }
""",
        ),
        Style(
            id="bold",
            name="Bold",
            description="Dark header band with honey accents. Stands out in a pile.",
            margin="0.55in 0",
            css="""
@page :first { margin-top: 0; }
body { padding-left: var(--pad); padding-right: var(--pad); }
@media screen { body { padding-top: 0; } }
:root { --ink: #1f2328; --muted: #57606a; --faint: #6b7280; --accent: #b45309; --name: #fff;
  --heading: #1f2937; --marker: #d97706; --font: "Segoe UI", Calibri, Arial, sans-serif;
  --size: 10pt; --leading: 1.36; --name-size: 26pt; --h2-size: 10.5pt; --gap: 14px; --sep: "·";
  --letter-size: 10.5pt; --pad: calc(0.65in * var(--fit-margin, 1)); }
.head { margin: 0 calc(-1 * var(--pad)); background: #1f2937;
        padding: calc(0.5in * var(--fit-margin, 1)) var(--pad) calc(0.3in * var(--fit-space, 1)); }
h1 { font-weight: 700; letter-spacing: -0.2px; }
.headline { color: #fbbf24; font-weight: 600; }
.contact { color: #d1d5db; }
.sec:first-of-type { margin-top: calc(18px * var(--fit-space, 1)); }
h2 { padding-left: 9px; border-left: 3px solid #d97706; line-height: 1.15;
     text-transform: uppercase; letter-spacing: 1.2px; font-weight: 700; }
.entry-sub { color: var(--accent); }
""",
        ),
    )
}

DEFAULT_STYLE = "modern"


def get_style(style_id: str | None) -> Style:
    return STYLES.get(style_id or "", STYLES[DEFAULT_STYLE])


def _shrink(length: str, factor: float) -> str:
    """Scale one CSS length ("0.6in"), keeping at least 0.35in of margin."""

    number = float(re.match(r"[\d.]+", length).group())
    if number == 0:
        return length
    return f"{max(0.35, number * factor):.3f}in"


def _fit_css(style: Style, fit: float) -> tuple[str, str]:
    """The page margin and tightening for a fit level (0 = as designed, 1 = tightest).

    Spacing shrinks most, then margins, then type, which never drops below 88%.
    Titles and organisations share a line once any tightening is needed; that
    rule goes before the style's own CSS so a style that already does it wins.
    """

    if fit <= 0:
        return f"@page {{ margin: {style.margin}; }}", ""
    margin = 1 - 0.4 * fit
    margins = " ".join(_shrink(v, margin) for v in style.margin.split())
    page = (
        f"@page {{ margin: {margins}; }}"
        f":root {{ --fit-space: {1 - 0.6 * fit:.3f}; --fit-margin: {margin:.3f};"
        f" --fit-leading: {1 - 0.12 * fit:.3f}; --fit-font: {1 - 0.12 * fit:.3f}; }}"
    )
    inline = ".entry-sub { display: inline; } .entry-sub::before { content: ', '; }"
    return page, inline


def _page(title: str, body: str, style: Style, fit: float = 0) -> str:
    page, inline = _fit_css(style, fit)
    css = (
        f"{_BASE_CSS}{inline}{page}"
        f"@media screen {{ body {{ padding: {style.margin}; }} }}{style.css}"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title><style>{css}</style></head>"
        f"<body>{body}</body></html>"
    )


def _bare(text: str) -> str:
    text = re.sub(r"^https?://", "", text.strip(), flags=re.I)
    return re.sub(r"^www\.", "", text, flags=re.I).rstrip("/")


def _link(text: str) -> str:
    """Contact details and project links, shortened and clickable in the PDF."""

    shown = _bare(text)
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", shown):
        return f"<a href='mailto:{escape(shown)}'>{escape(shown)}</a>"
    if " " not in shown and re.match(r"[\w.-]+\.[A-Za-z]{2,}(/|$)", shown):
        return f"<a href='https://{escape(shown)}'>{escape(shown)}</a>"
    return escape(text.strip())


def _head(cv: StructuredCv) -> str:
    parts = [f"<h1>{escape(cv.name)}</h1>"]
    if cv.headline:
        parts.append(f"<div class='headline'>{escape(cv.headline)}</div>")
    contact = [c for c in cv.contact if c.strip()]
    if contact:
        items = "".join(f"<span>{_link(c)}</span>" for c in contact)
        parts.append(f"<div class='contact'>{items}</div>")
    return f"<header class='head'>{''.join(parts)}</header>"


def _section(label: str, body: str) -> str:
    return f"<section class='sec'><h2>{label}</h2><div>{body}</div></section>"


def _dates(e: CvEntry) -> str:
    start, end = (e.start or "").strip(), (e.end or "").strip()
    if start and end and start.lower() == end.lower():
        return escape(start)
    return " – ".join(escape(p) for p in (start, end) if p)


def _entry(e: CvEntry) -> str:
    sub = ", ".join(_link(p) for p in (e.organization, e.location) if p and p.strip())
    bullets = "".join(f"<li>{escape(b)}</li>" for b in e.bullets if b.strip())
    return (
        "<div class='entry'><div class='entry-head'><div>"
        f"<span class='entry-title'>{escape(e.title)}</span>"
        + (f"<span class='entry-sub'>{sub}</span>" if sub else "")
        + f"</div><span class='entry-dates'>{_dates(e)}</span></div>"
        + (f"<ul class='bullets'>{bullets}</ul>" if bullets else "")
        + "</div>"
    )


def _skills(skills: list[str]) -> str:
    """Grouped lines ("Languages: Python, Go") when Claude grouped them, else one line."""

    skills = [s.strip() for s in skills if s.strip()]
    grouped = []
    for s in skills:
        label, sep, rest = s.partition(":")
        if sep and 0 < len(label.strip()) <= 40 and rest.strip():
            grouped.append((label.strip(), rest.strip()))
        else:
            grouped.append(("", s))
    if any(label for label, _ in grouped):
        rows = "".join(
            f"<div><span class='skill-label'>{escape(label)}:</span> {escape(rest)}</div>"
            if label
            else f"<div>{escape(rest)}</div>"
            for label, rest in grouped
        )
        return f"<div class='skills'>{rows}</div>"
    return f"<p class='skills'>{' · '.join(escape(s) for s in skills)}</p>"


def resume_html(cv: StructuredCv, style_id: str | None = None, fit: float = 0) -> str:
    style = get_style(style_id)
    parts = [_head(cv)]
    if cv.summary:
        parts.append(_section("Summary", f"<p>{escape(cv.summary)}</p>"))
    if cv.skills:
        parts.append(_section("Skills", _skills(cv.skills)))
    for label, entries in (
        ("Experience", cv.experience),
        ("Projects", cv.projects),
        ("Education", cv.education),
    ):
        if entries:
            parts.append(_section(label, "".join(_entry(e) for e in entries)))
    if cv.certifications:
        items = "".join(f"<li>{escape(c)}</li>" for c in cv.certifications if c.strip())
        parts.append(_section("Certifications", f"<ul class='bullets'>{items}</ul>"))
    return _page(f"{cv.name} — Resume", "".join(parts), style, fit)


_GREETING = re.compile(r"^(dear|hello|hi|to whom)\b", re.I)
_CLOSING = re.compile(
    r"^(sincerely|best|best regards|kind regards|warm regards|warmly|regards|thank you|thanks|"
    r"respectfully|with thanks|yours (sincerely|truly|faithfully))[\s,.!]*$",
    re.I,
)


def letter_parts(letter: str, name: str) -> tuple[str | None, list[str], str, str]:
    """Split Claude's letter into greeting, body paragraphs, closing and signature."""

    paras = [" ".join(p.split()) for p in letter.splitlines() if p.strip()]
    greeting = None
    if paras and _GREETING.match(paras[0]) and len(paras[0]) <= 90:
        greeting = paras.pop(0)
    closing, signature = "Sincerely,", name
    if paras and _CLOSING.match(paras[-1]):
        closing = paras.pop()
    elif len(paras) >= 2 and _CLOSING.match(paras[-2]) and len(paras[-1]) <= 60:
        signature = paras.pop()
        closing = paras.pop()
    elif paras:
        head, sep, tail = paras[-1].partition(",")
        first_name = name.split()[0].lower() if name.split() else ""
        if sep and _CLOSING.match(head) and 0 < len(tail.strip()) <= 60:
            paras.pop()
            closing, signature = head, tail.strip()
        elif paras[-1].lower() in (name.lower(), first_name):
            signature = paras.pop()
    closing = closing.rstrip(" .!,") + ","
    return greeting, paras, closing, signature


def _today() -> str:
    d = date.today()
    return f"{d:%B} {d.day}, {d.year}"


def cover_letter_html(
    cv: StructuredCv,
    letter: str,
    company: str | None,
    role: str | None = None,
    style_id: str | None = None,
    fit: float = 0,
) -> str:
    style = get_style(style_id)
    greeting, paras, closing, signature = letter_parts(letter, cv.name)
    body = [f"<p>{escape(_today())}</p>"]
    if company:
        body.append(f"<p>Hiring Team<br>{escape(company)}</p>")
    if role:
        body.append(f"<p class='re'>Re: {escape(role)}</p>")
    body.append(f"<p>{escape(greeting or 'Dear Hiring Team,')}</p>")
    body += [f"<p>{escape(p)}</p>" for p in paras]
    body.append(f"<p>{escape(closing)}<br>{escape(signature)}</p>")
    return _page(
        f"{cv.name} — Cover letter",
        f"{_head(cv)}<div class='letter'>{''.join(body)}</div>",
        style,
        fit,
    )


async def html_to_pdf(build: Callable[[float], str], out_path: Path, max_pages: int = 1) -> int:
    """Print `build(fit)` to PDF, tightened just enough to fit `max_pages`; returns the pages."""

    from playwright.async_api import async_playwright

    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = None
        for kwargs in ({"channel": "chrome"}, {"channel": "msedge"}, {}):
            try:
                browser = await pw.chromium.launch(headless=True, **kwargs)
                break
            except Exception:
                continue
        if browser is None:
            raise RuntimeError("No Chrome/Edge available to render PDFs.")
        try:
            page = await browser.new_page()

            async def render(fit: float) -> tuple[bytes, int]:
                await page.set_content(build(fit), wait_until="load")
                pdf = await page.pdf(format="Letter", print_background=True)
                return pdf, len(PdfReader(io.BytesIO(pdf)).pages)

            pdf, pages = await fit_to_pages(render, max_pages)
        finally:
            await browser.close()
    out_path.write_bytes(pdf)
    return pages


async def fit_to_pages(
    render: Callable[[float], Awaitable[tuple[bytes, int]]], max_pages: int
) -> tuple[bytes, int]:
    """The lightest fit level within the page budget: as designed, else a binary search.

    If even the tightest level runs over, the budget becomes the page count the
    tightest level reaches, so the CV is never longer than it has to be.
    """

    best = await render(0)
    if best[1] <= max_pages:
        return best
    tightest = await render(1)
    budget = max(max_pages, tightest[1])
    if best[1] <= budget:
        return best
    best, low, high = tightest, 0.0, 1.0
    for _ in range(4):
        fit = (low + high) / 2
        attempt = await render(fit)
        if attempt[1] <= budget:
            best, high = attempt, fit
        else:
            low = fit
    return best


def file_stem(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in name.strip()).strip("_")
    return "_".join(p for p in cleaned.split("_") if p) or "Candidate"


SAMPLE_CV = StructuredCv(
    name="Jordan Lee",
    headline="Backend Engineer · Python, APIs and data pipelines",
    contact=[
        "jordan.lee@example.com",
        "+1 416 555 0142",
        "Toronto, ON",
        "https://linkedin.com/in/jordanlee",
        "https://github.com/jordanlee",
    ],
    summary=(
        "Backend engineer with four years of experience building Python services and data "
        "pipelines. Comfortable owning a feature from design to on-call, and happiest on small "
        "teams that ship often."
    ),
    skills=[
        "Languages: Python, TypeScript, SQL, Go",
        "Backend: FastAPI, Django, PostgreSQL, Redis, Kafka",
        "Cloud & tooling: AWS, Docker, Terraform, GitHub Actions",
    ],
    experience=[
        CvEntry(
            title="Software Engineer II",
            organization="Northwind Logistics",
            location="Toronto, ON",
            start="Mar 2023",
            end="Present",
            bullets=[
                "Built the shipment-tracking API in FastAPI that now serves 2M requests a day.",
                "Cut nightly ETL time from 3 hours to 40 minutes with incremental loads.",
                "Mentored two new hires and led the team's move to typed Python.",
            ],
        ),
        CvEntry(
            title="Software Developer",
            organization="Brightleaf Health",
            location="Remote",
            start="Jun 2021",
            end="Feb 2023",
            bullets=[
                "Shipped patient-scheduling features used by 300 clinics.",
                "Added contract tests that caught 30+ breaking API changes before release.",
            ],
        ),
    ],
    projects=[
        CvEntry(
            title="Open-source rate limiter",
            organization="github.com/jordanlee/throttle",
            start="2024",
            end="2024",
            bullets=["Redis-backed limiter for ASGI apps with 400+ GitHub stars."],
        ),
    ],
    education=[
        CvEntry(
            title="BSc, Computer Science",
            organization="University of Waterloo",
            start="2017",
            end="2021",
        ),
    ],
)

SAMPLE_LETTER = """Dear Hiring Team,
I'm applying for the Backend Engineer role. Your team's work on real-time logistics data is \
close to what I've been building for the last two years, and I'd love to bring that experience \
to a product with your reach.
At Northwind Logistics I built and run the shipment-tracking API, which serves 2M requests a day, \
and I rebuilt our nightly ETL so it finishes in 40 minutes instead of three hours. Before that I \
shipped scheduling features for 300 clinics at Brightleaf Health.
I'd welcome the chance to talk about how I could help your team ship reliable services.
Sincerely,
Jordan Lee"""
