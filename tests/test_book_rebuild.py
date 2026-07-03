import pdf_translator.book_rebuild as book_rebuild
from pdf_translator.book_rebuild import apply_canonical_chapter_plan, build_book_reconstruction


def _prov(page_no: int, left: float, top: float) -> list[dict]:
    return [{"page_no": page_no, "bbox": {"l": left, "t": top}}]


def test_apply_canonical_chapter_plan_replaces_automatic_titles_and_preserves_pages() -> None:
    book = {
        "metadata": {"chapter_source": "pdf_outline"},
        "chapters": [
            {
                "title": "Running Header",
                "source_pages": [1, 2, 3],
                "trace_markdown": (
                    "[[page: 1]]\nFront matter.\n\n"
                    "[[page: 2]]\nFirst body page.\n\n"
                    "[[page: 3]]\nSecond body page."
                ),
                "markdown": "Front matter.\n\nFirst body page.\n\nSecond body page.",
            }
        ],
        "pages": [
            {"page_no": 1, "has_content": True},
            {"page_no": 2, "has_content": True},
            {"page_no": 3, "has_content": True},
        ],
    }
    canonical = {
        "chapters": [
            {
                "title": "Confirmed Chapter",
                "page_start": 2,
                "page_end": 3,
                "source_pages": [2, 3],
            }
        ]
    }

    result = apply_canonical_chapter_plan(book, canonical)

    assert result["metadata"]["chapter_source"] == "user_confirmed_canonical"
    assert [chapter["title"] for chapter in result["chapters"]] == [
        "Front Matter",
        "Confirmed Chapter",
    ]
    assert result["chapters"][0]["toc"] is False
    assert result["chapters"][1]["toc"] is True
    assert "First body page." in result["chapters"][1]["markdown"]
    assert {page for chapter in result["chapters"] for page in chapter["source_pages"]} == {1, 2, 3}


def test_apply_canonical_chapter_plan_preserves_asset_only_pages() -> None:
    book = {
        "metadata": {"chapter_source": "pdf_outline"},
        "chapters": [
            {
                "title": "Body",
                "source_pages": [1],
                "trace_markdown": "[[page: 1]]\nBody text.",
                "markdown": "Body text.",
            }
        ],
        "pages": [
            {"page_no": 1, "has_content": True, "figure_count": 0, "table_count": 0},
            {"page_no": 2, "has_content": True, "figure_count": 0, "table_count": 1},
            {"page_no": 3, "has_content": False, "figure_count": 0, "table_count": 0},
        ],
        "assets": [
            {
                "kind": "table",
                "page_no": 2,
                "path": "/tmp/table-p0002-01.png",
                "text": "![Table 2.1](/tmp/table-p0002-01.png)",
            }
        ],
    }
    canonical = {
        "chapters": [
            {"title": "Confirmed", "page_start": 1, "page_end": 1, "source_pages": [1]}
        ]
    }

    result = apply_canonical_chapter_plan(book, canonical)

    assert result["chapters"][-1]["source_pages"] == [2]
    assert "table-p0002-01.png" in result["chapters"][-1]["markdown"]
    assert all(3 not in chapter["source_pages"] for chapter in result["chapters"])


def test_canonical_plan_preserves_unplanned_resource_pages() -> None:
    book = {
        "chapters": [
            {
                "title": "Index",
                "source_pages": [8, 9],
                "trace_markdown": (
                    "[[page: 8]]\n\n![Original page 8](/tmp/p8.png)\n\n"
                    "[[page: 9]]\n\n![Original page 9](/tmp/p9.png)"
                ),
                "preserve_original": True,
                "resource_only": True,
                "translate": False,
            }
        ],
        "pages": [
            {"page_no": 8, "has_content": True},
            {"page_no": 9, "has_content": True},
        ],
    }
    canonical = {
        "chapters": [
            {
                "title": "Confirmed Index",
                "source_pages": [8],
                "page_start": 8,
                "page_end": 8,
            }
        ]
    }

    result = apply_canonical_chapter_plan(book, canonical)
    supplemental = result["chapters"][-1]

    assert supplemental["source_pages"] == [9]
    assert supplemental["preserve_original"] is True
    assert supplemental["translate"] is False
    assert "Original page 9" in supplemental["markdown"]


def test_canonical_plan_does_not_merge_resource_and_content_pages() -> None:
    book = {
        "chapters": [
            {
                "title": "Front Matter",
                "source_pages": [1],
                "trace_markdown": "[[page: 1]]\n\nFront matter.",
                "translate": True,
            },
            {
                "title": "Contents",
                "source_pages": [2],
                "trace_markdown": "[[page: 2]]\n\n![Original page 2](/tmp/p2.png)",
                "preserve_original": True,
                "resource_only": True,
                "translate": False,
            },
            {
                "title": "Chapter",
                "source_pages": [3],
                "trace_markdown": "[[page: 3]]\n\nChapter body.",
                "translate": True,
            },
        ],
        "pages": [
            {"page_no": 1, "has_content": True},
            {"page_no": 2, "has_content": True},
            {"page_no": 3, "has_content": True},
        ],
    }
    canonical = {
        "chapters": [
            {
                "title": "Confirmed Chapter",
                "source_pages": [3],
                "page_start": 3,
                "page_end": 3,
            }
        ]
    }

    result = apply_canonical_chapter_plan(book, canonical)
    page_two_chapter = next(
        chapter for chapter in result["chapters"] if chapter["source_pages"] == [2]
    )

    assert page_two_chapter["preserve_original"] is True
    assert page_two_chapter["translate"] is False


