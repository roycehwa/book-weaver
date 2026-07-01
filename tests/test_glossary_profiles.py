from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.glossary import apply_glossary_decision, compute_max_candidates, extract_glossary_candidates
from pdf_translator.glossary_extraction import (
    extract_connector_phrases,
    extract_domain_single_words,
    score_glossary_candidate,
)
from pdf_translator.glossary_profiles import (
    FORMAL_LOGIC_PHILOSOPHY,
    HUMANITIES_HISTORY,
    SOCIAL_ECON_PHILOSOPHY,
    detect_glossary_profile,
    profile_policy,
)


def _policy_book() -> dict:
    return {
        "metadata": {
            "title": "Good Company",
            "subtitle": "Economic Policy after Shareholder Primacy",
            "author": "Lenore Palladino",
        },
        "chapters": [
            {
                "chapter_id": "ch-001",
                "title": "Introduction",
                "markdown": (
                    "This book argues that shareholder primacy shaped corporate governance. "
                    "The Accountable Capitalism Act proposed reforms."
                ),
            },
            {
                "chapter_id": "ch-002",
                "title": "Policy Debate",
                "markdown": (
                    "Shareholder primacy continued to dominate boards. "
                    "Federal incorporation and market governance mattered."
                ),
            },
        ],
    }


def _history_book() -> dict:
    chapters = []
    body = (
        "The Cultural Revolution reshaped China. Deng Xiaoping led Reform and Opening Up. "
        "The Gang of Four was purged. In Shanghai protesters gathered. "
    )
    for index in range(1, 13):
        chapters.append(
            {
                "chapter_id": f"ch-{index:03d}",
                "title": f"Chapter {index}",
                "markdown": body + f"Mao Zedong and Zhou Enlai appear in chapter {index}.",
            }
        )
    chapters.append(
        {
            "chapter_id": "ch-index",
            "title": "Index",
            "markdown": "Cultural Revolution, 12-40\nGang of Four, 88\nDeng Xiaoping, 1-200",
        }
    )
    return {
        "metadata": {
            "title": "China After Mao",
            "subtitle": "The Rise of a Superpower",
            "author": "Frank Dikötter",
        },
        "chapters": chapters,
    }


def _logic_book() -> dict:
    body = (
        "Modal logic studies necessity and possibility. Truth conditions for modal operators "
        "raise semantic paradoxes. The liar paradox challenges deflationary theories of truth. "
        "Syntax and semantics interact in formal proofs. Quantifier scope matters for validity. "
        "Tarski and Kripke shaped referential semantics. Russell's paradox informed type theory."
    )
    chapters = []
    for index in range(1, 11):
        chapters.append(
            {
                "chapter_id": f"ch-{index:03d}",
                "title": f"Chapter {index}",
                "markdown": body + f" Chapter {index} revisits modality and truth.",
            }
        )
    chapters.append(
        {
            "chapter_id": "ch-index",
            "title": "Index",
            "markdown": "Modal logic, 12-40\nTruth conditions, 88\nLiar paradox, 1-200",
        }
    )
    return {
        "metadata": {
            "title": "The Road to Paradox",
            "subtitle": "Logic, Syntax, and Truth",
            "author": "Example Author",
        },
        "chapters": chapters,
    }


def test_detect_policy_book_profile() -> None:
    detection = detect_glossary_profile(_policy_book())
    assert detection["glossary_profile"] == SOCIAL_ECON_PHILOSOPHY
    assert detection["glossary_profile_label"] == "社会·经济·哲学"


def test_detect_history_book_profile() -> None:
    detection = detect_glossary_profile(_history_book())
    assert detection["glossary_profile"] == HUMANITIES_HISTORY
    assert detection["glossary_profile_label"] == "人文·历史·艺术"
    assert detection["glossary_profile_confidence"] >= 0.5


def test_detect_logic_book_profile() -> None:
    detection = detect_glossary_profile(_logic_book())
    assert detection["glossary_profile"] == FORMAL_LOGIC_PHILOSOPHY
    assert detection["glossary_profile_label"] == "逻辑·语言哲学"
    assert detection["glossary_profile_confidence"] >= 0.5


def test_domain_single_word_extraction_for_logic_profile() -> None:
    text = "Modal logic and modality appear often. Modal operators and truth recur."
    words = extract_domain_single_words(text, profile_policy(FORMAL_LOGIC_PHILOSOPHY).single_word_markers)
    assert "Modal" in words or "Truth" in words


