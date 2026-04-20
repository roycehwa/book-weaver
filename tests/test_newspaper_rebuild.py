from pathlib import Path

from pdf_translator.newspaper_rebuild import (
    rebuild_article_body,
    render_rebuilt_reading_markdown,
    write_rebuilt_outputs,
)


def test_rebuild_article_body_trims_listing_tail() -> None:
    body_text = "\n\n".join(
        [
            "The coalition talks continued through the night as negotiators tried to bridge the budget gap. "
            * 6,
            "Lawmakers said they expected a final vote this week after private meetings with party leaders. "
            * 5,
            "W L 26 39 82 z-Vegas .......................",
            "Miami at Real Salt Lake, 9:30. Colorado at Los Angeles FC, 10:30.",
        ]
    )

    rebuilt_text, metadata = rebuild_article_body(body_text)

    assert "coalition talks continued" in rebuilt_text
    assert "W L 26 39 82" not in rebuilt_text
    assert "Real Salt Lake" not in rebuilt_text
    assert metadata["raw_paragraphs"] == 4
    assert metadata["kept_paragraphs"] <= 2


def test_rebuild_article_body_keeps_narrative_story() -> None:
    body_text = "\n\n".join(
        [
            "A federal judge questioned both parties during a hearing on emergency immigration powers. "
            * 6,
            "Attorneys for the administration argued that the order followed established precedent and national "
            "security guidance. " * 5,
        ]
    )

    rebuilt_text, metadata = rebuild_article_body(body_text)

    assert "federal judge questioned both parties" in rebuilt_text
    assert "Attorneys for the administration argued" in rebuilt_text
    assert metadata["raw_paragraphs"] == 2
    assert metadata["kept_paragraphs"] == 2


def test_render_rebuilt_markdown_uses_selected_indexes() -> None:
    payload = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_top_half_count": 1,
        "selected_article_indexes": [1],
        "articles": [
            {
                "headline": "Listing-heavy item",
                "page_start": 30,
                "quality": {"grade": "medium", "score": 70},
                "score": 9.0,
                "body_text": "W L 26 39 82 z-Vegas .......................\n\nMiami at Real Salt Lake, 9:30",
            },
            {
                "headline": "Main policy article",
                "page_start": 1,
                "quality": {"grade": "high", "score": 92},
                "score": 45.0,
                "body_text": "Policy paragraph one. " * 30 + "\n\n" + "Policy paragraph two. " * 25,
            },
        ],
    }

    markdown_text, summary = render_rebuilt_reading_markdown(payload)

    assert "Main policy article" in markdown_text
    assert "Listing-heavy item" not in markdown_text
    assert summary["included_articles"] == 1


def test_write_rebuilt_outputs_writes_markdown_and_json(tmp_path: Path) -> None:
    payload = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_top_half_count": 1,
        "selected_article_indexes": [0],
        "articles": [
            {
                "headline": "Story headline",
                "page_start": 2,
                "quality": {"grade": "high", "score": 90},
                "score": 33.1,
                "body_text": "First paragraph. " * 20 + "\n\n" + "Second paragraph. " * 20,
            }
        ],
    }

    markdown_path = tmp_path / "articles.rebuilt.md"
    json_path = tmp_path / "articles.rebuilt.json"
    result = write_rebuilt_outputs(
        payload,
        output_markdown_path=markdown_path,
        output_json_path=json_path,
    )

    assert markdown_path.exists()
    assert json_path.exists()
    assert result["summary"]["included_articles"] == 1