def test_preserved_page_images_keep_trace_markers(tmp_path, monkeypatch) -> None:
    chapters = [
        {
            "title": "Index",
            "source_pages": [10, 11],
            "preserve_original": True,
        }
    ]
    monkeypatch.setattr(
        book_rebuild,
        "_render_pdf_page_image",
        lambda _source, _images, page: tmp_path / f"page-{page}.png",
    )

    book_rebuild._replace_preserved_apparatus_with_page_images(
        chapters,
        source_pdf=tmp_path / "source.pdf",
        images_dir=tmp_path,
    )

    assert "[[page: 10]]" in chapters[0]["trace_markdown"]
    assert "[[page: 11]]" in chapters[0]["trace_markdown"]
    assert "[[page:" not in chapters[0]["markdown"]


def test_book_rebuild_preserves_skipped_outline_sections_untranslated(monkeypatch) -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(6)]
        },
        "texts": [
            {"label": "section_header", "text": "Contents", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "Chapter 1 ........ 3", "prov": _prov(1, 40, 680)},
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(3, 40, 760)},
            {"label": "text", "text": "The translated body should remain in a normal chapter.", "prov": _prov(3, 40, 620)},
            {"label": "section_header", "text": "Index", "prov": _prov(4, 40, 760)},
            {"label": "text", "text": "Alpha, 3, 4", "prov": _prov(4, 40, 700)},
        ],
        "pictures": [],
        "tables": [],
    }

    monkeypatch.setattr(
        book_rebuild,
        "_extract_pdf_outline_chapters",
        lambda source_pdf, total_pages: [
            {"title": "Contents", "page_no": 1, "depth": 0, "skip": True},
            {"title": "Chapter 1", "page_no": 3, "depth": 0, "skip": False},
            {"title": "Index", "page_no": 4, "depth": 0, "skip": True},
        ],
    )

    result = build_book_reconstruction(structured)

    assert [chapter["title"] for chapter in result["chapters"]] == ["Contents", "Chapter 1", "Index"]
    assert result["chapters"][0]["translate"] is False
    assert result["chapters"][0]["preserve_original"] is True
    assert "Chapter 1" in result["chapters"][0]["markdown"]
    assert result["chapters"][1]["translate"] is True
    assert result["chapters"][2]["translate"] is False
    assert "Alpha, 3, 4" in result["chapters"][2]["markdown"]


def test_book_rebuild_filters_epub_title_page_shell() -> None:
    structured = {
        "_epub_meta": {
            "schema": "epub_ingest_v1",
            "chapters": [
                {"title": "Cover", "markdown": "![Cover](/tmp/cover.png)\n"},
                {"title": "Title Page", "markdown": "The Book Title\n"},
                {"title": "Chapter 1", "markdown": "Real body text."},
            ],
        }
    }

    result = build_book_reconstruction(structured)

    assert [chapter["title"] for chapter in result["chapters"]] == ["Cover", "Chapter 1"]
    assert result["chapters"][0]["translate"] is False
    assert result["chapters"][1]["translate"] is True


def test_book_rebuild_marks_epub_cover_page_as_non_toc_resource() -> None:
    structured = {
        "_epub_meta": {
            "schema": "epub_ingest_v1",
            "chapters": [
                {"title": "Cover Page", "markdown": "![Cover](/tmp/cover.png)\n"},
                {"title": "Chapter 1", "markdown": "Real body text."},
            ],
            "assets": [{"kind": "cover", "path": "/tmp/cover.png"}],
        }
    }

    result = build_book_reconstruction(structured)

    assert result["chapters"][0]["title"] == "Cover Page"
    assert result["chapters"][0]["translate"] is False
    assert result["chapters"][0]["preserve_original"] is True
    assert result["chapters"][0]["toc"] is False
    assert result["metadata"]["cover_image_path"] == "/tmp/cover.png"


def test_book_rebuild_marks_epub_apparatus_chapters_as_non_toc_resources() -> None:
    structured = {
        "_epub_meta": {
            "schema": "epub_ingest_v1",
            "chapters": [
                {"title": "Contents", "markdown": "[Chapter 1](chapter.xhtml)\n"},
                {"title": "List of Figures and Tables", "markdown": "- Figure 1.1 Sample\n"},
                {"title": "Chapter 1", "markdown": "Real body text."},
                {"title": "Notes", "markdown": "- 1. A note.\n"},
                {"title": "Index", "markdown": "- Alpha, 1\n"},
                {"title": "V", "markdown": "- Veblen, Thorsten, [161](text/page.xhtml#p161)\n- Venice, 32, 35, 37, 40\n"},
                {"title": "W", "markdown": "- Wallace, Henry, 187-188\n- Walmart, 196, 197, 199, 200, 201\n"},
            ],
        }
    }

    result = build_book_reconstruction(structured)

    by_title = {chapter["title"]: chapter for chapter in result["chapters"]}
    assert by_title["Contents"]["translate"] is False
    assert by_title["Contents"]["toc"] is False
    assert by_title["List of Figures and Tables"]["translate"] is False
    assert by_title["List of Figures and Tables"]["toc"] is False
    assert by_title["Chapter 1"]["translate"] is True
    assert by_title["Chapter 1"]["toc"] is True
    assert by_title["Notes"]["translate"] is False
    assert by_title["Index"]["toc"] is False
    assert by_title["V"]["translate"] is False
    assert by_title["V"]["toc"] is False
    assert by_title["W"]["translate"] is False
    assert by_title["Chapter 1"]["chapter_id"] == "ch-003-chapter-1"