def test_connector_phrase_extraction() -> None:
    text = "The Gang of Four was purged during Reform and Opening Up."
    phrases = extract_connector_phrases(text)
    assert "Gang of Four" in phrases


def test_cultural_revolution_ranks_higher_under_humanities_policy() -> None:
    humanities = profile_policy(HUMANITIES_HISTORY)
    social = profile_policy(SOCIAL_ECON_PHILOSOPHY)
    h_score, _, h_rejected = score_glossary_candidate(
        "Cultural Revolution",
        occurrences=78,
        chapter_count=12,
        exclusions=set(),
        in_index=True,
        policy=humanities,
    )
    s_score, _, s_rejected = score_glossary_candidate(
        "Cultural Revolution",
        occurrences=78,
        chapter_count=12,
        exclusions=set(),
        in_index=True,
        policy=social,
    )
    assert not h_rejected
    assert h_score > s_score


def test_fragment_phrase_penalized_for_humanities() -> None:
    humanities = profile_policy(HUMANITIES_HISTORY)
    score, reasons, rejected = score_glossary_candidate(
        "In Shanghai",
        occurrences=40,
        chapter_count=10,
        exclusions=set(),
        in_index=False,
        policy=humanities,
    )
    assert any("碎片" in reason for reason in reasons)
    good_score, _, good_rejected = score_glossary_candidate(
        "Deng Xiaoping",
        occurrences=40,
        chapter_count=10,
        exclusions=set(),
        in_index=False,
        policy=humanities,
    )
    assert good_score > score or rejected


def test_extract_writes_profile_v2_policy(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(_history_book()), encoding="utf-8")
    result = extract_glossary_candidates(run_dir, max_candidates=30, profile=HUMANITIES_HISTORY)
    policy = json.loads((run_dir / "glossary" / "extraction-policy.json").read_text(encoding="utf-8"))
    assert policy["schema"] == "phase_a_glossary_extraction_v2"
    assert policy["glossary_profile"] == HUMANITIES_HISTORY
    assert policy["glossary_profile_label"] == "人文·历史·艺术"
    assert policy["profile_policy_version"] == 2
    sources = [item["source"] for item in result["candidates"]]
    assert "Cultural Revolution" in sources
    assert "Gang of Four" in sources
    assert "In Shanghai" not in sources


def test_logic_profile_surfaces_more_domain_terms(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(_logic_book()), encoding="utf-8")
    result = extract_glossary_candidates(run_dir, profile=FORMAL_LOGIC_PHILOSOPHY)
    sources = {item["source"] for item in result["candidates"]}
    assert len(sources) >= 8
    assert any("Modal" in source or "Truth" in source or "Paradox" in source for source in sources)


def test_profile_switch_changes_candidate_set(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(_logic_book()), encoding="utf-8")
    logic = extract_glossary_candidates(run_dir, max_candidates=40, profile=FORMAL_LOGIC_PHILOSOPHY)
    humanities = extract_glossary_candidates(
        run_dir,
        max_candidates=40,
        profile=HUMANITIES_HISTORY,
        profile_source="user",
    )
    logic_sources = {item["source"] for item in logic["candidates"]}
    humanities_sources = {item["source"] for item in humanities["candidates"]}
    assert logic_sources != humanities_sources


def test_compute_max_candidates_scales_with_book() -> None:
    small = compute_max_candidates(_policy_book())
    big = {
        "chapters": [
            {"chapter_id": f"ch-{index:03d}", "markdown": "terminology " * 4000}
            for index in range(1, 26)
        ]
    }
    large = compute_max_candidates(big)
    assert small >= 60
    assert large > small
    assert large <= 200


def test_profile_override_preserves_active_entries(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps(_policy_book()), encoding="utf-8")
    extract_glossary_candidates(run_dir, profile=SOCIAL_ECON_PHILOSOPHY)
    apply_glossary_decision(
        run_dir,
        source="Shareholder Primacy",
        target="股东至上",
        term_type="policy_term",
        status="active",
        decided_by="user",
    )
    extract_glossary_candidates(run_dir, profile=HUMANITIES_HISTORY, profile_source="user")
    active = json.loads((run_dir / "glossary" / "active.json").read_text(encoding="utf-8"))
    assert any(entry["source"] == "Shareholder Primacy" for entry in active["entries"])
    policy = json.loads((run_dir / "glossary" / "extraction-policy.json").read_text(encoding="utf-8"))
    assert policy["glossary_profile_overridden"] is True
