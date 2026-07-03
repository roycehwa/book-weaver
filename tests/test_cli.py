from pathlib import Path
import json

import pytest

import pdf_translator.cli as cli_module
from pdf_translator.cli import (
    _load_complete_review_book,
    _review_image_roots,
    _uncovered_book_pages,
    _validate_approved_review_project,
    build_parser,
)


def test_public_cli_book_and_magazine_profiles_only() -> None:
    parser = build_parser()
    help_text = parser.format_help()

    assert parser.prog == "book-weaver"
    assert "articles-html" not in help_text
    assert "newspaper-batch" not in help_text

    with pytest.raises(SystemExit):
        parser.parse_args(["profile", "sample.pdf", "--profile", "newspaper"])

    with pytest.raises(SystemExit):
        parser.parse_args(["translate", "sample.pdf", "--target-lang", "zh-CN", "--profile", "newspaper"])

    with pytest.raises(SystemExit):
        parser.parse_args(["articles-html", "sample.pdf"])


def test_public_cli_accepts_intake_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "intake",
            "sample.pdf",
            "--source-lang",
            "zh-CN",
            "--profile",
            "book",
            "--max-chunk-chars",
            "7000",
        ]
    )

    assert args.command == "intake"
    assert str(args.source_pdf) == "sample.pdf"
    assert args.source_lang == "zh-CN"
    assert args.profile == "book"
    assert args.max_chunk_chars == 7000


def test_review_export_discovers_manifest_and_run_image_roots(tmp_path: Path) -> None:
    manifest_images = tmp_path / "external-images"
    manifest_images.mkdir()
    (tmp_path / "book-images").mkdir()
    (tmp_path / "images").mkdir()

    roots = _review_image_roots(
        tmp_path,
        {"files": {"images_dir": str(manifest_images)}},
    )

    assert roots == [
        manifest_images.resolve(),
        (tmp_path / "book-images").resolve(),
        (tmp_path / "images").resolve(),
    ]


def test_review_export_detects_pages_missing_from_book_chapters() -> None:
    book = {
        "pages": [
            {"page_no": 1, "page_kind": "body", "has_content": True},
            {"page_no": 2, "page_kind": "notes_heavy", "has_content": True},
        ],
        "chapters": [{"source_pages": [1]}],
    }

    assert _uncovered_book_pages(book) == [2]


def test_approved_review_export_rejects_missing_translation() -> None:
    project = {
        "segments": [
            {
                "segment_id": "ch-001:r001",
                "source_text": "Substantive source text.",
                "translate": True,
            }
        ],
        "translated_segments": [
            {
                "segment_id": "ch-001:r001",
                "translated_text": "",
                "translate": True,
            }
        ],
        "review_items": [],
        "review_state": {"decisions": {}},
    }

    with pytest.raises(ValueError, match="missing translated content"):
        _validate_approved_review_project(project)


def test_approved_review_export_rejects_open_review_items() -> None:
    project = {
        "segments": [
            {
                "segment_id": "ch-001:r001",
                "source_text": "Source text.",
                "translate": True,
            }
        ],
        "translated_segments": [
            {
                "segment_id": "ch-001:r001",
                "translated_text": "译文。",
                "translate": True,
            }
        ],
        "review_items": [{"segment_id": "ch-001:r001", "status": "open"}],
        "review_state": {"decisions": {}},
    }

    with pytest.raises(ValueError, match="unresolved review items"):
        _validate_approved_review_project(project)


def test_approved_review_export_rejects_integrity_ledger_failure() -> None:
    project = {
        "translated_segments": [],
        "review_state": {},
        "review_items": [],
    }
    ledger = {
        "ready": False,
        "failures": {
            "absolute_paths": ["OEBPS/chapters/001.xhtml"],
        },
    }

    with pytest.raises(ValueError, match="absolute_paths"):
        _validate_approved_review_project(project, integrity_ledger=ledger)


