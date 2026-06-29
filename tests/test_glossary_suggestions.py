from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.glossary import extract_glossary_candidates
from pdf_translator.glossary_suggestions import suggest_glossary_targets


def _policy_book() -> dict:
    return {
        "metadata": {"title": "Good Company", "author": "Lenore Palladino"},
        "chapters": [
            {
                "chapter_id": "ch-001",
                "title": "Introduction",
                "markdown": "Shareholder Primacy shaped corporate governance.",
            },
            {
                "chapter_id": "ch-002",
                "title": "Policy",
                "markdown": "Shareholder Primacy continued in debates.",
            },
        ],
    }


def test_suggest_writes_target_suggestion_with_mock(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(_policy_book()), encoding="utf-8")
    extract_glossary_candidates(run_dir, max_candidates=10, profile="social_econ_philosophy")

    result = suggest_glossary_targets(run_dir, translator="mock")
    shareholder = next(
        item for item in result["candidates"] if item["source"] == "Shareholder Primacy"
    )
    assert shareholder["target_suggestion"] == "股东至上"
    assert shareholder["suggestion_confidence"] >= 0.9
    assert (run_dir / "glossary" / "suggestions.json").is_file()


def test_suggest_does_not_modify_active_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(_policy_book()), encoding="utf-8")
    extract_glossary_candidates(run_dir, max_candidates=10, profile="social_econ_philosophy")
    active_path = run_dir / "glossary" / "active.json"
    before = json.loads(active_path.read_text(encoding="utf-8"))

    suggest_glossary_targets(run_dir, translator="mock")

    after = json.loads(active_path.read_text(encoding="utf-8"))
    assert after == before
