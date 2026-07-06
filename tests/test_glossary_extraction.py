from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.glossary import extract_glossary_candidates
from pdf_translator.glossary_extraction import canonical_source_key, canonical_source_term


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


def test_canonical_source_key_preserves_islam_and_iraq() -> None:
    assert canonical_source_key("Islamic State") == canonical_source_key("Islamic State")
    assert canonical_source_key("Iraq") == canonical_source_key("Iraq")
    assert canonical_source_key("Ibn Fadl") == canonical_source_key("Ibn Fadl")
    assert " " not in canonical_source_term("Islamic")
    assert canonical_source_key("Charles IIandhis") == canonical_source_key("Charles II andhis")


def test_extract_rejects_generic_century_and_geography(tmp_path: Path) -> None:
    book = {
        "metadata": {"title": "Land and Trade in Early Islam"},
        "chapters": [
            {
                "chapter_id": "ch-001",
                "markdown": (
                    "The seventh century shaped Near East trade. Seventh Century markets expanded. "
                    "Far East contacts remained limited. Early Islamic commerce grew across Iraq."
                ),
            },
            {
                "chapter_id": "ch-002",
                "markdown": (
                    "Seventh Century exchange continued. Middle East routes linked East Africa. "
                    "The Abbasid Caliphate centralized trade. Islamic State institutions evolved."
                ),
            },
            {
                "chapter_id": "ch-003",
                "title": "Index",
                "markdown": "Abbasid Caliphate, Islamic State, Ibn Fadl",
            },
        ],
    }
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(book), encoding="utf-8")

    result = extract_glossary_candidates(run_dir, max_candidates=50)
    sources = [item["source"] for item in result["candidates"]]

    assert not any("I slam" in source or "I raq" in source for source in sources)
    assert "Seventh Century" not in sources
    assert "Near East" not in sources
    assert "Far East" not in sources
    assert "Islamic State" in sources
    assert len(sources) < 10


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