def test_review_export_migrates_legacy_book_before_delivery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest = {"source_pdf": str(tmp_path / "source.pdf")}
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "pages": [
                    {"page_no": 1, "has_content": True},
                    {"page_no": 2, "has_content": True},
                ],
                "chapters": [{"chapter_id": "body", "source_pages": [1]}],
            }
        ),
        encoding="utf-8",
    )
    migrated: list[Path] = []

    def fake_migrate(path: Path):
        migrated.append(path)
        (path / "book.json").write_text(
            json.dumps(
                {
                    "pages": [
                        {"page_no": 1, "has_content": True},
                        {"page_no": 2, "has_content": True},
                    ],
                    "chapters": [
                        {"chapter_id": "body", "source_pages": [1]},
                        {
                            "chapter_id": "resource",
                            "source_pages": [2],
                            "resource_only": True,
                            "preserve_original": True,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return {"migrated": True}

    monkeypatch.setattr(cli_module, "migrate_legacy_review_run", fake_migrate)

    book = _load_complete_review_book(run_dir, manifest)

    assert migrated == [run_dir.resolve()]
    assert _uncovered_book_pages(book) == []


def test_review_export_restores_chapter_notes_before_delivery(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_pdf": str(source_pdf),
                "files": {"translated_chapters": str(run_dir / "translated-chapters.json")},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "pages": [{"page_no": 1, "has_content": True}],
                "chapters": [{"chapter_id": "ch-001", "source_pages": [1]}],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "segments.json").write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "segment_id": "ch-001:r001",
                        "chapter_id": "ch-001",
                        "chapter_index": 1,
                        "chapter_title": "Chapter",
                        "block_index": 1,
                        "source_text": "Body",
                        "translate": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "translated_segments.json").write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "segment_id": "ch-001:r001",
                        "chapter_id": "ch-001",
                        "chapter_index": 1,
                        "chapter_title": "Chapter",
                        "block_index": 1,
                        "translated_text": "审阅正文",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "review_items.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    (run_dir / "review_state.json").write_text(json.dumps({"decisions": {}, "summary": {}}), encoding="utf-8")
    (run_dir / "pre_review.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    (run_dir / "translated-chapters.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "chapter_id": "ch-001",
                        "title": "Chapter",
                        "markdown": "# Chapter\n\nBase body.\n\n### Notes\n\n- [**1.**](OPS/c01.xhtml#R_c01-note-0001) Preserved note.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cli_module, "render_epub_from_book", lambda **kwargs: kwargs["output_path"].write_text("epub", encoding="utf-8"))
    monkeypatch.setattr(cli_module, "validate_epub_internal_hrefs", lambda _path: {"resolved_ratio": 1.0})
    monkeypatch.setattr(cli_module, "render_pdf_from_markdown", lambda **kwargs: kwargs["output_path"].write_text("pdf", encoding="utf-8"))

    result = cli_module._run_review_export(
        run_dir=run_dir,
        version_name="v-notes",
        parent_version=None,
        target_language="zh-CN",
        output_format="epub",
        approve=True,
    )

    translated = Path(result["version"]["translated_markdown_path"]).read_text(encoding="utf-8")
    assert "审阅正文" in translated
    assert "Preserved note" in translated


def test_public_cli_accepts_polish_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "polish",
            "runs/sample",
            "--target-lang",
            "zh-CN",
            "--translator",
            "minimax",
            "--request-timeout-seconds",
            "600",
        ]
    )

    assert args.command == "polish"
    assert str(args.run_dir) == "runs/sample"
    assert args.request_timeout_seconds == 600


def test_public_cli_accepts_finalize_and_cleanup_commands() -> None:
    parser = build_parser()
    finalize_args = parser.parse_args(["finalize", "runs/sample"])
    cleanup_args = parser.parse_args(["cleanup", "runs/sample", "--dry-run", "--keep-caches"])

    assert finalize_args.command == "finalize"
    assert str(finalize_args.run_dir) == "runs/sample"
    assert cleanup_args.command == "cleanup"
    assert cleanup_args.dry_run is True
    assert cleanup_args.keep_caches is True


def test_public_cli_accepts_translation_review_commands() -> None:
    parser = build_parser()
    status_args = parser.parse_args(["review", "status", "runs/sample"])
    rewrite_args = parser.parse_args(
        [
            "review",
            "rewrite",
            "runs/sample",
            "--target-lang",
            "zh-CN",
            "--translator",
            "mock",
            "--segment-id",
            "ch-001:c001",
        ]
    )
    export_args = parser.parse_args(
        [
            "review",
            "export",
            "runs/sample",
            "--version",
            "review-v2",
            "--format",
            "epub",
            "--approve",
        ]
    )

    assert status_args.command == "review"
    assert status_args.review_command == "status"
    assert rewrite_args.review_command == "rewrite"
    assert rewrite_args.segment_id == "ch-001:c001"
    assert export_args.review_command == "export"
    assert export_args.version == "review-v2"
    assert export_args.approve is True


def test_public_cli_accepts_knowledge_build_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["knowledge", "build", "runs/sample"])

    assert args.command == "knowledge"
    assert args.knowledge_command == "build"
    assert str(args.run_dir) == "runs/sample"


def test_public_cli_accepts_knowledge_suitability_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["knowledge", "suitability", "runs/sample"])

    assert args.command == "knowledge"
    assert args.knowledge_command == "suitability"
    assert str(args.run_dir) == "runs/sample"


def test_public_cli_accepts_knowledge_plan_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["knowledge", "plan", "runs/sample"])

    assert args.command == "knowledge"
    assert args.knowledge_command == "plan"
    assert str(args.run_dir) == "runs/sample"
    assert args.planner == "rule"
    assert args.metadata_prior == "none"


