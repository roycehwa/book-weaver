from __future__ import annotations

from pathlib import Path
import json

from pdf_translator.knowledge import (
    apply_user_review,
    build_knowledge_package,
    build_knowledge_plan,
    build_metadata_prior,
    build_suitability_report,
    emit_mindmap_mermaid_from_book,
    emit_wiki_outline_from_book,
    parse_user_review_answers,
)


def test_emit_wiki_outline_writes_index_and_chapter_stubs(tmp_path: Path) -> None:
    book = {
        "chapters": [
            {"index": 1, "title": "Alpha", "markdown": "x"},
            {"index": 2, "title": "Beta", "markdown": "y"},
        ]
    }
    out = tmp_path / "wiki"
    emit_wiki_outline_from_book(book, out)
    assert (out / "index.md").is_file()
    assert (out / "001-alpha.md").is_file()
    assert "Alpha" in (out / "001-alpha.md").read_text(encoding="utf-8")


def test_emit_mindmap_mermaid_writes_fenced_block(tmp_path: Path) -> None:
    book = {"chapters": [{"index": 1, "title": "Only", "markdown": "m"}]}
    p = tmp_path / "mm.md"
    emit_mindmap_mermaid_from_book(book, p)
    text = p.read_text(encoding="utf-8")
    assert "```mermaid" in text
    assert "flowchart TB" in text
    assert "Only" in text


def test_build_knowledge_package_writes_deterministic_core_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    book = {
        "chapters": [
            {
                "index": 1,
                "chapter_id": "ch-001-alpha",
                "title": "Alpha",
                "page_start": 3,
                "page_end": 4,
                "source_pages": [3, 4],
                "markdown": "# Alpha\n\nFirst paragraph.\n\n![Fig](images/f1.png)\n",
            },
            {
                "index": 2,
                "title": "Beta",
                "page_start": 5,
                "page_end": 5,
                "source_pages": [5],
                "markdown": "# Beta\n\n- Item\n",
            },
        ],
        "assets": [{"kind": "figure", "path": "images/f1.png", "page_no": 3}],
    }
    (run_dir / "book.json").write_text(json.dumps(book), encoding="utf-8")
    (run_dir / "translated.md").write_text("# 阿尔法\n\n第一段。\n\n![Fig](images/f1.png)\n\n# 贝塔\n\n- 条目\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps({"source_pdf": "/books/a.pdf", "source_language": "en", "target_language": "zh-CN"}),
        encoding="utf-8",
    )

    paths = build_knowledge_package(run_dir)

    assert paths["manifest"].is_file()
    chapters = json.loads(paths["chapters"].read_text(encoding="utf-8"))
    units = json.loads(paths["semantic_units"].read_text(encoding="utf-8"))
    assets = json.loads(paths["assets"].read_text(encoding="utf-8"))
    source_map = json.loads(paths["source_map"].read_text(encoding="utf-8"))
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert chapters[0]["chapter_id"] == "ch-001-alpha"
    assert chapters[1]["chapter_id"].startswith("ch-002-beta")
    assert [unit["kind"] for unit in units] == ["heading", "paragraph", "image", "heading", "list"]
    assert units[1]["text_translated"] == "第一段。"
    assert units[1]["source_pages"] == [3, 4]
    assert assets == [{"kind": "figure", "path": "images/f1.png", "page_no": 3}]
    assert source_map["semantic_units"]["ch-001-alpha-u0002"]["page_start"] == 3
    assert manifest["counts"] == {"chapters": 2, "semantic_units": 5, "assets": 1}


