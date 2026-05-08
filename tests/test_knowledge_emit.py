from __future__ import annotations

from pathlib import Path
import json

from pdf_translator.knowledge import (
    build_knowledge_package,
    build_suitability_report,
    emit_mindmap_mermaid_from_book,
    emit_wiki_outline_from_book,
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
