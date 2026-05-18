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
