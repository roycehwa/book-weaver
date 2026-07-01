from __future__ import annotations

import json
import time
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


def test_suggest_falls_back_to_deepl_on_timeout(monkeypatch, tmp_path: Path) -> None:
    import pdf_translator.glossary_suggestions as glossary_suggestions

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(_policy_book()), encoding="utf-8")
    extract_glossary_candidates(run_dir, max_candidates=10, profile="social_econ_philosophy")

    def raise_timeout(*args, **kwargs):
        raise ValueError(
            "MiniMax glossary suggestion failed: "
            "HTTPSConnectionPool(host='api.minimaxi.com', port=443): Read timed out. (read timeout=10)"
        )

    class FakeDeepL:
        name = "deepl"

    monkeypatch.setattr(glossary_suggestions, "_generate_suggestions", raise_timeout)
    monkeypatch.setattr(
        glossary_suggestions,
        "_deepl_translate_term_map",
        lambda terms, **kwargs: {term: f"译_{term}" for term in terms},
    )
    monkeypatch.setattr(
        "pdf_translator.translate._resolve_fallback_translator",
        lambda primary_name: FakeDeepL(),
    )

    result = suggest_glossary_targets(run_dir, translator="minimax")
    shareholder = next(
        item for item in result["candidates"] if item["source"] == "Shareholder Primacy"
    )
    assert shareholder["suggestion_source"] == "deepl"
    assert result["report"]["deepl_fallback_count"] >= 1


def test_suggest_falls_back_to_deepl_on_sensitive(monkeypatch, tmp_path: Path) -> None:
    import pdf_translator.glossary_suggestions as glossary_suggestions

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(_policy_book()), encoding="utf-8")
    extract_glossary_candidates(run_dir, max_candidates=10, profile="social_econ_philosophy")

    def raise_sensitive(*args, **kwargs):
        raise ValueError("MiniMax glossary suggestion failed: output new_sensitive (1027)")

    class FakeDeepL:
        name = "deepl"

    monkeypatch.setattr(glossary_suggestions, "_generate_suggestions", raise_sensitive)
    monkeypatch.setattr(
        glossary_suggestions,
        "_deepl_translate_term_map",
        lambda terms, **kwargs: {term: f"译_{term}" for term in terms},
    )
    monkeypatch.setattr(
        "pdf_translator.translate._resolve_fallback_translator",
        lambda primary_name: FakeDeepL(),
    )

    result = suggest_glossary_targets(run_dir, translator="minimax")
    shareholder = next(
        item for item in result["candidates"] if item["source"] == "Shareholder Primacy"
    )
    assert shareholder["target_suggestion"] == "译_Shareholder Primacy"
    assert shareholder["suggestion_source"] == "deepl"
    assert result["report"]["deepl_fallback_count"] >= 1


def test_suggest_skips_adopted_terms(tmp_path: Path) -> None:
    import pdf_translator.glossary as glossary_mod

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(_policy_book()), encoding="utf-8")
    extract_glossary_candidates(run_dir, max_candidates=10, profile="social_econ_philosophy")
    candidates_path = run_dir / "glossary" / "candidates.json"
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    payload["candidates"].append(
        {
            "source": "Corporate Governance",
            "type": "concept",
            "score": 1.0,
            "occurrences": 2,
        }
    )
    candidates_path.write_text(json.dumps(payload), encoding="utf-8")
    glossary_mod.apply_glossary_decision(
        run_dir,
        source="Shareholder Primacy",
        target="股东至上（已定稿）",
        term_type="concept",
        status="active",
        decided_by="test",
    )

    result = suggest_glossary_targets(run_dir, translator="mock")
    shareholder = next(
        item for item in result["candidates"] if item["source"] == "Shareholder Primacy"
    )
    governance = next(
        item for item in result["candidates"] if item["source"] == "Corporate Governance"
    )
    assert shareholder.get("suggestion_source") != "mock"
    assert governance.get("target_suggestion")
    assert result["report"]["skipped_locked_count"] == 1
    assert result["report"]["suggest_scope"] == "pending_only"


def test_stale_running_lock_not_immediate(tmp_path: Path) -> None:
    import pdf_translator.glossary_suggestions as glossary_suggestions

    run_dir = tmp_path / "run"
    glossary_dir = run_dir / "glossary"
    glossary_dir.mkdir(parents=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (glossary_dir / "suggest-running.json").write_text(
        json.dumps({"status": "running", "updated_at": now}),
        encoding="utf-8",
    )

    status = glossary_suggestions.read_suggest_status(run_dir)
    assert status["status"] == "running"


def test_stale_running_lock_marked_failed(tmp_path: Path) -> None:
    import pdf_translator.glossary_suggestions as glossary_suggestions

    run_dir = tmp_path / "run"
    glossary_dir = run_dir / "glossary"
    glossary_dir.mkdir(parents=True)
    stale_time = "2020-01-01T00:00:00Z"
    (glossary_dir / "suggest-running.json").write_text(
        json.dumps({"status": "running", "updated_at": stale_time}),
        encoding="utf-8",
    )

    status = glossary_suggestions.read_suggest_status(run_dir)
    assert status["status"] == "failed"
    assert status.get("stale") is True