def test_build_knowledge_package_does_not_force_unsafe_translation_alignment(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {"index": 1, "title": "A", "markdown": "# A\n\nOne.\n\nTwo.\n"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "translated.md").write_text("# 甲\n\n只有一段。\n", encoding="utf-8")

    paths = build_knowledge_package(run_dir)
    units = json.loads(paths["semantic_units"].read_text(encoding="utf-8"))

    assert all(unit["text_translated"] is None for unit in units)
    assert all(unit["translation_alignment"] == "unavailable" for unit in units)


def test_build_suitability_report_detects_argumentative_book(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "chapter_id": "ch-001-introduction",
                        "title": "Introduction",
                        "markdown": (
                            "# Introduction\n\n"
                            "This book argues that political theory requires a new concept of evidence.\n\n"
                            "The argument contrasts earlier theory with new interpretation.\n\n"
                            "However, the author critiques this framework because it hides the central claim.\n\n"
                            "Therefore the chapter develops a theory of institutional evidence.\n"
                        ),
                    }
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_suitability_report(run_dir)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    md = paths["markdown"].read_text(encoding="utf-8")

    assert report["profile"] == "argumentative"
    assert report["network_suitability"] in {"high", "medium"}
    assert "claim" in report["extractable_objects"]
    assert report["chapters"][0]["action"] == "extract"
    assert "Knowledge Suitability Report" in md
    assert "argument_map" in md


def test_build_suitability_report_detects_practical_book(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "A Practical Strategy Guide",
                        "markdown": (
                            "# A Practical Strategy Guide\n\n"
                            "This guide provides a method and a checklist for teams.\n\n"
                            "- Step one: define the problem.\n\n"
                            "- Step two: choose the tool.\n\n"
                            "A case study illustrates when the principle applies.\n"
                        ),
                    }
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_suitability_report(run_dir)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))

    assert report["profile"] == "practical"
    assert "playbook" in report["recommended_outputs"]
    assert "action" in report["extractable_objects"]


def test_build_suitability_report_flags_technical_visual_risk(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "Formula and Data Appendix",
                        "markdown": (
                            "# Formula and Data Appendix\n\n"
                            "The theorem follows from the equation and proof.\n\n"
                            "![Figure](fig1.png)\n\n"
                            "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
                            "![Figure](fig2.png)\n\n"
                            "| C | D |\n| --- | --- |\n| 3 | 4 |\n"
                        ),
                    }
                ],
                "assets": [{"kind": "figure", "path": "fig1.png"}],
            }
        ),
        encoding="utf-8",
    )

    paths = build_suitability_report(run_dir)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))

    assert report["profile"] == "technical_lite"
    assert any(risk["risk"] == "visual_or_table_heavy" for risk in report["risks"])
    assert "automatic_formula_semantics" in report["do_not_extract"]


def test_build_knowledge_plan_selects_argument_network(tmp_path: Path) -> None:
    run_dir = tmp_path / "True Conservatism"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {"index": 1, "title": "Copyright Page", "markdown": "# Copyright Page\n\nCopyright.\n"},
                    {
                        "index": 2,
                        "title": "1. Our Prejudices",
                        "markdown": (
                            "# 1. Our Prejudices\n\n"
                            "This book argues that reason, theory, and humanity require a concept of judgment.\n\n"
                            "The claim is developed through critique and evidence.\n\n"
                            "However, the author contrasts this framework with liberal assumptions.\n\n"
                            "Therefore the argument is about political philosophy and conservative thought.\n"
                        ),
                    },
                    {
                        "index": 3,
                        "title": "2. The Sufficiency of Reason",
                        "markdown": (
                            "# 2. The Sufficiency of Reason\n\n"
                            "The chapter develops a major claim about reason.\n\n"
                            "It provides evidence and responds to objections.\n\n"
                            "The concept of excellence is revised.\n\n"
                            "The argument contrasts rival theories.\n"
                        ),
                    },
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_knowledge_plan(run_dir)
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))
    md = paths["markdown"].read_text(encoding="utf-8")

    assert plan["final_plan"]["primary_network_model"] == "argument_network"
    assert plan["final_plan"]["chapter_roles"][0]["role"] == "skip"
    assert plan["final_plan"]["chapter_roles"][1]["role"] == "extract"
    assert "claims" in md