def test_epub_front_and_back_matter_translation_policy() -> None:
    structured = {
        "_epub_meta": {
            "schema": "epub_ingest_v1",
            "chapters": [
                {"title": "Copyright", "markdown": "Copyright notice."},
                {"title": "Dedication", "markdown": "For everyone who helped."},
                {"title": "Acknowledgments", "markdown": "Thanks to the editors."},
                {"title": "About the Author", "markdown": "The author biography."},
                {"title": "Index", "markdown": "Alpha, 1"},
                {"title": "End User License Agreement", "markdown": "License terms."},
            ],
        }
    }

    result = build_book_reconstruction(structured)
    by_title = {chapter["title"]: chapter for chapter in result["chapters"]}

    assert by_title["Copyright"]["translate"] is False
    assert by_title["Dedication"]["translate"] is True
    assert by_title["Acknowledgments"]["translate"] is True
    assert by_title["About the Author"]["translate"] is True
    assert by_title["Index"]["translate"] is False
    assert by_title["End User License Agreement"]["translate"] is False


def test_book_rebuild_adds_pdf_cover_chapter(monkeypatch, tmp_path) -> None:
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png")
    monkeypatch.setattr(book_rebuild, "_render_pdf_cover_page", lambda source_pdf, images_dir: cover)
    monkeypatch.setattr(book_rebuild, "_crop_pdf_regions", lambda *args, **kwargs: {})
    structured = {
        "body": {"children": [{"$ref": "#/texts/0"}]},
        "texts": [{"label": "text", "text": "Body text.", "prov": _prov(2, 40, 620)}],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured, source_pdf=tmp_path / "book.pdf", images_dir=tmp_path)

    assert result["chapters"][0]["title"] == "Cover"
    assert result["chapters"][0]["translate"] is False
    assert "![Cover]" in result["chapters"][0]["markdown"]
    assert result["metadata"]["cover_image_path"] == str(cover)
    assert any(asset["kind"] == "cover" for asset in result["assets"])


def test_book_rebuild_runs_layout_fallback_when_only_cover_exists(monkeypatch, tmp_path) -> None:
    cover = tmp_path / "cover.png"
    table = tmp_path / "table-p0002-01.png"
    cover.write_bytes(b"png")
    table.write_bytes(b"png")
    monkeypatch.setattr(book_rebuild, "_render_pdf_cover_page", lambda source_pdf, images_dir: cover)
    monkeypatch.setattr(book_rebuild, "_crop_pdf_regions", lambda *args, **kwargs: {2: {1: table}})
    monkeypatch.setattr(book_rebuild, "_extract_pdf_outline_chapters", lambda source_pdf, total_pages: [])
    structured = {
        "body": {"children": [{"$ref": f"#/texts/{index}"} for index in range(8)]},
        "texts": [
            {"label": "section_header", "text": "Title Page", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "A short title-page line.", "prov": _prov(1, 40, 700)},
            {"label": "section_header", "text": "Contents", "prov": _prov(2, 40, 760)},
            {"label": "text", "text": "1 Introduction ........ 3", "prov": _prov(2, 40, 700)},
            {"label": "section_header", "text": "Introduction", "prov": _prov(3, 40, 760)},
            {
                "label": "text",
                "text": "The real body starts here and must not be dropped merely because a cover chapter already exists.",
                "prov": _prov(3, 40, 620),
            },
            {
                "label": "text",
                "text": "The introduction continues with enough normal prose to form a readable chapter.",
                "prov": _prov(4, 40, 620),
            },
            {
                "label": "text",
                "text": "Another body paragraph confirms the layout fallback keeps pages after the table of contents.",
                "prov": _prov(5, 40, 620),
            },
        ],
        "pictures": [],
        "tables": [
            {
                "prov": [{"page_no": 2, "bbox": {"l": 40, "t": 700, "r": 300, "b": 500}}],
                "data": {"grid": [[{"text": "1 Introduction"}, {"text": "3"}]]},
            }
        ],
    }

    result = build_book_reconstruction(structured, source_pdf=tmp_path / "book.pdf", images_dir=tmp_path)

    assert result["chapters"][0]["title"] == "Cover"
    assert any(chapter["title"] == "Introduction" for chapter in result["chapters"])
    assert any(chapter["title"] == "Contents" for chapter in result["chapters"])
    assert all(not chapter["title"].startswith("Original Visual Page") for chapter in result["chapters"])
    introduction = next(chapter for chapter in result["chapters"] if chapter["title"] == "Introduction")
    contents = next(chapter for chapter in result["chapters"] if chapter["title"] == "Contents")
    assert contents["translate"] is False
    assert contents["toc"] is False
    assert introduction["translate"] is True
    assert introduction["preserve_original"] is False
    assert "The real body starts here" in introduction["markdown"]
    assert "The introduction continues" in introduction["markdown"]
    assert "Contents" not in introduction["markdown"]


def test_book_rebuild_prefers_pdf_table_crop_over_markdown_table(monkeypatch, tmp_path) -> None:
    table_crop = tmp_path / "table-p0001-01.png"
    table_crop.write_bytes(b"png")
    monkeypatch.setattr(book_rebuild, "_render_pdf_cover_page", lambda source_pdf, images_dir: None)
    monkeypatch.setattr(book_rebuild, "_crop_pdf_regions", lambda *args, **kwargs: {1: {1: table_crop}})
    structured = {
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/tables/0"}]},
        "texts": [{"label": "text", "text": "Body text before the table.", "prov": _prov(1, 40, 620)}],
        "pictures": [],
        "tables": [
            {
                "prov": _prov(1, 100, 300),
                "data": {
                    "table_cells": [
                        {"row_header": True, "text": "A"},
                        {"row_header": True, "text": "B"},
                        {"text": "1"},
                        {"text": "2"},
                    ]
                },
            }
        ],
    }

    result = build_book_reconstruction(structured, source_pdf=tmp_path / "book.pdf", images_dir=tmp_path)

    markdown = result["chapters"][0]["markdown"]
    assert f"![Table 1.1]({table_crop})" in markdown
    assert "| A | B |" not in markdown
    assert "**Table 1.1**" not in markdown
    assert any(asset["kind"] == "table" and asset["path"] == str(table_crop) for asset in result["assets"])


