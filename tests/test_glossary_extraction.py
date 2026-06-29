from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.glossary import extract_glossary_candidates


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
