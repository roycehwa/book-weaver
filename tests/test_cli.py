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


def test_review_export_ignores_skipped_pages_when_checking_coverage() -> None:
    book = {
        "pages": [
            {"page_no": 1, "has_content": True},
            {
                "page_no": 2,
                "has_content": True,
                "disposition": "skipped",
                "skip_reason": "page_render_fallback_excluded_from:Chapter 2",
            },
        ],
        "chapters": [{"source_pages": [1]}],
    }

    assert _uncovered_book_pages(book) == []


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


def test_review_export_rejects_book_without_current_contract(tmp_path: Path) -> None:
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
    with pytest.raises(ValueError, match="current semantic content is required"):
        _load_complete_review_book(run_dir, manifest)


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
                "semantic_content": {"schema": "semantic_content_v1", "footnotes": []},
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
    (run_dir / "integrity-ledger.json").write_text(
        json.dumps(
            {
                "technical_ready": True,
                "approved_ready": False,
                "ready": False,
                "failures": {"unresolved_review": ["stale-review-item"]},
            }
        ),
        encoding="utf-8",
    )
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


def test_render_review_export_invokes_quality_gate_before_publish(tmp_path: Path, monkeypatch) -> None:
    from pdf_translator.cli import _render_review_export
    from tests.synthetic_quality_fixtures import write_minimal_translation_quality_artifacts, write_synthetic_reading_units

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF")
    write_synthetic_reading_units(run_dir)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_pdf": str(source_pdf),
                "text_operation": "translate",
                "translation": {"mode": "translated"},
            }
        ),
        encoding="utf-8",
    )
    source_markdown = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    for filename in ("translated.raw.md", "translated.cleaned.md", "translated.md"):
        (run_dir / filename).write_text(source_markdown, encoding="utf-8")
    write_minimal_translation_quality_artifacts(run_dir)
    order: list[str] = []

    monkeypatch.setattr(
        "pdf_translator.translation_quality.assert_translation_quality_current",
        lambda _run_dir: order.append("quality"),
    )
    monkeypatch.setattr("pdf_translator.source_workspace.require_current_translation", lambda _run_dir: None)
    monkeypatch.setattr(
        cli_module,
        "review_project_from_run",
        lambda _run_dir: (_ for _ in ()).throw(AssertionError("stop-after-quality-gate")),
    )

    with pytest.raises(AssertionError, match="stop-after-quality-gate"):
        _render_review_export(
            run_dir=run_dir,
            version_name="draft",
            parent_version=None,
            target_language="zh-CN",
            output_format="epub",
            approve=False,
            output_dir=run_dir / "versions" / "draft",
        )
    assert order == ["quality"]


def test_render_review_export_draft_skips_approved_review_validation(tmp_path: Path, monkeypatch) -> None:
    from pdf_translator.cli import _render_review_export
    from tests.synthetic_quality_fixtures import write_minimal_translation_quality_artifacts, write_synthetic_reading_units

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_pdf": str(tmp_path / "source.pdf"),
                "text_operation": "translate",
                "translation": {"mode": "translated"},
                "files": {},
            }
        ),
        encoding="utf-8",
    )
    source_markdown = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    for filename in ("translated.raw.md", "translated.cleaned.md", "translated.md"):
        (run_dir / filename).write_text(source_markdown, encoding="utf-8")
    write_minimal_translation_quality_artifacts(run_dir)
    index = json.loads((run_dir / "translation-quality-index.json").read_text(encoding="utf-8"))
    index["blocking_count"] = 0
    index["review_count"] = 2
    index["aggregate_status"] = "review"
    index["acceptable"] = True
    (run_dir / "translation-quality-index.json").write_text(json.dumps(index), encoding="utf-8")

    monkeypatch.setattr("pdf_translator.source_workspace.require_current_translation", lambda _run_dir: None)
    monkeypatch.setattr(
        cli_module,
        "review_project_from_run",
        lambda _run_dir: {
            "segments": [{"segment_id": "ch-001:r001", "source_text": "Body.", "translate": True}],
            "translated_segments": [{"segment_id": "ch-001:r001", "translated_text": "正文。", "translate": True}],
            "review_items": [{"segment_id": "ch-001:r001", "status": "open"}],
            "review_state": {"decisions": {"ch-001:r001": {"status": "resolved"}}},
        },
    )
    monkeypatch.setattr(
        cli_module,
        "_validate_approved_review_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("approved-only")),
    )
    monkeypatch.setattr(
        cli_module,
        "_load_complete_review_book",
        lambda _run_dir, _manifest: {
            "chapters": [{"chapter_id": "ch-001", "title": "Chapter", "markdown": "正文。", "toc": True}]
        },
    )
    monkeypatch.setattr(cli_module, "_review_image_roots", lambda _run_dir, _manifest: [])
    monkeypatch.setattr(
        cli_module,
        "write_versioned_outputs",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("stop-before-render")),
    )

    with pytest.raises(AssertionError, match="stop-before-render"):
        _render_review_export(
            run_dir=run_dir,
            version_name="draft",
            parent_version=None,
            target_language="zh-CN",
            output_format="none",
            approve=False,
            output_dir=run_dir / "versions" / "draft",
        )