def test_book_rebuild_preserves_pdf_back_matter_as_original_page_images(monkeypatch, tmp_path) -> None:
    page_image = tmp_path / "original-page-p0003.png"
    page_image.write_bytes(b"png")
    monkeypatch.setattr(book_rebuild, "_render_pdf_cover_page", lambda source_pdf, images_dir: None)
    monkeypatch.setattr(book_rebuild, "_crop_pdf_regions", lambda *args, **kwargs: {})
    monkeypatch.setattr(book_rebuild, "_render_pdf_page_image", lambda source_pdf, images_dir, page_no: page_image)
    monkeypatch.setattr(
        book_rebuild,
        "_extract_pdf_outline_chapters",
        lambda source_pdf, total_pages: [
            {"title": "Chapter 1", "page_no": 1, "depth": 0, "skip": False},
            {"title": "Notes", "page_no": 3, "depth": 0, "skip": True},
        ],
    )
    structured = {
        "body": {"children": [{"$ref": f"#/texts/{index}"} for index in range(4)]},
        "texts": [
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "Body text.", "prov": _prov(1, 40, 620)},
            {"label": "section_header", "text": "Notes", "prov": _prov(3, 40, 760)},
            {"label": "text", "text": "1. A densely formatted note that should remain visually faithful.", "prov": _prov(3, 40, 700)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured, source_pdf=tmp_path / "book.pdf", images_dir=tmp_path)
    notes = result["chapters"][1]

    assert notes["title"] == "Notes"
    assert notes["translate"] is False
    assert notes["resource_only"] is True
    assert notes["toc"] is False
    assert f"![Original page 3]({page_image.name})" in notes["markdown"]
    assert str(tmp_path) not in notes["markdown"]
    assert "densely formatted note" not in notes["markdown"]


def test_book_rebuild_keeps_front_matter_when_it_has_text(monkeypatch) -> None:
    monkeypatch.setattr(
        book_rebuild,
        "_classify_page_kind",
        lambda page_no, total_pages, blocks: "front_matter" if page_no == 1 else "body",
    )
    structured = {
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}, {"$ref": "#/texts/2"}]},
        "texts": [
            {"label": "text", "text": "Cover line only", "prov": _prov(1, 40, 400)},
            {"label": "section_header", "text": "Chapter A", "prov": _prov(2, 40, 760)},
            {"label": "text", "text": "x" * 300, "prov": _prov(2, 40, 600)},
        ],
        "pictures": [],
        "tables": [],
    }
    result = build_book_reconstruction(structured, source_pdf=None)
    assert "Cover line only" in result["full_markdown"]


def test_book_rebuild_dedupes_adjacent_duplicate_headings() -> None:
    structured = {
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}, {"$ref": "#/texts/2"}]},
        "texts": [
            {"label": "section_header", "text": "Senses of Mourning", "prov": _prov(1, 40, 760)},
            {"label": "section_header", "text": "Senses of Mourning", "prov": _prov(1, 40, 700)},
            {"label": "text", "text": "Subtitle body.", "prov": _prov(1, 40, 620)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert result["full_markdown"].count("Senses of Mourning") == 1
    assert "Subtitle body." in result["full_markdown"]


def test_book_rebuild_preserves_toc_outside_reader_navigation_and_splits_chapters() -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(11)]
        },
        "texts": [
            {"label": "section_header", "text": "Contents", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "Chapter 1 ........ 3", "prov": _prov(1, 40, 680)},
            {"label": "text", "text": "Chapter 2 ........ 17", "prov": _prov(1, 40, 640)},
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(2, 40, 760)},
            {"label": "section_header", "text": "A Beginning", "prov": _prov(2, 40, 720)},
            {"label": "text", "text": "This is the opening paragraph of the first chapter with enough text to count as body copy.", "prov": _prov(2, 40, 600)},
            {"label": "text", "text": "More first chapter body text follows on the same page in a normal readable flow.", "prov": _prov(2, 40, 520)},
            {"label": "text", "text": "The first chapter continues onto the next page with more narrative detail and continuity.", "prov": _prov(3, 40, 620)},
            {"label": "section_header", "text": "Chapter 2", "prov": _prov(4, 40, 760)},
            {"label": "section_header", "text": "Another Start", "prov": _prov(4, 40, 720)},
            {"label": "text", "text": "The second chapter opens with a fresh body paragraph that should be retained.", "prov": _prov(4, 40, 600)},
        ],
        "pictures": [],
    }

    result = build_book_reconstruction(structured)

    assert result["chapter_count"] == 3
    assert result["pages"][0]["page_kind"] == "toc"
    assert result["chapters"][0]["title"] == "Contents"
    assert result["chapters"][0]["toc"] is False
    assert result["chapters"][1]["title"] == "Chapter 1: A Beginning"
    assert result["chapters"][1]["chapter_id"] == "ch-002-chapter-1-a-beginning"
    assert result["chapters"][1]["page_start"] == 2
    assert result["chapters"][1]["page_end"] == 3
    assert result["chapters"][2]["title"] == "Chapter 2: Another Start"
    assert "# Chapter 1: A Beginning" in result["full_markdown"]
    assert "[[page: 2]]" not in result["chapters"][1]["markdown"]
    assert "[[page: 2]]" in result["chapters"][1]["trace_markdown"]


