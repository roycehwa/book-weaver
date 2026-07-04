from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.glossary import (
    apply_glossary_decision,
    extract_glossary_candidates,
    glossary_status,
    glossary_terms_missing_in_translation,
    load_active_entries_for_translation,
    load_active_glossary,
    select_glossary_entries_for_text,
    migrate_glossary_variants,
)


def test_load_active_entries_canonicalizes_legacy_ocr_spacing(tmp_path: Path) -> None:
    (tmp_path / "glossary").mkdir()
    (tmp_path / "glossary" / "active.json").write_text(
        json.dumps(
            {
                "schema": "phase_a_glossary_v1",
                "entries": [
                    {
                        "source": "Charles IIand",
                        "target": "查理二世与",
                        "status": "active",
                    },
                    {
                        "source": "DeLa Mare",
                        "target": "德拉马尔",
                        "status": "active",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    entries = load_active_entries_for_translation(tmp_path)

    assert [entry["source"] for entry in entries] == [
        "Charles II and",
        "De La Mare",
    ]


def test_extract_glossary_candidates_writes_candidates_and_active(tmp_path: Path) -> None:
    book = {
        "metadata": {"title": "The Yellow Emperor", "author": "Test Author"},
        "chapters": [
            {
                "chapter_id": "ch-001",
                "title": "The Yellow Emperor",
                "markdown": "Yellow Emperor met the Ritual Office. Shareholder Primacy shaped policy.",
            },
            {
                "chapter_id": "ch-002",
                "title": "Ritual Office",
                "markdown": "Yellow Emperor visited the Ritual Office again. Shareholder Primacy returned.",
            },
        ],
    }
    run_dir = tmp_path
    (run_dir / "book.json").write_text(json.dumps(book), encoding="utf-8")
    (run_dir / "book.md").write_text("# The Yellow Emperor\n\nRitual Office", encoding="utf-8")

    result = extract_glossary_candidates(run_dir)

    assert (run_dir / "glossary" / "candidates.json").exists()
    assert (run_dir / "glossary" / "active.json").exists()
    assert (run_dir / "glossary" / "decisions.jsonl").exists()
    assert any(item["source"] == "Shareholder Primacy" for item in result["candidates"])
    assert load_active_glossary(run_dir)["entries"] == []


def test_apply_glossary_decision_updates_active_and_decision_log(tmp_path: Path) -> None:
    (tmp_path / "glossary").mkdir()
    (tmp_path / "glossary" / "active.json").write_text(
        json.dumps({"schema": "phase_a_glossary_v1", "entries": []}),
        encoding="utf-8",
    )

    apply_glossary_decision(
        tmp_path,
        source="Yellow Emperor",
        target="黄帝",
        term_type="cultural_term",
        status="active",
        decided_by="user",
    )

    active = load_active_glossary(tmp_path)
    assert active["entries"][0]["source"] == "Yellow Emperor"
    assert active["entries"][0]["target"] == "黄帝"
    log = (tmp_path / "glossary" / "decisions.jsonl").read_text(encoding="utf-8")
    assert '"event": "glossary_decision"' in log


def test_apply_glossary_decision_replaces_punctuation_variant(tmp_path: Path) -> None:
    (tmp_path / "glossary").mkdir()
    (tmp_path / "glossary" / "active.json").write_text(
        json.dumps(
            {
                "schema": "phase_a_glossary_v1",
                "entries": [
                    {
                        "source": "Soviet Union'",
                        "target": "苏维埃联盟",
                        "status": "active",
                        "updated_by": "machine",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    apply_glossary_decision(
        tmp_path,
        source="Soviet Union",
        target="苏联",
        term_type="concept",
        status="active",
        decided_by="user",
    )

    active = load_active_glossary(tmp_path)
    assert len(active["entries"]) == 1
    assert active["entries"][0]["source"] == "Soviet Union"
    assert active["entries"][0]["target"] == "苏联"
    assert active["entries"][0]["updated_by"] == "user"


def test_migrate_glossary_variants_prefers_user_decision(tmp_path: Path) -> None:
    glossary_dir = tmp_path / "glossary"
    glossary_dir.mkdir()
    (glossary_dir / "active.json").write_text(
        json.dumps(
            {
                "schema": "phase_a_glossary_v1",
                "entries": [
                    {
                        "source": "Soviet Union'",
                        "target": "苏维埃联盟",
                        "status": "active",
                        "updated_by": "machine",
                    },
                    {
                        "source": "Soviet Union",
                        "target": "苏联",
                        "status": "active",
                        "updated_by": "user",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = migrate_glossary_variants(tmp_path)

    assert result["merged_count"] == 1
    assert load_active_glossary(tmp_path)["entries"][0]["target"] == "苏联"


def test_select_glossary_entries_for_text_uses_relevant_active_terms() -> None:
    entries = [
        {"source": "Yellow Emperor", "target": "黄帝", "status": "active", "evidence": ["ch-001"]},
        {"source": "Ritual Office", "target": "礼官署", "status": "active", "evidence": ["ch-002"]},
        {"source": "Unused Name", "target": "未用名", "status": "active", "evidence": ["ch-099"]},
    ]

    selected = select_glossary_entries_for_text("The Yellow Emperor speaks.", entries, chapter_id="ch-001", limit=2)

    assert [entry["source"] for entry in selected] == ["Yellow Emperor"]


def test_select_glossary_entries_prefers_longest_overlapping_term() -> None:
    entries = [
        {"source": "World War", "target": "世界大战", "status": "active"},
        {"source": "World War II", "target": "第二次世界大战", "status": "active"},
        {"source": "Unused Term", "target": "未使用", "status": "active", "evidence": ["ch-001"]},
    ]

    selected = select_glossary_entries_for_text(
        "After World War II, policy changed.",
        entries,
        chapter_id="ch-001",
    )

    assert [entry["source"] for entry in selected] == ["World War II"]


def test_select_glossary_entries_rejects_fragment_of_longer_proper_name() -> None:
    entries = [
        {"source": "Second Sino", "target": "第二次中日战争", "status": "active"},
        {"source": "Japanese War", "target": "日本战争", "status": "active"},
    ]

    selected = select_glossary_entries_for_text(
        "The Second Sino–Japanese War changed the region.",
        entries,
        chapter_id="ch-001",
    )

    assert selected == []


def test_glossary_status_reports_counts(tmp_path: Path) -> None:
    run_dir = tmp_path / "book-run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "metadata": {"title": "Sample Book"},
                "chapters": [
                    {"chapter_id": "ch-001", "markdown": "Shareholder Primacy and corporate boards shaped policy."},
                    {"chapter_id": "ch-002", "markdown": "Shareholder Primacy continued across federal incorporation rules."},
                ],
            }
        ),
        encoding="utf-8",
    )
    extract_glossary_candidates(run_dir)
    apply_glossary_decision(
        run_dir,
        source="Yellow Emperor",
        target="黄帝",
        term_type="cultural_term",
        status="active",
        decided_by="user",
    )

    status = glossary_status(run_dir)

    assert status["candidate_count"] >= 1
    assert status["active_count"] == 1


def test_glossary_terms_missing_in_translation_detects_absent_targets() -> None:
    entries = [
        {"source": "Shareholder Primacy", "target": "股东至上", "status": "active"},
    ]
    missing = glossary_terms_missing_in_translation(
        "Shareholder Primacy shaped corporate law.",
        "股东优先塑造了公司法。",
        entries,
    )
    assert missing == [{"source": "Shareholder Primacy", "target": "股东至上"}]


def test_glossary_terms_missing_in_translation_ignores_irrelevant_terms() -> None:
    entries = [
        {"source": "Shareholder Primacy", "target": "股东至上", "status": "active"},
    ]
    assert glossary_terms_missing_in_translation(
        "Corporate boards shaped policy.",
        "董事会塑造了政策。",
        entries,
        chapter_id=None,
    ) == []


def test_glossary_terms_ignore_markdown_image_destinations() -> None:
    entries = [
        {
            "source": "Geneva and Savoy",
            "target": "日内瓦与萨瓦",
            "status": "active",
        },
        {
            "source": "Mathieu Caesar",
            "target": "马蒂厄·凯撒",
            "status": "active",
        },
    ]
    source = (
        "The economy remained stable.\n\n"
        "![Figure 1](/tmp/The Uncertain World of Geneva and Savoy/"
        "Mathieu Caesar/figure.png)"
    )

    assert glossary_terms_missing_in_translation(
        source,
        "经济保持稳定。",
        entries,
    ) == []


def test_glossary_term_does_not_match_prefix_of_longer_word() -> None:
    entries = [
        {
            "source": "Japanese War",
            "target": "抗日战争",
            "status": "active",
        }
    ]

    assert glossary_terms_missing_in_translation(
        "The report documented Japanese wartime atrocities.",
        "报告记录了日本战时暴行。",
        entries,
    ) == []


def test_glossary_terms_missing_prefers_longest_non_overlapping_match() -> None:
    entries = [
        {"source": "Steel Works", "target": "钢铁厂", "status": "active"},
        {
            "source": "Anshan Iron and Steel Works",
            "target": "鞍钢",
            "status": "active",
        },
    ]
    missing = glossary_terms_missing_in_translation(
        "Anshan Iron and Steel Works (Angang), located in Manchuria.",
        "安山钢铁公司位于满洲。",
        entries,
        chapter_id=None,
    )
    assert missing == [{"source": "Anshan Iron and Steel Works", "target": "鞍钢"}]
