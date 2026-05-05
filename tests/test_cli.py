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