def test_book_rebuild_detaches_docling_footnotes_from_body_flow() -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(4)]
        },
        "texts": [
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "Left column body.[^1]", "prov": _prov(1, 40, 650)},
            {"label": "footnote", "text": "1 Left column note.", "prov": _prov(1, 40, 180)},
            {"label": "text", "text": "Right column body.", "prov": _prov(1, 320, 650)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)
    markdown = result["chapters"][0]["markdown"]

    assert markdown.index("Left column body.") < markdown.index("Right column body.")
    assert "1 Left column note." not in markdown
    assert "> 1 Left column note." not in markdown
    semantic = result["semantic_content"]
    assert semantic["schema"] == "semantic_content_v1"
    assert len(semantic["footnotes"]) == 1
    assert semantic["footnotes"][0]["marker"] == "1"
    assert semantic["footnotes"][0]["source_page"] == 1
    assert semantic["footnotes"][0]["source_text"] == "Left column note."
    assert semantic["footnotes"][0]["backlinks"][0]["marker"] == "1"
    assert semantic["footnotes"][0]["backlinks"][0]["chapter_id"] == result["chapters"][0]["chapter_id"]


def test_book_rebuild_links_plain_pdf_footnote_marker_after_punctuation() -> None:
    structured = {
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {
                "label": "text",
                "text": "The body sentence ends here. 3 The next sentence continues.",
                "prov": _prov(1, 40, 580),
            },
            {
                "label": "footnote",
                "text": "3 Author, Foreign Book Title, 42.",
                "prov": _prov(1, 40, 120),
            },
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    note = result["semantic_content"]["footnotes"][0]
    assert len(note["backlinks"]) == 1
    assert note["backlinks"][0]["marker"] == "3"


def test_book_rebuild_links_footnote_carried_to_following_page() -> None:
    structured = {
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {
                "label": "text",
                "text": "The reference appears on this page.10 The discussion continues.",
                "prov": _prov(1, 40, 580),
            },
            {
                "label": "footnote",
                "text": "10 Author, Foreign Article Title, 42.",
                "prov": _prov(2, 40, 120),
            },
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    note = result["semantic_content"]["footnotes"][0]
    assert note["source_page"] == 2
    assert note["backlinks"][0]["source_page"] == 1


def test_book_rebuild_falls_back_to_single_untitled_section() -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(3)]
        },
        "texts": [
            {"label": "text", "text": "A coherent book page starts here with narrative text and no explicit chapter heading.", "prov": _prov(1, 40, 620)},
            {"label": "text", "text": "The body continues with a second readable paragraph that should stay in the same section.", "prov": _prov(2, 40, 620)},
            {"label": "text", "text": "A final paragraph closes the sample body.", "prov": _prov(3, 40, 620)},
        ],
        "pictures": [],
    }

    result = build_book_reconstruction(structured)

    assert result["chapter_count"] == 1
    assert result["chapters"][0]["title"] == "Untitled Section 1"


def test_book_rebuild_uses_first_meaningful_heading_for_section_title() -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(4)]
        },
        "texts": [
            {"label": "section_header", "text": "Tables", "prov": _prov(1, 40, 760)},
            {"label": "section_header", "text": "Preface", "prov": _prov(1, 40, 700)},
            {"label": "text", "text": "A normal preface paragraph follows the front list heading.", "prov": _prov(1, 40, 620)},
            {"label": "text", "text": "More preface text keeps the section readable.", "prov": _prov(1, 40, 560)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert result["chapter_count"] == 1
    assert result["chapters"][0]["title"] == "Preface"


def test_book_rebuild_preserves_trace_page_anchors_figures_and_tables() -> None:
    structured = {
        "body": {
            "children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]
        },
        "texts": [
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "A readable body paragraph appears near the figure and table.", "prov": _prov(1, 40, 620)},
        ],
        "pictures": [
            {
                "prov": [{"page_no": 1, "bbox": {"l": 60, "t": 590, "r": 200, "b": 420}}],
                "captions": [{"text": "A sample figure caption."}],
            }
        ],
        "tables": [
            {
                "prov": [{"page_no": 1, "bbox": {"l": 60, "t": 400, "r": 240, "b": 260}}],
                "data": {
                    "grid": [
                        [{"text": "Name"}, {"text": "Value"}],
                        [{"text": "Alpha"}, {"text": "1"}],
                    ]
                },
            }
        ],
    }

    result = build_book_reconstruction(structured)
    markdown = result["chapters"][0]["markdown"]

    assert "[[page: 1]]" not in markdown
    assert "[[page: 1]]" in result["chapters"][0]["trace_markdown"]
    assert "![Figure 1.1: A sample figure caption.](#figure-1-1)" in markdown
    assert "| Name | Value |" in markdown
    assert result["pages"][0]["figure_count"] == 1
    assert result["pages"][0]["table_count"] == 1


def test_book_rebuild_resolves_referenced_figure_captions_without_duplication() -> None:
    structured = {
        "body": {
            "children": [
                {"$ref": "#/texts/0"},
                {"$ref": "#/texts/1"},
                {"$ref": "#/texts/2"},
            ]
        },
        "texts": [
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "The paragraph should remain near its image.", "prov": _prov(1, 40, 620)},
            {"label": "caption", "text": "Referenced figure caption.", "prov": _prov(1, 60, 400)},
        ],
        "pictures": [
            {
                "prov": [{"page_no": 1, "bbox": {"l": 60, "t": 590, "r": 200, "b": 420}}],
                "captions": [{"$ref": "#/texts/2"}],
            }
        ],
        "tables": [],
    }

    result = build_book_reconstruction(structured)
    markdown = result["chapters"][0]["markdown"]

    assert "![Figure 1.1: Referenced figure caption.](#figure-1-1)" in markdown
    assert markdown.count("Referenced figure caption.") == 2