def test_build_knowledge_plan_selects_playbook_network_and_preserves_use_case_appendix(tmp_path: Path) -> None:
    run_dir = tmp_path / "AI Use Cases for Diplomats"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "Chapter 1 AI and Consular Affairs: Optimizing Visa Services",
                        "markdown": (
                            "# Chapter 1 AI and Consular Affairs: Optimizing Visa Services\n\n"
                            "This guide provides a practical AI use case for diplomats.\n\n"
                            "- Step one: define the use case.\n\n"
                            "- Step two: choose the tool.\n\n"
                            "A case study explains the management practice.\n"
                        ),
                    },
                    {
                        "index": 2,
                        "title": "Appendix A: 100 AI Use Cases for Diplomats",
                        "markdown": (
                            "# Appendix A: 100 AI Use Cases for Diplomats\n\n"
                            "Use case 1 applies AI to consular operations.\n\n"
                            "Use case 2 applies AI to economic affairs.\n\n"
                            "Use case 3 applies AI to public affairs.\n"
                        ),
                    },
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_knowledge_plan(run_dir)
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))
    roles = {chapter["title"]: chapter["role"] for chapter in plan["final_plan"]["chapter_roles"]}

    assert plan["final_plan"]["primary_network_model"] == "playbook_network"
    assert roles["Appendix A: 100 AI Use Cases for Diplomats"] == "preserve"
    assert "action" in plan["final_plan"]["recommended_extractors"]


def test_build_knowledge_plan_selects_event_timeline_network(tmp_path: Path) -> None:
    run_dir = tmp_path / "In Search of National Ancestors"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "2 Locating Religious Revivals in China",
                        "markdown": (
                            "# 2 Locating Religious Revivals in China\n\n"
                            "The historical revival of ceremonies changed the place of heritage.\n\n"
                            "The event involved local actors and a ceremony around ancestors.\n\n"
                            "This chapter explains time, place, and cultural identity.\n\n"
                            "The historical narrative links the revival to later practices.\n"
                        ),
                    },
                    {
                        "index": 2,
                        "title": "3 The Search for a Common Ancestor",
                        "markdown": (
                            "# 3 The Search for a Common Ancestor\n\n"
                            "The chapter follows a historical search for ancestor ceremonies.\n\n"
                            "Actors used heritage branding in different places.\n\n"
                            "The event sequence creates a timeline of cultural revival.\n\n"
                            "The interpretation connects national identity and place.\n"
                        ),
                    },
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_knowledge_plan(run_dir)
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))

    assert plan["final_plan"]["primary_network_model"] == "event_timeline_network"
    assert "event" in plan["final_plan"]["recommended_extractors"]


def test_build_metadata_prior_uses_external_records_as_weak_prior(tmp_path: Path) -> None:
    run_dir = tmp_path / "AI Use Cases for Diplomats -- Donald Kilburg"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"source_pdf": "/books/AI Use Cases for Diplomats -- Donald Kilburg.epub"}),
        encoding="utf-8",
    )

    def fake_fetcher(query: str, timeout_seconds: float) -> list[dict]:
        assert "AI Use Cases for Diplomats" in query
        return [
            {
                "source": "test",
                "title": "AI Use Cases for Diplomats",
                "authors": ["Donald Kilburg"],
                "publisher": "Taylor & Francis",
                "description": "A practical guide with real-world examples, use cases, tools, and strategy.",
                "categories": ["Artificial Intelligence", "Diplomacy", "Technology"],
                "url": "https://example.test/book",
            }
        ]

    paths = build_metadata_prior(run_dir, fetcher=fake_fetcher)
    prior = json.loads(paths["prior"].read_text(encoding="utf-8"))
    md = paths["markdown"].read_text(encoding="utf-8")

    assert prior["primary_network_model"] == "playbook_network"
    assert prior["network_scores"]["playbook_network"] > prior["network_scores"]["event_timeline_network"]
    assert "AI Use Cases for Diplomats" in md


