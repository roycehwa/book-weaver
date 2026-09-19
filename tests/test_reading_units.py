from pathlib import Path

import pytest

from pdf_translator.reading_units import (
    build_reading_units,
    chapter_markdown_from_units,
    logical_chapter_markdown,
    validate_reading_units,
)


def test_pdf_projection_preserves_logical_blocks_and_source_pages() -> None:
    book = {
        "pages": [{"page_no": 1, "page_kind": "body"}, {"page_no": 2, "page_kind": "body"}],
        "chapters": [{
            "index": 1,
            "chapter_id": "chapter-one",
            "title": "Chapter One",
            "markdown": "## Opening\n\nA paragraph crossing the source pages.\n\n> A quotation.",
            "source_pages": [1, 2],
            "translate": True,
            "translation_policy_confirmed": True,
        }],
    }

    payload = build_reading_units(book, source_path=Path("book.pdf"))

    assert payload["schema"] == "bookweaver_reading_units_v1"
    assert payload["generation"]["translation_authority"] is False
    assert [unit["kind"] for unit in payload["units"]] == ["heading", "paragraph", "quote"]
    assert [span["page_no"] for span in payload["units"][1]["provenance"]] == [1, 2]
    assert payload["units"][0]["boundary_before"] == "chapter"
    assert all(unit["policy"] == "translate" for unit in payload["units"])
    assert chapter_markdown_from_units(payload, "chapter-one") == book["chapters"][0]["markdown"]


def test_pdf_cross_page_unit_uses_node_and_character_span_provenance() -> None:
    book = {
        "pages": [
            {
                "page_no": 1,
                "content_items": [{
                    "kind": "text",
                    "text": "A revolution-",
                    "page_no": 1,
                    "source_node_id": "#/texts/0",
                    "source_char_start": 0,
                    "source_char_end": 13,
                }],
            },
            {
                "page_no": 2,
                "content_items": [{
                    "kind": "text",
                    "text": "ary movement emerged.",
                    "page_no": 2,
                    "source_node_id": "#/texts/0",
                    "source_char_start": 14,
                    "source_char_end": 35,
                }],
            },
        ],
        "logical_continuations": [{
            "from_page": 1,
            "to_page": 2,
            "source_node_id": "#/texts/0",
            "source_char_end": 13,
            "next_source_char_start": 14,
            "joined_text": "A revolutionary movement emerged.",
        }],
        "chapters": [{
            "chapter_id": "body",
            "title": "Body",
            "source_pages": [1, 2],
            "markdown": "A revolutionary movement emerged.",
        }],
    }

    payload = build_reading_units(book, source_path=Path("book.pdf"))

    paragraph = payload["units"][1]
    assert paragraph["markdown"] == "A revolutionary movement emerged."
    assert paragraph["provenance"] == [
        {
            "source_format": "pdf",
            "page_no": 1,
            "source_node_id": "#/texts/0",
            "char_start": 0,
            "char_end": 13,
            "precision": "span",
        },
        {
            "source_format": "pdf",
            "page_no": 2,
            "source_node_id": "#/texts/0",
            "char_start": 14,
            "char_end": 35,
            "precision": "span",
        },
    ]


def test_epub_projection_uses_resource_provenance_and_preserve_policy() -> None:
    book = {"chapters": [{
        "index": 4,
        "chapter_id": "notes",
        "title": "Notes",
        "markdown": "1. Original note.",
        "source_internal_path": "OPS/Text/notes.xhtml",
        "translate": False,
        "preserve_original": True,
        "resource_only": True,
        "translation_policy_confirmed": True,
    }]}

    payload = build_reading_units(book, source_path=Path("book.epub"))

    unit = payload["units"][0]
    assert unit["policy"] == "preserve"
    assert unit["policy_confirmed"] is True
    assert unit["provenance"] == [{
        "source_format": "epub",
        "resource_path": "OPS/Text/notes.xhtml",
        "precision": "resource",
    }]


def test_epub_projection_uses_dom_and_link_provenance_when_available() -> None:
    markdown = "See [the note](OPS/Text/notes.xhtml#n1)."
    book = {"chapters": [{
        "index": 1,
        "chapter_id": "body",
        "title": "Body",
        "markdown": markdown,
        "source_internal_path": "OPS/Text/body.xhtml",
        "dom_units": [{
            "markdown": markdown,
            "resource_path": "OPS/Text/body.xhtml",
            "dom_path": "/body[1]/p[2]",
            "char_start": 0,
            "char_end": len("See the note."),
            "element_id": "p2",
            "link_targets": ["OPS/Text/notes.xhtml#n1"],
        }],
    }]}

    payload = build_reading_units(book, source_path=Path("book.epub"))

    paragraph = payload["units"][1]
    assert paragraph["provenance"] == [{
        "source_format": "epub",
        "resource_path": "OPS/Text/body.xhtml",
        "dom_path": "/body[1]/p[2]",
        "char_start": 0,
        "char_end": len("See the note."),
        "precision": "dom",
        "element_id": "p2",
        "link_targets": ["OPS/Text/notes.xhtml#n1"],
    }]


