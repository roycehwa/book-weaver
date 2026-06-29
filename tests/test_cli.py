import pytest

from pdf_translator.cli import build_parser


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