def test_public_cli_accepts_knowledge_metadata_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["knowledge", "metadata", "runs/sample", "--refresh"])

    assert args.command == "knowledge"
    assert args.knowledge_command == "metadata"
    assert str(args.run_dir) == "runs/sample"
    assert args.refresh is True


def test_public_cli_accepts_knowledge_review_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["knowledge", "review", "runs/sample", "--answers", "answers.txt"])

    assert args.command == "knowledge"
    assert args.knowledge_command == "review"
    assert str(args.run_dir) == "runs/sample"
    assert str(args.answers) == "answers.txt"


def test_public_cli_accepts_knowledge_brief_and_feedback_commands() -> None:
    parser = build_parser()
    brief_args = parser.parse_args(["knowledge", "brief", "runs/sample"])
    feedback_args = parser.parse_args(["knowledge", "feedback", "runs/sample", "--input", "feedback.md"])

    assert brief_args.command == "knowledge"
    assert brief_args.knowledge_command == "brief"
    assert str(brief_args.run_dir) == "runs/sample"
    assert feedback_args.command == "knowledge"
    assert feedback_args.knowledge_command == "feedback"
    assert str(feedback_args.run_dir) == "runs/sample"
    assert str(feedback_args.input) == "feedback.md"


def test_public_cli_accepts_knowledge_extract_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["knowledge", "extract", "runs/sample", "--network-model", "argument_network"])

    assert args.command == "knowledge"
    assert args.knowledge_command == "extract"
    assert str(args.run_dir) == "runs/sample"
    assert args.network_model == "argument_network"


def test_public_cli_accepts_translate_resume_and_ignore_cache() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "translate",
            "sample.epub",
            "--target-lang",
            "zh-CN",
            "--resume",
            "--ignore-cache",
        ]
    )

    assert args.command == "translate"
    assert args.resume is True
    assert args.ignore_cache is True


def test_public_cli_accepts_job_progress_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["job", "progress", "runs/sample"])

    assert args.command == "job"
    assert args.job_command == "progress"
    assert str(args.run_dir) == "runs/sample"


def test_public_cli_accepts_job_events_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["job", "events", "runs/sample", "--limit", "5"])

    assert args.command == "job"
    assert args.job_command == "events"
    assert str(args.run_dir) == "runs/sample"
    assert args.limit == 5


def test_public_cli_accepts_glossary_commands() -> None:
    parser = build_parser()
    extract = parser.parse_args(["glossary", "extract", "runs/book"])
    apply_args = parser.parse_args(
        ["glossary", "apply", "runs/book", "--source", "Yellow Emperor", "--target", "黄帝", "--type", "cultural_term"]
    )
    status = parser.parse_args(["glossary", "status", "runs/book"])

    assert extract.command == "glossary"
    assert extract.glossary_command == "extract"
    assert apply_args.source == "Yellow Emperor"
    assert status.glossary_command == "status"