def test_book_rebuild_skips_unreadable_table_placeholders() -> None:
    structured = {
        "body": {
            "children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]
        },
        "texts": [
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "A normal paragraph should not be followed by an internal table placeholder.", "prov": _prov(1, 40, 620)},
        ],
        "pictures": [],
        "tables": [
            {
                "prov": [{"page_no": 1, "bbox": {"l": 60, "t": 400, "r": 240, "b": 260}}],
                "data": {
                    "grid": [[{"text": ""}]],
                },
            }
        ],
    }

    result = build_book_reconstruction(structured)

    assert "structure preserved" not in result["full_markdown"]
    assert result["pages"][0]["table_count"] == 0


def test_book_rebuild_keeps_image_only_pages() -> None:
    structured = {
        "body": {
            "children": [{"$ref": "#/texts/0"}]
        },
        "texts": [
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(1, 40, 760)},
        ],
        "pictures": [
            {
                "prov": [{"page_no": 2, "bbox": {"l": 60, "t": 590, "r": 200, "b": 420}}],
                "captions": [{"text": "A plate that appears on an otherwise image-only page."}],
            }
        ],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert result["pages"][1]["page_no"] == 2
    assert result["pages"][1]["page_kind"] == "body"
    assert "A plate that appears on an otherwise image-only page." in result["full_markdown"]


def test_book_rebuild_removes_control_chars_and_formula_fragments() -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(6)]
        },
        "texts": [
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "A readable paragraph should remain even in a math-heavy book.", "prov": _prov(1, 40, 620)},
            {"label": "text", "text": "\x05", "prov": _prov(1, 40, 560)},
            {"label": "text", "text": "=", "prov": _prov(1, 40, 540)},
            {"label": "text", "text": "√8", "prov": _prov(1, 40, 520)},
            {"label": "text", "text": "The next paragraph contains x < y as prose and should remain.", "prov": _prov(1, 40, 500)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert "\x05" not in result["full_markdown"]
    assert "\n=\n" not in result["full_markdown"]
    assert "√8" not in result["full_markdown"]
    assert "x < y" in result["full_markdown"]


def test_book_rebuild_preserves_references_and_index_pages() -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(14)]
        },
        "texts": [
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "The chapter body should remain.", "prov": _prov(1, 40, 620)},
            {"label": "section_header", "text": "References", "prov": _prov(2, 40, 760)},
            {"label": "text", "text": "Smith J (2020) A reference title. Publisher.", "prov": _prov(2, 40, 700)},
            {"label": "text", "text": "Brown A (2021) Another reference title. Journal.", "prov": _prov(2, 40, 660)},
            {"label": "text", "text": "Jones B (2022) More reference material. Journal.", "prov": _prov(2, 40, 620)},
            {"label": "text", "text": "Index", "prov": _prov(3, 40, 760)},
            {"label": "text", "text": "Alpha, 1, 2", "prov": _prov(3, 40, 720)},
            {"label": "text", "text": "Beta, 3, 4", "prov": _prov(3, 40, 700)},
            {"label": "text", "text": "Gamma, 5, 6", "prov": _prov(3, 40, 680)},
            {"label": "text", "text": "Delta, 7, 8", "prov": _prov(3, 40, 660)},
            {"label": "text", "text": "Epsilon, 9, 10", "prov": _prov(3, 40, 640)},
            {"label": "text", "text": "Zeta, 11, 12", "prov": _prov(3, 40, 620)},
            {"label": "text", "text": "Eta, 13, 14", "prov": _prov(3, 40, 600)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert result["chapter_count"] == 3
    assert "References" in result["full_markdown"]
    assert "Alpha, 1, 2" in result["full_markdown"]
    assert result["chapters"][1]["toc"] is False
    assert result["chapters"][2]["toc"] is False
    assert result["metadata"]["uncovered_content_pages"] == []
    assert result["metadata"]["content_page_coverage_ratio"] == 1.0
    assert [page["page_kind"] for page in result["pages"]] == ["body", "references", "index"]


def test_book_rebuild_filters_running_page_headers() -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(5)]
        },
        "texts": [
            {"label": "section_header", "text": "Introduction", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "The real introduction heading and its opening paragraph should remain.", "prov": _prov(1, 40, 620)},
            {"label": "page_header", "text": "Introduction", "prov": _prov(2, 80, 612)},
            {"label": "text", "text": "A continued paragraph on the next page should not be preceded by the running header.", "prov": _prov(2, 40, 560)},
            {"label": "page_footer", "text": "2", "prov": _prov(2, 300, 30)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert "# Introduction" in result["full_markdown"]
    assert result["full_markdown"].count("Introduction") == 1
    assert "running header" in result["full_markdown"]


def test_book_rebuild_detaches_note_like_page_footer_from_body(monkeypatch) -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(4)]
        },
        "texts": [
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(1, 40, 760)},
            {"label": "text", "text": "Main body paragraph with a superscript marker in the PDF export.", "prov": _prov(1, 40, 580)},
            {
                "label": "page_footer",
                "text": "1 First footnote line with enough words to qualify as substantive.\n2 Second footnote line also with enough words here.",
                "prov": _prov(1, 40, 120),
            },
            {"label": "page_footer", "text": "12", "prov": _prov(1, 500, 40)},
        ],
        "pictures": [],
        "tables": [],
    }

    monkeypatch.setattr(book_rebuild, "_extract_pdf_outline_chapters", lambda source_pdf, total_pages: [])

    result = build_book_reconstruction(structured)
    md = result["chapters"][0]["markdown"]

    assert "Main body paragraph" in md
    assert "First footnote line" not in md
    assert "Second footnote line" not in md
    assert "12" not in md
    notes = result["semantic_content"]["footnotes"]
    assert [note["marker"] for note in notes] == ["1", "2"]
    assert notes[0]["source_text"].startswith("First footnote line")


