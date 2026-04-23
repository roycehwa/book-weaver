from pathlib import Path

from pdf_translator.newspaper_rebuild import (
    rebuild_article_body,
    rebuild_articles_payload,
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


def test_rebuild_article_body_keeps_intro_when_continuation_split() -> None:
    body_text = "\n\n".join(
        [
            "Migrants deported by U.S. find hope in mountaintop sanctuary in Costa Rica as families adapt in exile. " * 3,
            "The house looked nothing like the modern beauty salon that Vusala Yusifova once owned in Azerbaijan. " * 3,
            "In Monteverde, residents and Quakers raised money and helped families settle into new homes. " * 3,
            "LEFT IN LIMBO",
            "When they arrived in Costa Rica, deportees were crowded into barracks with little access to care. " * 3,
            "Most returned to their home countries, while others sought asylum and remained stranded. " * 3,
        ]
    )

    rebuilt_text, metadata = rebuild_article_body(body_text)

    assert rebuilt_text.startswith("Migrants deported by U.S. find hope")
    assert "When they arrived in Costa Rica" in rebuilt_text
    assert metadata["kept_paragraphs"] >= 4


def test_rebuild_article_body_drops_caption_noise_and_credit_prefix() -> None:
    body_text = "\n\n".join(
        [
            "Normal intro paragraph about policy and people that should stay in output. " * 4,
            "SIMBARASHE CHA/THE NEW YORK TIMES she wrote in an email that the event needed energy.",
            "Clockwise from above left: a collage of images and credits across the page layout.",
            "— of joy as a kind of mandate that should not remain as a dash-fragment.",
            "Another narrative paragraph that should remain for continuity. " * 4,
        ]
    )

    rebuilt_text, _ = rebuild_article_body(body_text)

    assert "SIMBARASHE CHA/THE NEW YORK TIMES" not in rebuilt_text
    assert "Clockwise from above left" not in rebuilt_text
    assert "\n\n— of joy" not in rebuilt_text
    assert "of joy as a kind of mandate" in rebuilt_text
    assert "Another narrative paragraph" in rebuilt_text


def test_rebuild_article_body_drops_contextless_lowercase_fragment() -> None:
    body_text = "\n\n".join(
        [
            "Complete narrative paragraph that ends cleanly and establishes the main scene for the report. " * 3,
            "was given a choice: Go to prison or go fight in the war in Ukraine.",
            "Another complete paragraph follows and should remain because it continues the main story clearly. " * 3,
        ]
    )

    rebuilt_text, _ = rebuild_article_body(body_text)

    assert "Complete narrative paragraph" in rebuilt_text
    assert "Another complete paragraph follows" in rebuilt_text
    assert "was given a choice" not in rebuilt_text


def test_rebuild_article_body_drops_short_unfinished_tail() -> None:
    body_text = "\n\n".join(
        [
            "A complete paragraph introduces the scene and ends with a proper sentence. " * 3,
            'Ms. Wintour said the gala required a high-octane chair. "Lauren is a force,"',
            "A later complete paragraph closes the section with context and should remain. " * 3,
        ]
    )

    rebuilt_text, _ = rebuild_article_body(body_text)

    assert 'Lauren is a force,' not in rebuilt_text
    assert "A later complete paragraph closes the section" in rebuilt_text


def test_rebuild_article_body_keeps_short_heading_like_line() -> None:
    body_text = "\n\n".join(
        [
            "Migrants deported by U.S. find hope in mountaintop sanctuary in Costa Rica",
            "The house looked nothing like the modern beauty salon that Vusala Yusifova once owned in Azerbaijan. " * 3,
            "Another complete paragraph closes the section with context and should remain. " * 3,
        ]
    )

    rebuilt_text, _ = rebuild_article_body(body_text)

    assert rebuilt_text.startswith("Migrants deported by U.S. find hope")


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


def test_rebuild_articles_payload_merges_related_continuation_fragment() -> None:
    payload = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_article_indexes": [1],
        "selected_top_half_count": 1,
        "articles": [
            {
                "headline": "Other story",
                "page_start": 5,
                "quality": {"grade": "medium", "score": 70},
                "score": 11.0,
                "body_text": "Other story text. " * 40,
            },
            {
                "headline": "Unimaginably rich and unapologetically happy",
                "page_start": 2,
                "quality": {"grade": "high", "score": 90},
                "score": 78.0,
                "deck": "— of joy as a kind of mandate.",
                "body_text": "— of joy as a kind of mandate. " * 40 + "\n\n" + "A LONGTIME NETWORKER " * 20,
            },
            {
                "headline": "Unimaginably rich and unabashedly happy",
                "page_start": 1,
                "quality": {"grade": "medium", "score": 60},
                "score": 50.0,
                "deck": "A lot of things make Lauren Sánchez Bezos ridiculously happy.",
                "body_text": "A lot of things make Lauren Sánchez Bezos ridiculously happy. " * 40,
            },
        ],
    }

    rebuilt = rebuild_articles_payload(payload)
    selected_article = rebuilt["articles"][1]

    assert "A lot of things make Lauren Sánchez Bezos ridiculously happy." in selected_article["body_text"]
    assert "A LONGTIME NETWORKER" in selected_article["body_text"]
    assert selected_article["merged_fragment_indexes"] == [2]
    assert selected_article["deck"] == "A lot of things make Lauren Sánchez Bezos ridiculously happy."


def test_rebuild_articles_payload_merges_lower_score_selected_fragment() -> None:
    payload = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_article_indexes": [0, 1],
        "selected_top_half_count": 2,
        "articles": [
            {
                "headline": "Unimaginably rich and unapologetically happy",
                "page_start": 2,
                "quality": {"grade": "high", "score": 90},
                "score": 78.0,
                "deck": "— of joy as a kind of mandate.",
                "body_text": "— of joy as a kind of mandate. " * 35 + "\n\n" + "A LONGTIME NETWORKER " * 20,
            },
            {
                "headline": "Unimaginably rich and unabashedly happy",
                "page_start": 1,
                "quality": {"grade": "medium", "score": 60},
                "score": 50.0,
                "deck": "A lot of things make Lauren Sánchez Bezos ridiculously happy.",
                "body_text": "A lot of things make Lauren Sánchez Bezos ridiculously happy. " * 35,
            },
        ],
    }

    rebuilt = rebuild_articles_payload(payload)
    lead_article = rebuilt["articles"][0]

    assert "A lot of things make Lauren Sánchez Bezos ridiculously happy." in lead_article["body_text"]
    assert "A LONGTIME NETWORKER" in lead_article["body_text"]
    assert lead_article["merged_fragment_indexes"] == [1]
    assert lead_article["deck"] == "A lot of things make Lauren Sánchez Bezos ridiculously happy."


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
