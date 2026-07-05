"""PDF ingest cleanup pass.

This module is invoked immediately before ``normalized.md`` is written
to disk. It repairs three classes of artefacts that the underlying
Docling / PyPdfium pipeline can produce for English / French / Italian
academic prose, and that pollute the downstream glossary extractor:

1. ``Charles IIand`` — a line break between a roman numeral regnal
   marker (II, III, IV, …, XIII) and the next word is collapsed
   without inserting a space, so the next regex / n-gram pass treats
   ``IIand`` as a single token.
2. ``Charles Iand the duke`` — same pattern, the word that follows the
   roman numeral starts with a lowercase letter (because of how Docling
   re-flows text). After the previous step, the joiner is restored as
   ``II and``.
3. ``DellaRovere`` / ``Della Rovere`` — the Italian surname particle
   ``Della`` is sometimes glued to a capitalised surname when the
   source PDF has a soft hyphen / discretionary line break at the end
   of the line.

The pass also revises ``PHRASE_RE`` consumers' contract: see
``glossary_extraction.BLOCK_BOUNDARY`` for how the multi-line scrape
was previously over-matching.

This module is deliberately small and dependency-free: it only sees the
markdown text that is about to be written to disk.
"""

from __future__ import annotations

import re


# A conservative list of regnal / regnal-style roman numerals. The
# short ones (I, V, X, L, C, D, M) are intentionally excluded because
# they collide with ordinary English words and abbreviations.
_REGNAL_ROMAN = frozenset({
    "II", "III", "IV", "VI", "VII", "VIII", "IX",
    "XI", "XII", "XIII", "XIV", "XV", "XVI",
    "XX", "XXI", "XXII", "XXIII",
})

# Italian / French surname particles that occasionally get glued to a
# capitalised following word by a line-break in the source PDF.
_LOWERCASE_PARTICLES = frozenset({
    "de", "du", "del", "della", "delle", "dello", "degli",
    "la", "le", "von", "van", "der", "den", "ten", "ter",
    "af", "av", "zu", "al", "el",
    "mac", "mc", "st", "st.",
})


def _strip_soft_hyphens(text: str) -> str:
    """Remove U+00AD soft hyphens that some PDFs use to mark
    discretionary line breaks within a single word."""
    return text.replace("\u00ad", "")


def _restore_roman_numeral_breaks(text: str) -> str:
    """Insert a space between a regnal roman numeral glued to the next
    word by a soft-hyphen / line-break collapse.

    The upstream ``_normalize_pdf_text_block`` already removes soft
    hyphens and ``-`` hyphens at line ends. The remaining failure
    mode is the case where Docling joined a roman numeral and the next
    word without any joining character at all (so there is nothing to
    strip — the join is just wrong).

    Examples this function handles:

    * ``Charles IIand``     → ``Charles II and``
    * ``Amadeus VIIIand``   → ``Amadeus VIII and``
    * ``Henry IVwas``       → ``Henry IV was``
    * ``Louis XIVof``       → ``Louis XIV of``
    """
    # The glued suffix may be a full lower-case word (``IIand``),
    # a capitalised proper noun (``IVPhilip``), or a comma-separated
    # clause beginning with a capital (``IV,Philip``). We accept an
    # optional inter-word punctuation followed by two or more letters.
    pattern = re.compile(
        r"\b(" + "|".join(sorted(_REGNAL_ROMAN, key=len, reverse=True)) + r")"
        r"([,.;:!?])?([A-Z][a-z]+|[a-z]{2,})"
    )

    def _split(match: re.Match[str]) -> str:
        roman = match.group(1)
        separator = match.group(2) or ""
        following = match.group(3)
        # Preserve the original punctuation between the roman numeral
        # and the following word (``IV,Philip`` → ``IV, Philip``).
        return f"{roman}{separator} {following}"

    return pattern.sub(_split, text)


def _restore_lowercase_particles(text: str) -> str:
    """Lowercase an Italian / French surname particle that has been
    glued to a capitalised following word.

    The pattern matches an internal camelcase token where the first
    half is one of the particles and the second half starts with an
    uppercase letter: ``DellaRovere`` → ``Della Rovere`` (still
    capitalised because it is a real surname, but the particle
    continues to be a single token).

    We do *not* touch legitimately capitalised particles like ``Mac``
    / ``Mc`` / ``St`` — those are rare in book-length prose and the
    ambiguity cost outweighs the benefit.
    """
    # Only fire when the particle is *glued* to the following capitalised
    # token (no space between them). A correctly-spaced ``de la Baume``
    # must be left alone. We generate a case-insensitive alternation by
    # expanding each particle into ``[Ll][Aa]`` so we do not need the
    # ``re.IGNORECASE`` flag (which would also lowercase the lookahead
    # ``[A-Z][a-z]`` and over-match ordinary lowercase words).
    def _case_insensitive(p: str) -> str:
        return "".join(f"[{c.lower()}{c.upper()}]" if c.isalpha() else c for c in p)

    expanded = "|".join(
        _case_insensitive(p)
        for p in sorted(_LOWERCASE_PARTICLES, key=len, reverse=True)
    )
    pattern = re.compile(
        r"(?<![A-Za-z])(?:" + expanded + r")(?=[A-Z][a-z])"
    )
    # We only insert a space; the original case of the particle and
    # the following token is preserved so that real surnames like
    # ``Della Rovere`` stay capitalised.
    def _repair(match: re.Match[str]) -> str:
        glued = match.group(0)
        # If the glued form already starts with a capital letter
        # (``DellaRovere``), the source likely intends a real capitalised
        # surname like ``Della Rovere`` — leave the case alone.
        # If it starts with a lowercase letter (``deLa``), the source
        # is the Italian/French particle ``de`` glued to a capitalised
        # following word; restore the particle to its lower-case form.
        if glued[0].islower():
            return glued.lower() + " "
        return glued + " "

    return pattern.sub(_repair, text)


def _collapse_redundant_spaces(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", text)


def clean_pdf_ingest_markdown(markdown_text: str) -> str:
    """Apply the PDF ingest cleanup pass to a Docling export.

    The function is intentionally order-sensitive:

    1. Strip soft hyphens first so they cannot mask the joiner.
    2. Restore the roman-numeral break — this is the most common
       source of the ``IIand`` / ``VIIIand`` style false-positive
       glossary candidates.
    3. Lowercase glued particles for Italian / French surnames.
    4. Collapse any double spaces introduced by the previous steps.
    """
    text = _strip_soft_hyphens(markdown_text)
    text = _restore_roman_numeral_breaks(text)
    text = _restore_lowercase_particles(text)
    text = _collapse_redundant_spaces(text)
    return text