def test_build_knowledge_plan_can_use_cached_metadata_prior(tmp_path: Path) -> None:
    run_dir = tmp_path / "Ambiguous Book"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "A Framework for Action",
                        "markdown": (
                            "# A Framework for Action\n\n"
                            "This chapter discusses a framework.\n\n"
                            "The text uses examples and a guide for practice.\n\n"
                            "The method applies to teams.\n\n"
                            "The strategy becomes an action plan.\n"
                        ),
                    }
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )
    knowledge_dir = run_dir / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "metadata-prior.json").write_text(
        json.dumps(
            {
                "schema": "book_weaver_metadata_prior_v1",
                "query": {"query": "Ambiguous Book", "title_hint": "Ambiguous Book"},
                "primary_network_model": "playbook_network",
                "secondary_network_models": [],
                "confidence": 0.8,
                "network_scores": {
                    "argument_network": 0,
                    "concept_network": 0,
                    "event_timeline_network": 0,
                    "playbook_network": 120,
                    "narrative_network": 0,
                    "faceted_index_network": 0,
                },
                "records": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_knowledge_plan(run_dir, metadata_prior="auto")
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))

    assert plan["metadata_prior"]["primary_network_model"] == "playbook_network"
    assert plan["algorithm_candidate"]["metadata_prior_mode"] == "auto"
    assert plan["algorithm_candidate"]["network_scores"]["playbook_network"] > 0


def test_parse_user_review_answers_extracts_structural_inputs() -> None:
    answers = """
    组织方式：混合，偏 event_timeline_network + concept_network
    必须保留：chronology, glossary, appendix, illustrations, tables
    可以跳过：copyright, index, publisher pages
    参考材料：
    这本书的一篇书评认为它讨论民族祖先、文化遗产建构和地方竞争。
    https://example.test/review
    """

    review = parse_user_review_answers(answers)

    assert review["network_override"] == "event_timeline_network"
    assert "concept_network" in review["secondary_network_models"]
    assert {"chronology", "glossary", "appendix", "illustrations", "tables"}.issubset(
        set(review["preserve_content_types"])
    )
    assert {"copyright", "index", "publisher_pages"}.issubset(set(review["skip_content_types"]))
    assert len(review["references"]) == 2


def test_apply_user_review_updates_plan_without_user_editing_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "Review Book"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "Chapter 1 Practical Guide",
                        "markdown": "# Chapter 1 Practical Guide\n\nA guide with a tool.\n\nA practice.\n\nA case.\n\nAn action.\n",
                    },
                    {
                        "index": 2,
                        "title": "Appendix: Chronology",
                        "markdown": "# Appendix: Chronology\n\n1900 event.\n\n1910 event.\n",
                    },
                    {"index": 3, "title": "Index", "markdown": "# Index\n\nA 1\n"},
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )
    build_knowledge_plan(run_dir)
    answers = tmp_path / "answers.txt"
    answers.write_text(
        """
        organization: event_timeline_network
        preserve: appendix, chronology
        skip: index
        references:
        External review says the chronology is central context, not a throwaway appendix.
        """,
        encoding="utf-8",
    )

    paths = apply_user_review(run_dir, answers)
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))
    review = json.loads(paths["review"].read_text(encoding="utf-8"))
    roles = {chapter["title"]: chapter["role"] for chapter in plan["final_plan"]["chapter_roles"]}
    md = paths["markdown"].read_text(encoding="utf-8")

    assert review["network_override"] == "event_timeline_network"
    assert plan["final_plan"]["primary_network_model"] == "event_timeline_network"
    assert roles["Appendix: Chronology"] == "preserve"
    assert roles["Index"] == "skip"
    assert (run_dir / "knowledge" / "reference-prior.json").exists()
    assert "User Review" in md