def test_book_rebuild_quarantines_ocr_garbage_before_noise_filtering() -> None:
    garbage = "1:79. 2- 80 - - 3291/. 32 / 82.0/ 2 /892: 8/99"
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(3)]
        },
        "texts": [
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(1, 40, 760)},
            {
                "label": "text",
                "text": "Main body paragraph remains readable.",
                "prov": _prov(1, 40, 580),
            },
            {"label": "page_footer", "text": garbage, "prov": _prov(1, 40, 80)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert garbage not in result["full_markdown"]
    quarantine = result["semantic_content"]["ocr_quarantine"]
    assert len(quarantine) == 1
    assert quarantine[0]["raw_text"] == garbage
    assert quarantine[0]["source_page"] == 1
    assert quarantine[0]["disposition"] == "suspect_ocr"
    assert "footer_overlap" in quarantine[0]["reason_codes"]


def test_repeated_control_character_artifact_is_auto_confirmed_as_noise() -> None:
    artifact = "\x13\x06\x0c\x0b\x11\x08\x07\x01\x0f\x0e"
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(6)]
        },
        "texts": [
            {"label": "text", "text": f"Readable page {page}.", "prov": _prov(page, 40, 580)}
            for page in range(1, 4)
        ]
        + [
            {"label": "text", "text": artifact, "prov": _prov(page, 40, 80)}
            for page in range(1, 4)
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)
    quarantine = result["semantic_content"]["ocr_quarantine"]

    assert len(quarantine) == 3
    assert all(item["resolution"] == "confirmed_noise" for item in quarantine)
    assert all(item["auto_resolution"] == "repeated_control_artifact" for item in quarantine)


def test_unreadable_control_blob_is_auto_confirmed_without_repetition() -> None:
    artifact = "1::79\x0b .\x192-\x1980 \x05\x04-\x05\x04\x05\x08"
    structured = {
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {"label": "text", "text": "Readable body.", "prov": _prov(1, 40, 580)},
            {"label": "text", "text": artifact, "prov": _prov(1, 40, 80)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)
    item = result["semantic_content"]["ocr_quarantine"][0]

    assert item["resolution"] == "confirmed_noise"
    assert item["auto_resolution"] == "unreadable_control_artifact"
    assert item["raw_text"] == artifact


def test_book_rebuild_detaches_mislabelled_bottom_footnotes(monkeypatch) -> None:
    structured = {
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}, {"$ref": "#/texts/2"}]},
        "texts": [
            {"label": "section_header", "text": "Chapter One", "prov": _prov(1, 40, 620)},
            {
                "label": "text",
                "text": "Main body paragraph with adequate length for classification as body copy here.",
                "prov": _prov(1, 40, 560),
            },
            {
                "label": "text",
                "text": (
                    "1 First footnote line with enough words to qualify as substantive endnote material here.\n"
                    "2 Second footnote line also with enough words to pass the heuristic."
                ),
                "prov": _prov(1, 40, 80),
            },
        ],
        "pictures": [],
        "tables": [],
    }
    monkeypatch.setattr(book_rebuild, "_extract_pdf_outline_chapters", lambda source_pdf, total_pages: [])

    result = build_book_reconstruction(structured)
    md = result["chapters"][0]["markdown"]
    assert "Main body paragraph" in md
    assert "First footnote line" not in md
    assert [note["marker"] for note in result["semantic_content"]["footnotes"]] == [
        "1",
        "2",
    ]
    assert "footnote_line_ratio" in result["metadata"]
    assert 0.0 <= result["metadata"]["footnote_line_ratio"] <= 1.0
    assert result["metadata"]["footnote_load"] in ("typical", "footnote_heavy")


