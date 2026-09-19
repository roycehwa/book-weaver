from pdf_translator.reconstruct import reconstruct_markdown


def test_nested_groups_and_multipage_provenance_are_not_lost():
    from pdf_translator.reconstruct import _extract_text_blocks
    structured = {'body': {'children': [{'$ref': '#/groups/0'}]},
                  'groups': [{'children': [{'$ref': '#/texts/0'}, {'$ref': '#/groups/0'}]}],
                  'texts': [{'text': 'First page Second page', 'prov': [
                      {'page_no': 1, 'charspan': [0, 10], 'bbox': {'l': 10, 't': 20}},
                      {'page_no': 2, 'charspan': [11, 22], 'bbox': {'l': 10, 't': 700}}]}]}
    blocks = _extract_text_blocks(structured)
    assert [(b.page_no, b.text) for b in blocks] == [(1, 'First page'), (2, 'Second page')]
    assert [(b.source_node_id, b.source_char_start, b.source_char_end) for b in blocks] == [
        ('#/texts/0', 0, 10),
        ('#/texts/0', 11, 22),
    ]
    assert blocks[1].source_separator_before == ' '


def test_ambiguous_multipage_provenance_cannot_silently_drop_text():
    import pytest
    from pdf_translator.reconstruct import _extract_text_blocks
    with pytest.raises(ValueError, match='page ownership'):
        _extract_text_blocks({'body': {'children': [{'$ref': '#/texts/0'}]},
                              'texts': [{'text': 'Text', 'prov': [{'page_no': 1}, {'page_no': 2}]}]})


def test_geometric_reflow_requires_position_and_lowercase_continuation():
    from pdf_translator.book_rebuild import _join_geometric_continuations
    from pdf_translator.reconstruct import LayoutBlock
    a = LayoutBlock('text', 'A revolution-', 1, 50, 200, 190, 350)
    b = LayoutBlock('text', 'ary movement emerged.', 1, 50, 186, 176, 350)
    assert _join_geometric_continuations([a, b])[0].text == 'A revolutionary movement emerged.'
    b.top = 170
    assert len(_join_geometric_continuations([a, b])) == 2
    b.top = 186
    b.left = 70
    assert len(_join_geometric_continuations([a, b])) == 2


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


def test_indented_body_is_not_a_second_column():
    from pdf_translator.reconstruct import LayoutBlock, _cluster_columns
    blocks = [LayoutBlock("text", "full paragraph", 1, 40, 600, right=500),
              LayoutBlock("text", "indented continuation", 1, 160, 590, right=500)]
    assert len(_cluster_columns(blocks)) == 1
