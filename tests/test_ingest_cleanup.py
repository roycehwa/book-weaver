"""Tests for the PDF ingest cleanup pass.

The cleanup is invoked right before ``normalized.md`` is written. It
must repair the artefacts that the upstream Docling pipeline produces
and that the glossary extractor was wrongly promoting to candidates.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(ROOT))

from pdf_translator.ingest_cleanup import (  # noqa: E402
    clean_pdf_ingest_markdown,
)


# ---------------------------------------------------------------------------
# Roman-numeral break restoration
# ---------------------------------------------------------------------------


def test_restores_space_after_II_glued_to_lowercase_word():
    src = "Charles IIand the duke of Savoy"
    out = clean_pdf_ingest_markdown(src)
    assert out == "Charles II and the duke of Savoy", out


def test_restores_space_after_VIII_glued_to_lowercase_word():
    src = "Amadeus VIIIand the Sabaudian"
    out = clean_pdf_ingest_markdown(src)
    assert out == "Amadeus VIII and the Sabaudian", out


def test_restores_space_after_IV_glued_to_lowercase_word():
    src = "Henry IVwas crowned king"
    out = clean_pdf_ingest_markdown(src)
    assert out == "Henry IV was crowned king", out


def test_restores_space_after_roman_glued_to_capitalized_word():
    # When the next word happens to be capitalised (start of a new
    # sentence) we still want to insert the space, but keep the
    # capitalisation as-is.
    src = "After the death of Henry IV,Philip"
    out = clean_pdf_ingest_markdown(src)
    assert out == "After the death of Henry IV, Philip", out


def test_does_not_split_already_correctly_spaced_text():
    src = "Charles II and the duke"
    out = clean_pdf_ingest_markdown(src)
    assert out == "Charles II and the duke", out


def test_does_not_split_short_roman_like_II_when_followed_by_space():
    # Sanity check: ``II`` followed by an actual space should be left
    # alone.
    src = "The II World War"
    out = clean_pdf_ingest_markdown(src)
    assert out == "The II World War", out


def test_keeps_long_roman_numerals_intact_when_correctly_spaced():
    src = "The accession of Louis XIV in 1643"
    out = clean_pdf_ingest_markdown(src)
    assert out == src, out


# ---------------------------------------------------------------------------
# Lowercase particle restoration
# ---------------------------------------------------------------------------


def test_lowercases_della_particle_glued_to_capitalised_surname():
    # Della + Rovere style — particle is glued without space.
    src = "Giuliano DellaRovere was pope"
    out = clean_pdf_ingest_markdown(src)
    assert out == "Giuliano Della Rovere was pope", out


def test_lowercases_la_particle_glued_to_capitalised_surname():
    # ``deLa`` → ``de La``: the particle is restored to lowercase but
    # the following capitalised token (``La``/``Baume``) keeps its
    # original case. This is the same heuristic used for ``DellaRovere``
    # above.
    src = "Jean deLa Baume was bishop"
    out = clean_pdf_ingest_markdown(src)
    assert out == "Jean de La Baume was bishop", out


def test_does_not_touch_properly_spaced_della():
    src = "Giuliano Della Rovere was pope"
    out = clean_pdf_ingest_markdown(src)
    assert out == src, out


def test_does_not_lowercase_particle_when_already_lowercase():
    src = "He spoke of della Rovere"
    out = clean_pdf_ingest_markdown(src)
    assert out == src, out


# ---------------------------------------------------------------------------
# Soft-hyphen handling
# ---------------------------------------------------------------------------


def test_strips_soft_hyphens():
    src = "recon\u00adnaissance"
    out = clean_pdf_ingest_markdown(src)
    assert out == "reconnaissance", out


def test_strips_soft_hyphen_between_roman_and_word():
    src = "Charles II\u00adand the duke"
    out = clean_pdf_ingest_markdown(src)
    # After the soft-hyphen is removed, the regex still matches
    # ``IIand`` and restores the space.
    assert out == "Charles II and the duke", out


# ---------------------------------------------------------------------------
# Realistic full paragraph
# ---------------------------------------------------------------------------


def test_full_paragraph_with_multiple_glued_sites():
    src = (
        "The reign of Charles IIand the Sabaudian principality was long. "
        "In Lausanne, Giuliano DellaRovere was bishop. "
        "After the death of Henry IV,Philip returned."
    )
    out = clean_pdf_ingest_markdown(src)
    assert out == (
        "The reign of Charles II and the Sabaudian principality was long. "
        "In Lausanne, Giuliano Della Rovere was bishop. "
        "After the death of Henry IV, Philip returned."
    ), out


def test_does_not_double_space_after_cleanup():
    src = "Charles IIand  the duke"
    out = clean_pdf_ingest_markdown(src)
    # No double spaces even when the source had them.
    assert "  " not in out, out


# ---------------------------------------------------------------------------
# Glossary extractor integration: after the cleanup, the broken
# phrases must not be promoted by ``extract_candidate_phrases`` /
# ``extract_connector_phrases``.
# ---------------------------------------------------------------------------


def test_cleaned_text_does_not_yield_IIand_candidate():
    from pdf_translator.glossary_extraction import (
        extract_candidate_phrases,
        extract_connector_phrases,
    )

    src = "Charles IIand the duke was crowned."
    cleaned = clean_pdf_ingest_markdown(src)
    candidates = extract_candidate_phrases(cleaned)
    connectors = extract_connector_phrases(cleaned)
    assert "Charles IIand" not in candidates
    assert "Charles IIand" not in connectors
    # The roman-numeral regnal marker should still appear, correctly
    # spaced.
    assert "Charles II" in candidates or "Charles II" in connectors