def test_book_rebuild_promotes_book_section_starts_to_chapters() -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(8)]
        },
        "texts": [
            {"label": "text", "text": "This page intentionally left blank", "prov": _prov(1, 40, 620)},
            {"label": "section_header", "text": "Preface", "prov": _prov(2, 60, 510)},
            {"label": "text", "text": "The preface body should be its own front-matter section.", "prov": _prov(2, 40, 440)},
            {"label": "section_header", "text": "Introduction", "prov": _prov(3, 60, 510)},
            {"label": "text", "text": "The introduction body should begin a new section.", "prov": _prov(3, 40, 440)},
            {"label": "section_header", "text": "Chapter 1", "prov": _prov(4, 60, 510)},
            {"label": "section_header", "text": "The First Chapter", "prov": _prov(4, 60, 480)},
            {"label": "text", "text": "Chapter body text should begin after the chapter heading.", "prov": _prov(4, 40, 420)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert [chapter["title"] for chapter in result["chapters"]] == [
        "Preface",
        "Introduction",
        "Chapter 1: The First Chapter",
    ]
    assert "This page intentionally left blank" not in result["full_markdown"]
    assert "# Preface" in result["full_markdown"]
    assert "# Introduction" in result["full_markdown"]
    assert "# Chapter 1: The First Chapter" in result["full_markdown"]


def test_book_rebuild_detects_compact_numbered_chapters_near_top_band() -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(6)]
        },
        "texts": [
            {"label": "section_header", "text": "PREFACE", "prov": _prov(1, 60, 473)},
            {"label": "text", "text": "Preface body.", "prov": _prov(1, 60, 360)},
            {"label": "section_header", "text": "1 PATH DEPENDENCE", "prov": _prov(2, 60, 472)},
            {"label": "text", "text": "First chapter body.", "prov": _prov(2, 60, 360)},
            {"label": "section_header", "text": "2 AMBIVALENCE", "prov": _prov(3, 60, 473)},
            {"label": "text", "text": "Second chapter body.", "prov": _prov(3, 60, 360)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert [chapter["title"] for chapter in result["chapters"]] == [
        "PREFACE",
        "1 PATH DEPENDENCE",
        "2 AMBIVALENCE",
    ]


def test_book_rebuild_detects_part_divider_with_title() -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(4)]
        },
        "texts": [
            {"label": "section_header", "text": "Part II AUTARKY AND ARMAMENT", "prov": _prov(1, 60, 477)},
            {"label": "text", "text": "Part introduction.", "prov": _prov(1, 60, 360)},
            {"label": "section_header", "text": "3 COMPLIANCE", "prov": _prov(2, 60, 473)},
            {"label": "text", "text": "Chapter body.", "prov": _prov(2, 60, 360)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert [chapter["title"] for chapter in result["chapters"]] == [
        "Part II AUTARKY AND ARMAMENT",
        "3 COMPLIANCE",
    ]


def test_book_rebuild_keeps_title_only_part_divider() -> None:
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(3)]
        },
        "texts": [
            {"label": "section_header", "text": "Part II AUTARKY AND ARMAMENT", "prov": _prov(1, 60, 477)},
            {"label": "section_header", "text": "3 COMPLIANCE", "prov": _prov(2, 60, 473)},
            {"label": "text", "text": "Chapter body.", "prov": _prov(2, 60, 360)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert [chapter["title"] for chapter in result["chapters"]] == [
        "Part II AUTARKY AND ARMAMENT",
        "3 COMPLIANCE",
    ]
    assert result["chapters"][0]["markdown"] == ""


def test_book_rebuild_preserves_contiguous_layout_apparatus_pages(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(book_rebuild, "_render_pdf_cover_page", lambda source_pdf, images_dir: None)
    monkeypatch.setattr(book_rebuild, "_crop_pdf_regions", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        book_rebuild,
        "_render_pdf_page_image",
        lambda source_pdf, images_dir, page_no: tmp_path / f"page-{page_no}.png",
    )
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(6)]
        },
        "texts": [
            {"label": "section_header", "text": "1 BODY", "prov": _prov(1, 60, 473)},
            {"label": "text", "text": "Chapter body.", "prov": _prov(1, 60, 360)},
            {"label": "section_header", "text": "REFERENCES", "prov": _prov(2, 60, 473)},
            {"label": "text", "text": "Reference page one.", "prov": _prov(2, 60, 360)},
            {"label": "text", "text": "Reference page two.", "prov": _prov(3, 60, 360)},
            {"label": "section_header", "text": "INDEX", "prov": _prov(4, 60, 473)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(
        structured,
        source_pdf=tmp_path / "book.pdf",
        images_dir=tmp_path,
    )

    assert [chapter["title"] for chapter in result["chapters"]] == ["1 BODY", "References", "Index"]
    assert result["chapters"][1]["source_pages"] == [2, 3]
    assert result["chapters"][2]["source_pages"] == [4]
    assert "page-2.png" in result["chapters"][1]["markdown"]
    assert "page-3.png" in result["chapters"][1]["markdown"]


def test_book_rebuild_does_not_start_apparatus_from_copyright_citations(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(book_rebuild, "_render_pdf_cover_page", lambda source_pdf, images_dir: None)
    monkeypatch.setattr(book_rebuild, "_crop_pdf_regions", lambda *args, **kwargs: {})
    structured = {
        "body": {
            "children": [{"$ref": f"#/texts/{index}"} for index in range(8)]
        },
        "texts": [
            {"label": "section_header", "text": "Publisher address", "prov": _prov(1, 60, 473)},
            {"label": "text", "text": "This publication is in copyright.", "prov": _prov(1, 60, 430)},
            {"label": "text", "text": "First published 2025. ISBN 978-1-234.", "prov": _prov(1, 60, 400)},
            {"label": "text", "text": "Digital edition 2025 by Publisher.", "prov": _prov(1, 60, 370)},
            {"label": "text", "text": "Catalog record 2024.", "prov": _prov(1, 60, 340)},
            {"label": "text", "text": "Typeset 2023.", "prov": _prov(1, 60, 310)},
            {"label": "section_header", "text": "1 BODY", "prov": _prov(2, 60, 473)},
            {"label": "text", "text": "Chapter body.", "prov": _prov(2, 60, 360)},
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(
        structured,
        source_pdf=tmp_path / "book.pdf",
        images_dir=tmp_path,
    )

    assert all(chapter["title"] != "References" for chapter in result["chapters"])
    assert result["pages"][0]["page_kind"] != "references"
