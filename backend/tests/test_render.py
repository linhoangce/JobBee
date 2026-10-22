"""CV and cover letter styles: layout helpers, escaping, the letter's parts and page fitting."""

from __future__ import annotations

import zipfile

import pytest
from pypdf import PdfWriter

from app.documents.render_pdf import (
    DEFAULT_STYLE,
    SAMPLE_CV,
    SAMPLE_LETTER,
    STYLES,
    cover_letter_html,
    fit_to_pages,
    get_style,
    letter_parts,
    resume_html,
)
from app.models import Document, DocumentKind
from app.pipeline.tailor import CvEntry, StructuredCv
from app.services.documents import page_budget


def test_unknown_style_falls_back_to_the_default() -> None:
    assert get_style("nope").id == DEFAULT_STYLE
    assert get_style(None).id == DEFAULT_STYLE
    assert get_style("classic").id == "classic"


@pytest.mark.parametrize("style_id", list(STYLES))
def test_every_style_renders_both_documents(style_id: str) -> None:
    resume = resume_html(SAMPLE_CV, style_id)
    letter = cover_letter_html(SAMPLE_CV, SAMPLE_LETTER, "Acme", "Backend Engineer", style_id)
    for markup in (resume, letter):
        assert markup.startswith("<!doctype html>") and "Jordan Lee" in markup
        assert STYLES[style_id].css.strip()[:40] in markup
    assert "<h2>Experience</h2>" in resume and "Re: Backend Engineer" in letter


def test_values_are_escaped() -> None:
    cv = StructuredCv(
        name="<script>alert(1)</script>",
        contact=["javascript:alert(1)", "a@b.co"],
        skills=["<b>Python</b>"],
        experience=[CvEntry(title="Eng <i>", organization="x' onmouseover='y", bullets=["<img>"])],
    )
    markup = resume_html(cv) + cover_letter_html(cv, "<p>Hi</p>", "<Co>", "<Role>")
    assert "<script>" not in markup and "<img>" not in markup and "<b>Python" not in markup
    assert "&lt;script&gt;" in markup and "&lt;Co&gt;" in markup
    assert "href='https://javascript" not in markup and "x&#x27; onmouseover" in markup


def test_contact_links_are_short_and_clickable() -> None:
    cv = StructuredCv(
        name="A",
        contact=["https://www.linkedin.com/in/someone/", "me@example.com", "+1 416 555 0100"],
    )
    markup = resume_html(cv)
    assert "<a href='https://linkedin.com/in/someone'>linkedin.com/in/someone</a>" in markup
    assert "<a href='mailto:me@example.com'>me@example.com</a>" in markup
    assert "<span>+1 416 555 0100</span>" in markup


def test_grouped_skills_become_lines_and_plain_ones_one_line() -> None:
    grouped = resume_html(StructuredCv(name="A", skills=["Languages: Python, Go", "Docker"]))
    assert "<span class='skill-label'>Languages:</span> Python, Go" in grouped
    assert "<div>Docker</div>" in grouped
    flat = resume_html(StructuredCv(name="A", skills=["Python", "Go"]))
    assert "<p class='skills'>Python · Go</p>" in flat


def test_same_start_and_end_date_shows_once() -> None:
    cv = StructuredCv(
        name="A",
        projects=[CvEntry(title="Hack", start="May 2026", end="May 2026")],
        experience=[CvEntry(title="Eng", start="2021", end="Present")],
    )
    markup = resume_html(cv)
    assert "May 2026 – May 2026" not in markup and ">May 2026<" in markup
    assert "2021 – Present" in markup


