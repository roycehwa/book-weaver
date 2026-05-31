import pytest

from pdf_translator.cli import build_parser


def test_public_cli_book_and_magazine_profiles_only() -> None:
    parser = build_parser()
    help_text = parser.format_help()

    assert "articles-html" not in help_text
    assert "newspaper-batch" not in help_text

    with pytest.raises(SystemExit):
        parser.parse_args(["profile", "sample.pdf", "--profile", "newspaper"])

    with pytest.raises(SystemExit):
        parser.parse_args(["translate", "sample.pdf", "--target-lang", "zh-CN", "--profile", "newspaper"])

    with pytest.raises(SystemExit):
        parser.parse_args(["articles-html", "sample.pdf"])


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


def test_public_cli_accepts_agent_once_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "agent-once",
            "--source-root",
            "/tmp/books",
            "--translator",
            "mock",
            "--no-polish",
            "--source-lane",
            "CN",
        ]
    )

    assert args.command == "agent-once"
    assert str(args.source_root) == "/tmp/books"
    assert args.translator == "mock"
    assert args.no_polish is True
    assert args.source_lanes == ["CN"]


def test_public_cli_accepts_review_export_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "review-export",
            "runs/sample",
            "--version",
            "v2",
            "--target-lang",
            "zh-CN",
            "--format",
            "both",
            "--parent-version",
            "v1",
        ]
    )

    assert args.command == "review-export"
    assert str(args.run_dir) == "runs/sample"
    assert args.version == "v2"
    assert args.format == "both"
    assert args.parent_version == "v1"
