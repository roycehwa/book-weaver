from pathlib import Path

from pdf_translator.newspaper import (
    extract_newspaper_articles,
    render_newspaper_reading_markdown,
    write_newspaper_reading_markdown,
)


def _prov(page_no: int, left: float, top: float, right: float, bottom: float) -> list[dict]:
    return [{"page_no": page_no, "bbox": {"l": left, "t": top, "r": right, "b": bottom}}]


def test_extract_newspaper_articles_finds_main_and_secondary_story(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (2000.0, 3200.0)}
        structured = {
            "body": {
                "children": [{"$ref": f"#/texts/{index}"} for index in range(8)]
            },
            "texts": [
                {"label": "section_header", "text": "Main front-page story", "prov": _prov(1, 50, 2900, 1100, 2800)},
                {"label": "text", "text": "Lead deck for the main story.", "prov": _prov(1, 60, 2700, 1000, 2630)},
                {"label": "text", "text": "Main story body paragraph one. " * 40, "prov": _prov(1, 60, 2500, 500, 2100)},
                {"label": "text", "text": "Main story body paragraph two. " * 40, "prov": _prov(1, 540, 2500, 1000, 2100)},
                {"label": "section_header", "text": "Secondary market story", "prov": _prov(1, 1100, 1700, 1600, 1620)},
                {"label": "text", "text": "Secondary story body paragraph. " * 35, "prov": _prov(1, 1100, 1500, 1600, 1100)},
                {"label": "section_header", "text": "Briefing", "prov": _prov(1, 1650, 2800, 1900, 2740)},
                {
                    "label": "list_item",
                    "text": "Rates hold steady in bond markets as policymakers signal caution.",
                    "prov": _prov(1, 1660, 2600, 1940, 2500),
                },
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    headlines = [article["headline"] for article in result["articles"]]
    assert "Main front-page story" in headlines
    assert "Secondary market story" in headlines
    assert any(article["article_type"] == "briefing_item" for article in result["articles"])
    assert result["selected_top_half_count"] >= 1


def test_extract_newspaper_articles_filters_banners_and_short_quotes(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (2000.0, 3200.0)}
        structured = {
            "body": {
                "children": [{"$ref": f"#/texts/{index}"} for index in range(7)]
            },
            "texts": [
                {"label": "section_header", "text": "COMPANIES & MARKETS", "prov": _prov(1, 900, 3150, 1300, 3100)},
                {"label": "text", "text": "Section rail copy." * 20, "prov": _prov(1, 920, 3000, 1260, 2400)},
                {
                    "label": "section_header",
                    "text": '"The card will be made of gold. It will be beautiful."',
                    "prov": _prov(1, 200, 1800, 600, 1650),
                },
                {"label": "text", "text": "Short pull quote body." * 10, "prov": _prov(1, 220, 1600, 620, 1350)},
                {"label": "section_header", "text": "Real market story develops", "prov": _prov(1, 700, 2300, 1500, 2200)},
                {"label": "text", "text": "Lead deck for the market story.", "prov": _prov(1, 720, 2150, 1480, 2080)},
                {"label": "text", "text": "Detailed market story body." * 60, "prov": _prov(1, 720, 2050, 1480, 900)},
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    headlines = [article["headline"] for article in result["articles"]]
    assert "COMPANIES & MARKETS" not in headlines
    assert '"The card will be made of gold. It will be beautiful."' not in headlines
    assert "Real market story develops" in headlines


def test_extract_newspaper_articles_ignores_pull_quote_as_headline_anchor(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (1200.0, 2000.0)}
        structured = {
            "body": {
                "children": [{"$ref": f"#/texts/{index}"} for index in range(8)]
            },
            "texts": [
                {"label": "section_header", "text": "Main migration story continues", "prov": _prov(1, 40, 1900, 920, 1820)},
                {"label": "text", "text": "Lead deck line.", "prov": _prov(1, 50, 1780, 880, 1720)},
                {"label": "text", "text": "Main body intro paragraph. " * 20, "prov": _prov(1, 50, 1660, 200, 1520)},
                {"label": "text", "text": "If I take you to Russia without docu-", "prov": _prov(1, 50, 1500, 200, 1460)},
                {"label": "section_header", "text": '"I\'ve been robbed. I did everything right."', "prov": _prov(1, 280, 1540, 420, 1490)},
                {"label": "text", "text": "ments, without money and without support, how would you react?", "prov": _prov(1, 280, 1480, 420, 1420)},
                {"label": "text", "text": "Mr. Smirnov said he had followed every instruction before the deportation.", "prov": _prov(1, 280, 1400, 420, 1320)},
                {"label": "text", "text": "The story closes with a final paragraph about the family's future. " * 14, "prov": _prov(1, 50, 1300, 200, 1180)},
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    headlines = [article["headline"] for article in result["articles"]]
    assert '"I\'ve been robbed. I did everything right."' not in headlines

    article = next(article for article in result["articles"] if article["headline"] == "Main migration story continues")
    assert "ments, without money and without support" in article["body_text"]
    assert "The story closes with a final paragraph" in article["body_text"]


def test_extract_newspaper_articles_uses_column_reading_order(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {
            1: (2000.0, 3200.0),
            2: (2000.0, 3200.0),
            3: (2000.0, 3200.0),
        }
        structured = {
            "body": {
                "children": [{"$ref": f"#/texts/{index}"} for index in range(5)]
            },
            "texts": [
                {"label": "section_header", "text": "Two-column story heading", "prov": _prov(3, 50, 2900, 900, 2800)},
                {"label": "text", "text": "Deck text for the article.", "prov": _prov(3, 60, 2700, 880, 2640)},
                {"label": "text", "text": "LEFT TOP " * 30, "prov": _prov(3, 60, 2500, 360, 2400)},
                {"label": "text", "text": "LEFT BOTTOM " * 30, "prov": _prov(3, 60, 2200, 360, 2100)},
                {"label": "text", "text": "RIGHT TOP " * 30, "prov": _prov(3, 420, 2450, 720, 2350)},
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    article = next(article for article in result["articles"] if article["headline"] == "Two-column story heading")
    body_parts = article["body_text"].split("\n\n")
    assert [part.strip() for part in body_parts] == [
        ("LEFT TOP " * 30).strip(),
        ("LEFT BOTTOM " * 30).strip(),
        ("RIGHT TOP " * 30).strip(),
    ]


def test_extract_newspaper_articles_keeps_adjacent_narrow_columns_separate(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (900.0, 1400.0)}
        structured = {
            "body": {
                "children": [{"$ref": f"#/texts/{index}"} for index in range(7)]
            },
            "texts": [
                {"label": "section_header", "text": "Narrow columns should keep order", "prov": _prov(1, 40, 1320, 760, 1260)},
                {"label": "text", "text": "Deck text for narrow-column article.", "prov": _prov(1, 40, 1240, 760, 1200)},
                {"label": "text", "text": "COL1 TOP " * 18, "prov": _prov(1, 40, 1160, 140, 1080)},
                {"label": "text", "text": "COL1 BOTTOM " * 18, "prov": _prov(1, 40, 1040, 140, 980)},
                {"label": "text", "text": "COL2 TOP " * 18, "prov": _prov(1, 170, 1150, 270, 1070)},
                {"label": "text", "text": "COL2 BOTTOM " * 18, "prov": _prov(1, 170, 1030, 270, 970)},
                {"label": "text", "text": "COL3 TOP " * 18, "prov": _prov(1, 300, 1140, 400, 1060)},
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    article = next(article for article in result["articles"] if article["headline"] == "Narrow columns should keep order")
    body_parts = [part.strip() for part in article["body_text"].split("\n\n")]
    assert body_parts == [
        ("COL1 TOP " * 18).strip(),
        ("COL1 BOTTOM " * 18).strip(),
        ("COL2 TOP " * 18).strip(),
        ("COL2 BOTTOM " * 18).strip(),
        ("COL3 TOP " * 18).strip(),
    ]


def test_extract_newspaper_articles_does_not_pull_briefing_into_main_story(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {
            1: (2200.0, 3200.0),
            2: (2200.0, 3200.0),
            3: (2200.0, 3200.0),
        }
        structured = {
            "body": {
                "children": [{"$ref": f"#/texts/{index}"} for index in range(7)]
            },
            "texts": [
                {"label": "section_header", "text": "Main story headline with breadth", "prov": _prov(3, 50, 2900, 1750, 2780)},
                {"label": "text", "text": "Wide deck text for the lead story.", "prov": _prov(3, 60, 2700, 1740, 2630)},
                {"label": "text", "text": "Lead body left. " * 30, "prov": _prov(3, 60, 2500, 360, 2400)},
                {"label": "text", "text": "Lead body right. " * 30, "prov": _prov(3, 420, 2500, 720, 2400)},
                {"label": "section_header", "text": "Briefing", "prov": _prov(3, 1820, 2880, 1910, 2840)},
                {
                    "label": "text",
                    "text": "Briefing item should stay separate and never enter the lead story.",
                    "prov": _prov(3, 1800, 2200, 2060, 2120),
                },
                {"label": "text", "text": "Lead body far right. " * 30, "prov": _prov(3, 1420, 2500, 1710, 2400)},
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    article = next(article for article in result["articles"] if article["headline"] == "Main story headline with breadth")
    assert "Briefing item should stay separate" not in article["body_text"]


def test_extract_newspaper_articles_keeps_far_right_continuation_for_wide_headline(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (2200.0, 3200.0)}
        structured = {
            "body": {
                    "children": [{"$ref": f"#/texts/{index}"} for index in range(7)]
                },
                "texts": [
                    {"label": "section_header", "text": "Wide international lead story", "prov": _prov(1, 50, 2920, 1680, 2800)},
                    {"label": "text", "text": "Lead deck line.", "prov": _prov(1, 60, 2720, 1200, 2640)},
                    {"label": "text", "text": "Lead story left column. " * 30, "prov": _prov(1, 60, 2520, 360, 2100)},
                    {"label": "text", "text": "Lead story middle column. " * 30, "prov": _prov(1, 420, 2520, 720, 2100)},
                    {"label": "text", "text": "Lead story far right continuation. " * 30, "prov": _prov(1, 1760, 2480, 2060, 2140)},
                    {"label": "section_header", "text": "Later separate story", "prov": _prov(1, 50, 1880, 1500, 1800)},
                    {"label": "text", "text": "Later far-right obituary continuation. " * 25, "prov": _prov(1, 1760, 1750, 2060, 1450)},
                ],
            }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    article = next(article for article in result["articles"] if article["headline"] == "Wide international lead story")
    assert "Lead story far right continuation." in article["body_text"]
    assert "Later separate story" not in article["body_text"]
    assert "Later far-right obituary continuation." not in article["body_text"]


def test_extract_newspaper_articles_extracts_front_matter_and_quality(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (2200.0, 3200.0), 2: (2200.0, 3200.0), 3: (2200.0, 3200.0)}
        structured = {
            "body": {
                "children": [{"$ref": f"#/texts/{index}"} for index in range(7)]
            },
            "texts": [
                {"label": "section_header", "text": "Lead political story expands", "prov": _prov(3, 50, 2920, 1750, 2790)},
                {"label": "section_header", "text": "Subheadline explains the policy split in detail", "prov": _prov(3, 50, 2760, 820, 2680)},
                {"label": "text", "text": "JANE DOE AND JOHN SMITH", "prov": _prov(3, 60, 2640, 420, 2600)},
                {"label": "text", "text": "LONDON - WESTMINSTER", "prov": _prov(3, 60, 2580, 340, 2540)},
                {"label": "text", "text": "Deck line for the article.", "prov": _prov(3, 60, 2520, 960, 2470)},
                {"label": "text", "text": "First body paragraph. " * 35, "prov": _prov(3, 60, 2400, 360, 2100)},
                {"label": "text", "text": "Second body paragraph. " * 35, "prov": _prov(3, 420, 2400, 720, 2100)},
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    article = next(article for article in result["articles"] if article["headline"] == "Lead political story expands")
    assert article["deck"] == "Subheadline explains the policy split in detail Deck line for the article."
    assert article["byline"] == "JANE DOE AND JOHN SMITH"
    assert article["dateline"] == "LONDON - WESTMINSTER"
    assert "JANE DOE AND JOHN SMITH" not in article["body_text"]
    assert "LONDON - WESTMINSTER" not in article["body_text"]
    assert article["quality"]["score"] > 0
    assert article["quality"]["grade"] in {"high", "medium", "low"}


def test_extract_newspaper_articles_filters_legal_notice_content(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (2200.0, 3200.0), 2: (2200.0, 3200.0), 3: (2200.0, 3200.0)}
        structured = {
            "body": {
                "children": [{"$ref": f"#/texts/{index}"} for index in range(7)]
            },
            "texts": [
                {"label": "section_header", "text": "Big political story develops", "prov": _prov(3, 50, 2920, 1500, 2790)},
                {"label": "text", "text": "Political deck line.", "prov": _prov(3, 60, 2700, 1200, 2640)},
                {"label": "text", "text": "Political body paragraph. " * 35, "prov": _prov(3, 60, 2500, 360, 2100)},
                {"label": "text", "text": "Second political body paragraph. " * 35, "prov": _prov(3, 420, 2500, 720, 2100)},
                {"label": "section_header", "text": "SUPREME COURT OF THE STATE OF NEW YORK COUNTY OF NEW YORK", "prov": _prov(3, 1450, 1800, 2100, 1700)},
                {
                    "label": "text",
                    "text": "PLEASE TAKE NOTICE that pursuant to a Final Judgment of Foreclosure and Sale the court-appointed Referee will sell at public auction.",
                    "prov": _prov(3, 1450, 1650, 2100, 1500),
                },
                {
                    "label": "text",
                    "text": "Index No. 850126/2022. Plaintiff and Defendants are notified that the Substitute Trustee will offer for sale the premises.",
                    "prov": _prov(3, 1450, 1480, 2100, 1340),
                },
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    headlines = [article["headline"] for article in result["articles"]]
    assert "Big political story develops" in headlines
    assert "SUPREME COURT OF THE STATE OF NEW YORK COUNTY OF NEW YORK" not in headlines


def test_extract_newspaper_articles_sanitizes_inline_photo_credit(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (2200.0, 3200.0), 2: (2200.0, 3200.0), 3: (2200.0, 3200.0)}
        structured = {
            "body": {
                "children": [{"$ref": f"#/texts/{index}"} for index in range(4)]
            },
            "texts": [
                {
                    "label": "section_header",
                    "text": "Tariff Clock Is Ticking After BRENDAN MCDERMID/REUTERS The Senate approval is a big win",
                    "prov": _prov(3, 50, 2920, 1500, 2790),
                },
                {"label": "text", "text": "Trade deck line.", "prov": _prov(3, 60, 2700, 1200, 2640)},
                {"label": "text", "text": "Main trade body paragraph. " * 35, "prov": _prov(3, 60, 2500, 360, 2100)},
                {"label": "text", "text": "Second trade body paragraph. " * 35, "prov": _prov(3, 420, 2500, 720, 2100)},
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    article = next(article for article in result["articles"])
    assert "REUTERS" not in article["headline"]
    assert article["headline"].startswith("Tariff Clock Is Ticking After")


def test_extract_newspaper_articles_filters_fragmented_headline_and_notice_copy(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (2200.0, 3200.0)}
        structured = {
            "body": {"children": [{"$ref": f"#/texts/{index}"} for index in range(8)]},
            "texts": [
                {
                    "label": "section_header",
                    "text": "growing tensions in the coalition were on display late Thursday after lawmakers returned",
                    "prov": _prov(1, 50, 2920, 1500, 2790),
                },
                {"label": "text", "text": "Broken deck line.", "prov": _prov(1, 60, 2700, 1200, 2640)},
                {"label": "text", "text": "Broken body paragraph. " * 35, "prov": _prov(1, 60, 2500, 360, 2100)},
                {
                    "label": "section_header",
                    "text": "Common Sense Media What parents need to know",
                    "prov": _prov(1, 1450, 1800, 2100, 1700),
                },
                {
                    "label": "text",
                    "text": "Available in theaters. Age 15+. Terms of sale and certified check information do not belong in an article.",
                    "prov": _prov(1, 1450, 1650, 2100, 1500),
                },
                {"label": "section_header", "text": "Clean political article", "prov": _prov(1, 200, 1500, 1100, 1400)},
                {"label": "text", "text": "Deck for clean political article.", "prov": _prov(1, 220, 1360, 1080, 1300)},
                {"label": "text", "text": "Clean political body paragraph. " * 60, "prov": _prov(1, 220, 1260, 1080, 700)},
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    headlines = [article["headline"] for article in result["articles"]]
    assert "Clean political article" in headlines
    assert "Common Sense Media What parents need to know" not in headlines
    assert not any(headline.startswith("growing tensions in the coalition") for headline in headlines)


def test_render_newspaper_reading_markdown_uses_selected_indexes() -> None:
    result = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_top_half_count": 1,
        "articles": [
            {
                "headline": "Lower ranked story",
                "page_start": 5,
                "article_type": "secondary_story",
                "quality": {"grade": "medium", "score": 70},
                "score": 12.5,
                "body_text": "Body A",
            },
            {
                "headline": "Primary story",
                "page_start": 1,
                "article_type": "main_story",
                "quality": {"grade": "high", "score": 93},
                "score": 45.2,
                "body_text": "Body B",
            },
        ],
        "selected_article_indexes": [1],
    }

    markdown_text = render_newspaper_reading_markdown(result)

    assert "Primary story" in markdown_text
    assert "Lower ranked story" not in markdown_text
    assert "Included in this file: 1 (selected)" in markdown_text


def test_write_newspaper_reading_markdown_writes_markdown_file(tmp_path: Path) -> None:
    output_path = tmp_path / "articles.md"
    result = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_top_half_count": 1,
        "articles": [
            {
                "headline": "Story headline",
                "page_start": 2,
                "article_type": "main_story",
                "quality": {"grade": "high", "score": 90},
                "score": 33.1,
                "body_text": "First paragraph.\n\nSecond paragraph.",
            }
        ],
        "selected_article_indexes": [0],
    }

    write_newspaper_reading_markdown(result, output_path)

    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "# Reading Edition: sample.pdf" in content
    assert "## 1. Story headline" in content
    assert "Byline: None" not in content


def test_extract_newspaper_articles_filters_listing_fragments(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (2200.0, 3200.0)}
        listing_body = "\n\n".join(
            [
                "W L 26 39 82 z-Vegas .......................",
                "y-Anaheim 82 43 33 ..................",
                "xy-Edmonton 40 30 81 ..............",
                "x-Seattle 36 81 34 .....................",
                "Miami at Real Salt Lake, 9:30",
                "Colorado at Los Angeles FC, 10:30",
                "Austin FC at San Jose, 10:30",
                "WP: Beeter (1-0); LP: Santana (2-1).",
                "R H BI BBSOAVG 0 2 .238 3 0 .246",
            ]
        )
        structured = {
            "body": {"children": [{"$ref": f"#/texts/{index}"} for index in range(6)]},
            "texts": [
                {"label": "section_header", "text": "Serious politics story holds coalition together", "prov": _prov(1, 50, 2920, 1400, 2790)},
                {"label": "text", "text": "Deck for coalition story.", "prov": _prov(1, 60, 2700, 1200, 2640)},
                {"label": "text", "text": "Coalition body paragraph. " * 60, "prov": _prov(1, 60, 2500, 360, 2000)},
                {"label": "section_header", "text": "Miami at Real Salt Lake, 9:30 Colorado at Los Angeles FC, 10:30", "prov": _prov(1, 1450, 2200, 2100, 2100)},
                {"label": "text", "text": listing_body, "prov": _prov(1, 1450, 2050, 2100, 1300)},
                {"label": "text", "text": "...............", "prov": _prov(1, 1450, 1250, 2100, 1200)},
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    headlines = [article["headline"] for article in result["articles"]]
    assert "Serious politics story holds coalition together" in headlines
    assert not any("Miami at Real Salt Lake" in headline for headline in headlines)


def test_extract_newspaper_articles_filters_calendar_listing_headlines(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (2200.0, 3200.0)}
        structured = {
            "body": {"children": [{"$ref": f"#/texts/{index}"} for index in range(6)]},
            "texts": [
                {"label": "section_header", "text": "Regional policy fight escalates in congress", "prov": _prov(1, 50, 2920, 1400, 2790)},
                {"label": "text", "text": "Policy deck line.", "prov": _prov(1, 60, 2700, 1200, 2640)},
                {"label": "text", "text": "Policy body paragraph. " * 55, "prov": _prov(1, 60, 2500, 360, 2000)},
                {"label": "section_header", "text": "Saturday, April 26 Earth Day at Brookside Gardens", "prov": _prov(1, 1450, 2200, 2100, 2100)},
                {
                    "label": "text",
                    "text": "Saturday 10:00 a.m. Sunday 11:00 a.m. Monday 12:00 p.m. April 26. April 27. April 28. May 1. May 2. May 3.",
                    "prov": _prov(1, 1450, 2050, 2100, 1300),
                },
                {"label": "text", "text": "Admission and location details.", "prov": _prov(1, 1450, 1250, 2100, 1200)},
            ],
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    headlines = [article["headline"] for article in result["articles"]]
    assert "Regional policy fight escalates in congress" in headlines
    assert "Saturday, April 26 Earth Day at Brookside Gardens" not in headlines


def test_extract_newspaper_articles_skips_dense_fragment_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import newspaper as newspaper_module

    original_page_sizes = newspaper_module._page_sizes
    try:
        newspaper_module._page_sizes = lambda _: {1: (2200.0, 3200.0), 2: (2200.0, 3200.0)}

        texts = [
            {"label": "section_header", "text": "Front-page policy analysis", "prov": _prov(1, 80, 2920, 1500, 2800)},
            {"label": "text", "text": "Policy body paragraph. " * 65, "prov": _prov(1, 90, 2660, 1560, 2000)},
            {"label": "section_header", "text": "League standings and schedules board", "prov": _prov(2, 1500, 2950, 1960, 2860)},
        ]
        for index in range(220):
            texts.append(
                {
                    "label": "text",
                    "text": f"T{index} ............. {index % 9} {index % 7} {index % 5}",
                    "prov": _prov(2, 1510, 2800 - index * 5, 1980, 2790 - index * 5),
                }
            )

        structured = {
            "body": {"children": [{"$ref": f"#/texts/{index}"} for index in range(len(texts))]},
            "texts": texts,
        }
        result = extract_newspaper_articles(structured, pdf_path)
    finally:
        newspaper_module._page_sizes = original_page_sizes

    headlines = [article["headline"] for article in result["articles"]]
    assert "Front-page policy analysis" in headlines
    assert not any("League standings and schedules board" in headline for headline in headlines)
    assert any(page["page_no"] == 2 for page in result["skipped_pages"])
