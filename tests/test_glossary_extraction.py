from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.glossary import extract_glossary_candidates
from pdf_translator.glossary_extraction import (
    candidate_integrity_rejection,
    canonical_source_key,
    canonical_source_term,
    extract_connector_phrases,
)


def test_extract_filters_book_title_and_surfaces_policy_terms(tmp_path: Path) -> None:
    book = {
        "metadata": {
            "title": "Good Company",
            "subtitle": "Economic Policy after Shareholder Primacy",
            "author": "Lenore Palladino",
            "publisher": "University of Chicago Press",
        },
        "chapters": [
            {
                "chapter_id": "ch-001",
                "title": "Introduction",
                "markdown": (
                    "Good Company asks what shareholder primacy means for workers. "
                    "Shareholder Primacy shaped corporate governance. "
                    "The Accountable Capitalism Act proposed reforms."
                ),
            },
            {
                "chapter_id": "ch-002",
                "title": "Policy",
                "markdown": (
                    "Shareholder Primacy continued to dominate boards. "
                    "Accountable Capitalism Act resurfaced in debates. "
                    "Federal incorporation rules mattered."
                ),
            },
            {
                "chapter_id": "ch-003",
                "title": "Index",
                "markdown": "Shareholder Primacy, Accountable Capitalism Act, Adam Smith",
            },
        ],
    }
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(book), encoding="utf-8")

    result = extract_glossary_candidates(run_dir, max_candidates=20)
    sources = [item["source"] for item in result["candidates"]]

    assert "Good Company" not in sources
    assert "Shareholder Primacy" in sources
    assert "Accountable Capitalism Act" in sources
    assert len(sources) <= 20
    shareholder = next(item for item in result["candidates"] if item["source"] == "Shareholder Primacy")
    assert shareholder["chapter_count"] >= 2
    assert shareholder["reasons"]


def test_extraction_policy_file_written(tmp_path: Path) -> None:
    book = {
        "metadata": {"title": "Sample Book"},
        "chapters": [
            {"chapter_id": "ch-001", "markdown": "Shareholder Primacy and corporate boards."},
            {"chapter_id": "ch-002", "markdown": "Shareholder Primacy and federal incorporation rules."},
        ],
    }
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(book), encoding="utf-8")
    extract_glossary_candidates(run_dir)
    policy = json.loads((run_dir / "glossary" / "extraction-policy.json").read_text(encoding="utf-8"))
    assert policy["schema"] == "phase_a_glossary_extraction_v2"
    assert policy.get("glossary_profile") == "social_econ_philosophy"
    assert policy["stats"]["surfaced"] >= 1


def test_extract_merges_trailing_apostrophe_variants(tmp_path: Path) -> None:
    assert canonical_source_key("Soviet Union") == canonical_source_key("Soviet Union'")
    assert canonical_source_key("Soviet Union") == canonical_source_key("Soviet Union’")

    book = {
        "metadata": {"title": "Industrial History"},
        "chapters": [
            {
                "chapter_id": "ch-001",
                "markdown": (
                    "Soviet Union shaped policy. Soviet Union' policy changed. "
                    "Soviet Union’ influence remained. Soviet Union shaped planning."
                ),
            }
        ],
    }
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(book), encoding="utf-8")

    result = extract_glossary_candidates(run_dir, max_candidates=20)
    matches = [
        item
        for item in result["candidates"]
        if item["source"].rstrip("'’") == "Soviet Union"
    ]

    assert len(matches) == 1
    assert matches[0]["source"] == "Soviet Union"


def test_canonical_source_key_splits_roman_connector_glue() -> None:
    assert canonical_source_key("Charles IIandhis") == canonical_source_key("Charles II andhis")
    assert canonical_source_key("Charles IIand") == canonical_source_key("Charles II and")


def test_canonical_source_term_does_not_split_words_starting_with_roman_letters() -> None:
    assert canonical_source_term("Iran-Iraq War") == "Iran-Iraq War"
    assert canonical_source_term("Iraqi Women") == "Iraqi Women"
    assert canonical_source_term("Institute of Peace") == "Institute of Peace"
    assert canonical_source_term("Vice Chair") == "Vice Chair"


def test_connector_extraction_keeps_complete_title_cased_tail() -> None:
    phrases = extract_connector_phrases(
        "The Federation of Iraqi Women met the United States Institute of Peace. "
        "They discussed the Mother of All Battles."
    )

    assert "Federation of Iraqi Women" in phrases
    assert "United States Institute of Peace" in phrases
    assert "Mother of All Battles" in phrases
    assert "Federation of Iraqi" not in phrases
    assert "Institute of Peace" not in phrases


def test_candidate_integrity_rejects_clause_leads_and_incomplete_modifiers() -> None:
    assert candidate_integrity_rejection("During the Gulf War") == "clause_fragment"
    assert candidate_integrity_rejection("Making of Modern") == "clause_fragment"
    assert candidate_integrity_rejection("District Advisory") == "incomplete_trailing_modifier"
    assert candidate_integrity_rejection("Despite War") == "clause_fragment"
    assert candidate_integrity_rejection("Journal of Middle East Studies") == "bibliographic_label"
    assert candidate_integrity_rejection("Glossary of Key Arabic Terms") == "bibliographic_label"
    assert candidate_integrity_rejection("Iraq in Wartime*") == "markup_contamination"
    assert candidate_integrity_rejection("Iran-Iraq War") is None
    assert candidate_integrity_rejection("United States Institute of Peace") is None


def test_extract_filters_fragment_lead_phrases(tmp_path: Path) -> None:
    book = {
        "metadata": {"title": "Sample Book"},
        "chapters": [
            {
                "chapter_id": "ch-001",
                "markdown": (
                    "Within the Sabaudian network the conflict escalated. "
                    "Between Pulpit and Reformation this appears repeatedly. "
                    "Charles IIandhis advisors dominated court politics."
                ),
            },
            {
                "chapter_id": "ch-002",
                "markdown": (
                    "Within the Sabaudian network remained unstable. "
                    "Charles IIandhis advisors appeared again."
                ),
            },
        ],
    }
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(book), encoding="utf-8")

    result = extract_glossary_candidates(run_dir, max_candidates=20)
    sources = [item["source"] for item in result["candidates"]]

    assert "Within the Sabaudian" not in sources
    assert "Between Pulpit" not in sources
    assert all("IIand" not in source for source in sources)
