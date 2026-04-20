from pdf_translator.reconstruct import reconstruct_markdown


def _prov(page_no: int, left: float, top: float) -> list[dict]:
    return [{"page_no": page_no, "bbox": {"l": left, "t": top}}]


def test_reconstruct_markdown_reorders_columns_and_repairs_byline() -> None:
    structured = {
        "body": {
            "children": [
                {"$ref": "#/texts/0"},
                {"$ref": "#/texts/1"},
                {"$ref": "#/texts/2"},
                {"$ref": "#/texts/3"},
                {"$ref": "#/texts/4"},
                {"$ref": "#/texts/5"},
                {"$ref": "#/texts/6"},
                {"$ref": "#/texts/7"},
            ]
        },
        "texts": [
            {"label": "section_header", "text": "Periscope", "prov": _prov(1, 30, 710)},
            {"label": "section_header", "text": "Deadly Divides", "prov": _prov(1, 70, 630)},
            {"label": "text", "text": "Left column lead.", "prov": _prov(1, 35, 520)},
            {"label": "text", "text": "Earlier this year, Kirk by", "prov": _prov(1, 35, 420)},
            {"label": "text", "text": "JESUS", "prov": _prov(1, 180, 430)},
            {"label": "text", "text": "MESA", "prov": _prov(1, 180, 410)},
            {"label": "text", "text": "warned on X about violence.", "prov": _prov(1, 190, 390)},
            {"label": "page_footer", "text": "NEWSWEEK.COM", "prov": _prov(1, 500, 40)},
        ],
    }

    markdown = reconstruct_markdown(structured, "fallback")

    assert "Periscope" not in markdown
    assert "NEWSWEEK.COM" not in markdown
    assert markdown.index("Left column lead.") < markdown.index("warned on X about violence.")
    assert "By Jesus Mesa" in markdown
    assert "Earlier this year, Kirk" in markdown


def test_reconstruct_markdown_formats_headers_and_captions() -> None:
    structured = {
        "body": {
            "children": [
                {"$ref": "#/texts/0"},
                {"$ref": "#/texts/1"},
                {"$ref": "#/texts/2"},
            ]
        },
        "texts": [
            {"label": "section_header", "text": "Law and World Order", "prov": _prov(1, 50, 600)},
            {"label": "caption", "text": "MASS PANIC Crowd runs for cover.", "prov": _prov(1, 410, 500)},
            {"label": "text", "text": "正文段落示例。", "prov": _prov(1, 50, 420)},
        ],
    }

    markdown = reconstruct_markdown(structured, "fallback")

    assert "## Law and World Order" in markdown
    assert "> MASS PANIC Crowd runs for cover." in markdown
    assert "正文段落示例。" in markdown
