"""Exemption rules for the Phase A review detector.

The pre-review pass flags every translated segment that looks like it
has untranslated English mixed in. In practice a large share of those
flags come from segments where English is *legitimately* present:

* Apparatus chapters (notes on transcription, bibliography, ...) where
  the original-language text is kept as reading context.
* Inline footnote bodies (``> ...`` blockquotes, indented notes).
* Proper-noun / book-title references that match the active glossary.
* Latin / Arabic / Greek transliterations that should not be touched.
* Code blocks and inline code.

Each rule below returns a tuple ``(is_exempt, reason)``. The detector
in :mod:`pdf_translator.review` calls :func:`apply_review_exemptions`
before it assigns an ``issue_type``; segments that match a rule keep
the issue_type they would have been assigned *only* if the rule
explicitly says so (most rules just say "this isn't a defect").
"""

from __future__ import annotations

import re
from typing import Any

#: Latin-script-only regex used for the foreign-script exemption.
_LATINISH_RE = re.compile(r"\b([A-Za-z]{4,}\s+){2,}[A-Za-z]{4,}\b")

#: Arabic-script and Hebrew-script Unicode ranges.
_NON_LATIN_SCRIPT_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\u0590-\u05FF\uFB50-\uFDFF\uFE70-\uFEFF\u02BB-\u02FF]")

#: Inline code and code block fences.
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")

#: Blockquote lines (typical note-style markers).
_BLOCKQUOTE_RE = re.compile(r"(?m)^\s*>\s+")

#: Numeric ranges that look like citation years / pagination.
_YEAR_LIKE_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")


def _is_apparatus_segment(segment: dict[str, Any]) -> bool:
    chapter_kind = str(segment.get("chapter_kind") or "").lower()
    if chapter_kind in {"apparatus", "bibliography", "index", "toc", "cover"}:
        return True
    chapter_title = str(segment.get("chapter_title") or "").lower()
    if any(
        needle in chapter_title
        for needle in (
            "notes on transcription",
            "notes on dates",
            "abbreviations",
            "glossary",
            "editorial note",
            "translator's note",
        )
    ):
        return True
    return False


def _is_quote_segment(segment: dict[str, Any]) -> bool:
    source_text = str(segment.get("source_text") or "")
    translated_text = str(segment.get("translated_text") or "")
    for text in (source_text, translated_text):
        if not text:
            continue
        if _BLOCKQUOTE_RE.search(text):
            return True
        if _FENCED_CODE_RE.search(text):
            return True
        if _INLINE_CODE_RE.search(text):
            return True
    return False


def _is_foreign_script_segment(segment: dict[str, Any]) -> bool:
    for field in ("translated_text", "source_text"):
        text = str(segment.get(field) or "")
        if text and _NON_LATIN_SCRIPT_RE.search(text):
            return True
    return False


def _is_glossary_term_segment(segment: dict[str, Any]) -> bool:
    """Return True if the translated text consists mostly of a
    glossary-listed term (proper noun / book title that should remain
    in the original language)."""
    glossary_active = segment.get("glossary_active") or []
    if not glossary_active:
        return False
    text = str(segment.get("translated_text") or "").strip()
    if not text:
        return False
    low = text.lower()
    matches = 0
    for entry in glossary_active:
        if not isinstance(entry, dict):
            continue
        term = str(entry.get("source") or "").strip().lower()
        if term and term in low:
            matches += 1
    # one term is not enough; require at least two or the segment to
    # be short (a proper-noun / title by itself).
    if matches >= 2:
        return True
    if matches >= 1 and len(text) <= 60:
        return True
    return False


def _is_citation_year_segment(segment: dict[str, Any]) -> bool:
    """Pure year-range segments (e.g. bibliography years) are not
    translation defects."""
    text = str(segment.get("translated_text") or "").strip()
    if not text or len(text) > 80:
        return False
    return bool(_YEAR_LIKE_RE.fullmatch(text))



def _is_pure_citation_segment(segment: dict[str, Any]) -> bool:
    """Short bibliography/footnote citation lines (e.g. ``Aḥmed, A. Q.
    *Title*. Place: Publisher, 2006.``) are valid even when the
    translation keeps them in the source language. They are not
    translation defects."""
    text = str(segment.get("translated_text") or "").strip()
    if not text or len(text) > 600:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > 6:
        return False
    # every line must look like a citation: contains a year OR ends with period
    # and contains a colon OR a 4-digit year OR an italic title marker
    def looks_like_citation(line: str) -> bool:
        if len(line) < 12:
            return False
        if not re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", line):
            return False
        if not re.search(r"[A-Z][a-z]+", line):
            return False
        return line.endswith((".", ")", "）", "】"))
    return all(looks_like_citation(line) for line in lines)


EXEMPTION_RULES = (
    _is_apparatus_segment,
    _is_quote_segment,
    _is_foreign_script_segment,
    _is_glossary_term_segment,
    _is_citation_year_segment,
    _is_pure_citation_segment,
)


def apply_review_exemptions(segment: dict[str, Any]) -> tuple[bool, str | None]:
    """Run the exemption rules in order. Return ``(exempt, reason)``."""
    for rule in EXEMPTION_RULES:
        try:
            if rule(segment):
                return True, rule.__name__
        except Exception:
            # never let an exemption crash the detector
            continue
    return False, None


def summarise_exemptions(segments: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for seg in segments:
        exempt, reason = apply_review_exemptions(seg)
        if not exempt or not reason:
            continue
        counts[reason] = counts.get(reason, 0) + 1
    return counts
