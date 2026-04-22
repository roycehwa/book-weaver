from pdf_translator.newspaper_illustrate import (
    match_pictures_to_articles,
    render_illustrated_reading_markdown,
)


def _prov(page_no: int, left: float, top: float, right: float, bottom: float) -> list[dict]:
    return [{"page_no": page_no, "bbox": {"l": left, "t": top, "r": right, "b": bottom}}]


def test_match_pictures_to_articles_assigns_same_page_picture() -> None:
    payload = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_article_indexes": [0],
        "selected_top_half_count": 1,
        "articles": [
            {
                "headline": "Main story",
                "page_start": 1,
                "block_indexes": [0, 1],
                "body_text": "Main body paragraph. " * 30,
            },
            {
                "headline": "Side story",
                "page_start": 1,
                "block_indexes": [2],
                "body_text": "Side body paragraph. " * 10,
            },
        ],
    }
    structured = {
        "body": {"children": [{"$ref": f"#/texts/{index}"} for index in range(3)]},
        "texts": [
            {"label": "section_header", "text": "Main story", "prov": _prov(1, 100, 900, 500, 840)},
            {"label": "text", "text": "Main story paragraph. " * 30, "prov": _prov(1, 100, 820, 500, 500)},
            {"label": "section_header", "text": "Side story", "prov": _prov(1, 560, 900, 900, 840)},
            {"label": "text", "text": "Photo: Candidate at a campaign stop.", "prov": _prov(1, 130, 540, 460, 500)},
        ],
        "pictures": [
            {
                "prov": _prov(1, 120, 760, 470, 540),
                "captions": [{"$ref": "#/texts/3"}],
            }
        ],
    }

    matched = match_pictures_to_articles(payload, structured, max_images_per_article=1)

    main_images = matched["articles"][0]["illustration_images"]
    side_images = matched["articles"][1]["illustration_images"]
    assert len(main_images) == 1
    assert side_images == []
    assert main_images[0]["picture_index"] == 0
    assert main_images[0]["caption"] == "Photo: Candidate at a campaign stop."
    assert main_images[0]["score_gap"] is not None


def test_match_pictures_to_articles_drops_ambiguous_picture() -> None:
    payload = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_article_indexes": [0, 1],
        "selected_top_half_count": 2,
        "articles": [
            {
                "headline": "Left story",
                "page_start": 1,
                "block_indexes": [0, 1],
                "body_text": "Left body paragraph. " * 20,
            },
            {
                "headline": "Right story",
                "page_start": 1,
                "block_indexes": [2, 3],
                "body_text": "Right body paragraph. " * 20,
            },
        ],
    }
    structured = {
        "body": {"children": [{"$ref": f"#/texts/{index}"} for index in range(4)]},
        "texts": [
            {"label": "section_header", "text": "Left story", "prov": _prov(1, 100, 900, 450, 840)},
            {"label": "text", "text": "Left body paragraph. " * 30, "prov": _prov(1, 100, 820, 450, 500)},
            {"label": "section_header", "text": "Right story", "prov": _prov(1, 520, 900, 870, 840)},
            {"label": "text", "text": "Right body paragraph. " * 30, "prov": _prov(1, 520, 820, 870, 500)},
        ],
        "pictures": [
            {
                "prov": _prov(1, 430, 760, 540, 620),
            }
        ],
    }

    matched = match_pictures_to_articles(payload, structured)

    assert matched["articles"][0]["illustration_images"] == []
    assert matched["articles"][1]["illustration_images"] == []


def test_render_illustrated_reading_markdown_includes_image_and_rebuilt_body() -> None:
    payload = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_article_indexes": [0],
        "selected_top_half_count": 1,
        "articles": [
            {
                "headline": "Lead story",
                "page_start": 3,
                "quality": {"grade": "high", "score": 91.2},
                "score": 44.6,
                "rebuilt_body_text": "Paragraph one.\n\nParagraph two.",
                "illustration_images": [
                    {
                        "path": "/tmp/article image (1).png",
                        "caption": "Lead image caption [raw] (draft) " * 8,
                    }
                ],
            }
        ],
    }

    markdown_text, summary = render_illustrated_reading_markdown(payload, selected_only=True)
    image_line = next(line for line in markdown_text.splitlines() if line.startswith("!["))

    assert "Lead story" in markdown_text
    assert "Paragraph one." in markdown_text
    assert "](<" in image_line
    assert "/tmp/article image (1).png" in image_line
    assert "[raw]" not in image_line
    assert "(draft)" not in image_line
    assert len(image_line) < 140
    assert summary["included_articles"] == 1
    assert summary["included_images"] == 1