@pytest.mark.parametrize(
    ("letter", "greeting", "body", "closing", "signature"),
    [
        (
            "Dear Acme team,\nFirst.\nSecond.\nSincerely,\nLin Ho",
            "Dear Acme team,",
            ["First.", "Second."],
            "Sincerely,",
            "Lin Ho",
        ),
        ("First.\nBest regards, Lin", None, ["First."], "Best regards,", "Lin"),
        ("Hello there,\nFirst.\nThanks!", "Hello there,", ["First."], "Thanks,", "Lin Ho"),
        (
            "Dear Beta team,\nI'd love to help.\nLin",
            "Dear Beta team,",
            ["I'd love to help."],
            "Sincerely,",
            "Lin",
        ),
        ("Only a body.", None, ["Only a body."], "Sincerely,", "Lin Ho"),
    ],
)
def test_letter_parts(letter, greeting, body, closing, signature) -> None:
    assert letter_parts(letter, "Lin Ho") == (greeting, body, closing, signature)


def test_letter_has_one_greeting_and_one_sign_off() -> None:
    markup = cover_letter_html(SAMPLE_CV, SAMPLE_LETTER, "Acme", None)
    assert markup.count("Dear") == 1 and markup.count("Sincerely") == 1
    assert "Hiring Team<br>Acme" in markup and "Re:" not in markup


def test_fit_tightens_spacing_margins_and_type() -> None:
    designed = resume_html(SAMPLE_CV, "modern")
    assert ":root { --fit-space" not in designed and "@page { margin: 0.6in 0.65in; }" in designed
    tight = resume_html(SAMPLE_CV, "modern", 1)
    assert "@page { margin: 0.360in 0.390in; }" in tight and "--fit-font: 0.880" in tight
    assert ".entry-sub { display: inline; }" in tight
    # Bold's page has no side margin; the top and bottom keep at least 0.35in.
    assert "@page { margin: 0.350in 0; }" in resume_html(SAMPLE_CV, "bold", 1)
    letter = cover_letter_html(SAMPLE_CV, SAMPLE_LETTER, "Acme", None, None, 1)
    assert "--fit-space: 0.400" in letter


def _pages_at(threshold: float, over: int = 2, tried: list[float] | None = None):
    """A fake printer: `over` pages below the fit threshold, one page from it on."""

    async def render(fit: float) -> tuple[bytes, int]:
        if tried is not None:
            tried.append(fit)
        return str(fit).encode(), over if fit < threshold else 1

    return render


@pytest.mark.asyncio
async def test_fit_to_pages_uses_the_lightest_fit_that_fits() -> None:
    tried: list[float] = []
    assert await fit_to_pages(_pages_at(0, tried=tried), 1) == (b"0", 1)
    assert tried == [0]
    pdf, pages = await fit_to_pages(_pages_at(0.6), 1)
    assert pages == 1 and 0.6 <= float(pdf) < 0.7
    assert await fit_to_pages(_pages_at(0.6), 2) == (b"0", 2)


@pytest.mark.asyncio
async def test_fit_to_pages_keeps_the_design_when_it_cannot_save_a_page() -> None:
    async def always_two(fit: float) -> tuple[bytes, int]:
        return str(fit).encode(), 2

    assert await fit_to_pages(always_two, 1) == (b"0", 2)

    async def three_then_two(fit: float) -> tuple[bytes, int]:
        return str(fit).encode(), 3 if fit < 0.3 else 2

    pdf, pages = await fit_to_pages(three_then_two, 1)
    assert pages == 2 and 0.3 <= float(pdf) < 0.4


def test_page_budget_follows_the_uploaded_resume(tmp_path) -> None:
    def doc(path=None, words=100) -> Document:
        return Document(
            kind=DocumentKind.resume, content="word " * words, file_path=str(path) if path else None
        )

    pdf = tmp_path / "cv.pdf"
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(612, 792)
    writer.write(pdf)
    assert page_budget(doc(pdf)) == 2

    docx = tmp_path / "cv.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("docProps/app.xml", "<Properties><Pages>1</Pages></Properties>")
    assert page_budget(doc(docx, words=2000)) == 1

    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    assert page_budget(doc(broken, words=900)) == 2
    assert page_budget(doc(words=300)) == 1 and page_budget(None) == 1