def test_epub_without_resource_path_does_not_overstate_provenance_precision() -> None:
    payload = build_reading_units(
        {"chapters": [{"chapter_id": "body", "title": "Body", "markdown": "Text."}]},
        source_path=Path("book.epub"),
    )

    assert payload["generation"]["provenance_precision"] == "chapter"
    assert payload["units"][0]["provenance"][0]["precision"] == "chapter"


@pytest.mark.parametrize(
    ("chapter", "policy"),
    [
        ({"title": "Body", "markdown": "Text.", "translate": False}, "exclude"),
        ({"title": "Notes", "markdown": "Text.", "translate": False, "preserve_original": True}, "preserve"),
        ({"title": "Body", "markdown": "Text.", "resource_only": True}, "translate"),
    ],
)
def test_projection_policy_matches_existing_translation_flags(chapter: dict, policy: str) -> None:
    payload = build_reading_units({"chapters": [chapter]}, source_path=Path("book.epub"))
    assert {unit["policy"] for unit in payload["units"]} == {policy}


def test_logical_chapter_round_trip_adds_separate_bookir_title() -> None:
    chapter = {"chapter_id": "body", "title": "Body", "markdown": "Alpha.\n\nBeta.\n"}
    payload = build_reading_units({"chapters": [chapter]}, source_path=Path("book.epub"))

    assert chapter_markdown_from_units(payload, "body") == logical_chapter_markdown(chapter)
    assert [unit["kind"] for unit in payload["units"]] == ["heading", "paragraph", "paragraph"]


def test_empty_chapter_keeps_its_logical_heading() -> None:
    payload = build_reading_units(
        {"chapters": [{"chapter_id": "empty", "title": "Empty", "markdown": ""}]},
        source_path=Path("book.pdf"),
    )
    assert chapter_markdown_from_units(payload, "empty") == "# Empty"


def test_projection_is_deterministic_and_fingerprints_content() -> None:
    book = {"chapters": [{"chapter_id": "body", "title": "Body", "markdown": "Alpha.\n\nBeta."}]}

    first = build_reading_units(book, source_path=Path("book.epub"))
    second = build_reading_units(book, source_path=Path("book.epub"))

    assert first == second
    changed = build_reading_units(
        {"chapters": [{"chapter_id": "body", "title": "Body", "markdown": "Alpha.\n\nGamma."}]},
        source_path=Path("book.epub"),
    )
    assert changed["document_fingerprint"] != first["document_fingerprint"]


def test_user_confirmed_projection_can_be_frozen_as_translation_authority() -> None:
    payload = build_reading_units(
        {"chapters": [{"chapter_id": "body", "title": "Body", "markdown": "Text."}]},
        source_path=Path("book.pdf"),
        translation_authority=True,
    )

    assert payload["generation"]["translation_authority"] is True
    validate_reading_units(payload)


def test_validator_rejects_duplicate_ids() -> None:
    payload = build_reading_units(
        {"chapters": [{"chapter_id": "body", "title": "Body", "markdown": "Alpha.\n\nBeta."}]},
        source_path=Path("book.pdf"),
    )
    payload["units"][1]["unit_id"] = payload["units"][0]["unit_id"]
    with pytest.raises(ValueError, match="unique"):
        validate_reading_units(payload)


def test_validator_rejects_incomplete_chapter_coverage() -> None:
    payload = build_reading_units(
        {"chapters": [{"chapter_id": "body", "title": "Body", "markdown": "Alpha.\n\nBeta."}]},
        source_path=Path("book.pdf"),
    )
    payload["chapters"][0]["unit_ids"].pop()
    with pytest.raises(ValueError, match="cover every"):
        validate_reading_units(payload)


def test_validator_rejects_precise_epub_span_without_resource_path() -> None:
    payload = build_reading_units(
        {"chapters": [{"chapter_id": "body", "title": "Body", "markdown": "Alpha."}]},
        source_path=Path("book.epub"),
    )
    payload["units"][0]["provenance"][0]["precision"] = "resource"
    with pytest.raises(ValueError, match="resource path"):
        validate_reading_units(payload)
