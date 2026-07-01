from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.glossary import apply_glossary_decision, extract_glossary_candidates
from pdf_translator.glossary_suggestions import (
    _resolve_suggest_strategy,
    effective_suggest_strategy_label,
    suggest_glossary_targets,
)
from pdf_translator.workflow import (
    clear_glossary_suggestions,
    glossary_ready_summary,
    mark_glossary_ready,
    reset_glossary_review,
)


def _policy_book() -> dict:
    return {
        "metadata": {"title": "Good Company", "author": "Lenore Palladino"},
        "chapters": [
            {
                "chapter_id": "ch-001",
                "title": "Introduction",
                "markdown": "Shareholder Primacy shaped corporate governance.",
            }
        ],
    }


def test_reset_glossary_review_clears_active_and_workflow(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(_policy_book()), encoding="utf-8")
    extract_glossary_candidates(run_dir, max_candidates=10, profile="social_econ_philosophy")
    apply_glossary_decision(
        run_dir,
        source="Shareholder Primacy",
        target="股东至上",
        term_type="concept",
        status="active",
        decided_by="user",
    )
    mark_glossary_ready(run_dir)

    result = reset_glossary_review(run_dir)
    summary = glossary_ready_summary(run_dir)

    assert result["active_count"] == 0
    assert summary["workflow_stage"] == "awaiting_glossary"
    assert summary["is_ready"] is False


def test_reset_clears_policy_annotations(tmp_path: Path) -> None:
    from pdf_translator.glossary import clear_glossary_policy_round_annotations

    run_dir = tmp_path / "run"
    glossary_dir = run_dir / "glossary"
    glossary_dir.mkdir(parents=True)
    policy = {
        "glossary_profile": "social_econ_philosophy",
        "sensitive_content_risk": "high",
        "glossary_suggest_strategy": "deepl_first",
    }
    (glossary_dir / "extraction-policy.json").write_text(json.dumps(policy), encoding="utf-8")

    stripped = clear_glossary_policy_round_annotations(run_dir)
    assert stripped is not None
    updated = json.loads((glossary_dir / "extraction-policy.json").read_text(encoding="utf-8"))
    assert "sensitive_content_risk" not in updated
    assert "glossary_suggest_strategy" not in updated
    assert updated["glossary_profile"] == "social_econ_philosophy"


def test_clear_glossary_suggestions_keeps_adoptions(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(_policy_book()), encoding="utf-8")
    extracted = extract_glossary_candidates(run_dir, max_candidates=10, profile="social_econ_philosophy")
    apply_glossary_decision(
        run_dir,
        source="Shareholder Primacy",
        target="股东至上",
        term_type="concept",
        status="active",
        decided_by="user",
    )
    glossary_dir = run_dir / "glossary"
    glossary_dir.mkdir(parents=True, exist_ok=True)
    candidates = list(extracted.get("candidates") or [])
    if not candidates:
        candidates = [{"source": "Shareholder Primacy", "type": "concept", "status": "candidate"}]
    candidates[0]["target_suggestion"] = "测试建议"
    candidates[0]["suggestion_confidence"] = 0.9
    candidates_path = glossary_dir / "candidates.json"
    candidates_path.write_text(
        json.dumps({"schema": "phase_a_glossary_v1", "candidates": candidates}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = clear_glossary_suggestions(run_dir)
    summary = glossary_ready_summary(run_dir)
    updated = json.loads(candidates_path.read_text(encoding="utf-8"))

    assert result["active_count"] == 1
    assert result["kept_adoptions"] is True
    assert result["cleared_suggestion_count"] >= 1
    assert summary["active_count"] == 1
    assert not any(item.get("target_suggestion") for item in updated["candidates"])


def test_resolve_strategy_ignores_legacy_auto_deepl_without_user_source() -> None:
    policy = {"glossary_suggest_strategy": "deepl_first"}
    assert _resolve_suggest_strategy(policy, primary_translator="minimax") == "minimax_with_deepl_fallback"


def test_resolve_strategy_honors_user_explicit_deepl(monkeypatch) -> None:
    policy = {
        "glossary_suggest_strategy": "deepl_first",
        "glossary_suggest_strategy_source": "user",
    }
    monkeypatch.setattr(
        "pdf_translator.glossary_suggestions._deepl_available",
        lambda **kwargs: True,
    )
    assert _resolve_suggest_strategy(policy, primary_translator="minimax") == "deepl_first"


def test_resolve_strategy_falls_back_without_deepl(monkeypatch) -> None:
    policy = {
        "glossary_suggest_strategy": "deepl_first",
        "glossary_suggest_strategy_source": "user",
    }
    monkeypatch.setattr(
        "pdf_translator.glossary_suggestions._deepl_available",
        lambda **kwargs: False,
    )
    assert _resolve_suggest_strategy(policy, primary_translator="minimax") == "minimax_with_deepl_fallback"
    assert "DeepL 未配置" in effective_suggest_strategy_label(policy, primary_translator="minimax")