def test_render_review_export_publishes_after_segment_quality_finding_is_adjudicated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from pdf_translator.cli import _render_review_export
    from pdf_translator.translation_quality import (
        translation_quality_summary,
        write_translation_quality_bundle,
    )
    from tests.synthetic_quality_fixtures import write_synthetic_reading_units

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source_markdown = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    for filename in ("translated.raw.md", "translated.cleaned.md", "translated.md"):
        (run_dir / filename).write_text(source_markdown, encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_pdf": str(tmp_path / "synthetic-book.pdf"),
                "text_operation": "translate",
                "translation": {"mode": "translated"},
                "files": {},
            }
        ),
        encoding="utf-8",
    )
    source_segment = {
        "segment_id": "seg-1",
        "chapter_id": "ch-001",
        "chapter_index": 1,
        "chapter_title": "Chapter 1",
        "block_index": 1,
        "source_text": "Synthetic source paragraph.",
        "translate": True,
    }
    (run_dir / "segments.json").write_text(
        json.dumps({"segments": [source_segment]}),
        encoding="utf-8",
    )
    (run_dir / "translated_segments.json").write_text(
        json.dumps({"segments": [{**source_segment, "translated_text": ""}]}),
        encoding="utf-8",
    )
    review_items = [
        {
            "item_id": "review-0001",
            "segment_id": "seg-1",
            "issue_type": "missing_translation",
            "severity": "high",
            "status": "approved",
            "evidence": {},
        }
    ]
    (run_dir / "review_items.json").write_text(
        json.dumps({"items": review_items}),
        encoding="utf-8",
    )
    (run_dir / "review_state.json").write_text(
        json.dumps(
            {
                "decisions": {
                    "seg-1": {
                        "status": "approved",
                        "action": "manual_edit",
                        "approved_text": "人工补齐的完整译文。",
                    }
                },
                "summary": {"total_items": 1, "open_items": 0, "approved_items": 1},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "pre_review.json").write_text(
        json.dumps({"status": "completed"}),
        encoding="utf-8",
    )
    write_translation_quality_bundle(
        run_dir,
        text_operation="translate",
        source_markdown=source_markdown,
        review_items=review_items,
    )
    assert translation_quality_summary(run_dir)["translation_quality_blocking"] is False

    monkeypatch.setattr("pdf_translator.source_workspace.require_current_translation", lambda _run_dir: None)
    monkeypatch.setattr(
        cli_module,
        "_load_complete_review_book",
        lambda _run_dir, _manifest: {
            "chapters": [
                {
                    "chapter_id": "ch-001",
                    "title": "Chapter 1",
                    "markdown": "Synthetic source paragraph.\n",
                    "source_pages": [],
                    "translate": True,
                    "toc": True,
                }
            ]
        },
    )
    monkeypatch.setattr(cli_module, "_review_image_roots", lambda _run_dir, _manifest: [])

    result = _render_review_export(
        run_dir=run_dir,
        version_name="adjudicated",
        parent_version=None,
        target_language="zh-CN",
        output_format="none",
        approve=False,
        output_dir=run_dir / "versions" / "adjudicated",
    )

    delivered = Path(result["version"]["translated_markdown_path"]).read_text(encoding="utf-8")
    assert "人工补齐的完整译文。" in delivered


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
