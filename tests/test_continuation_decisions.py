from pathlib import Path

from pdf_translator.book_rebuild import build_book_reconstruction
from pdf_translator.continuation_decisions import validate_continuation_decisions
from pdf_translator.reading_units import build_reading_units


def _prov(page_no: int, left: float, top: float, *, bottom: float | None = None, right: float = 500) -> list[dict]:
    return [{
        "page_no": page_no,
        "bbox": {"l": left, "t": top, "b": bottom if bottom is not None else top - 20, "r": right},
    }]


def _docling_pages(height: float = 792.0, width: float = 612.0, count: int = 2) -> dict[str, dict]:
    return {
        str(page_no): {"size": {"width": width, "height": height}}
        for page_no in range(1, count + 1)
    }


def test_cross_node_true_continuation_merges_and_records_acceptance() -> None:
    structured = {
        "pages": _docling_pages(),
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {
                "label": "text",
                "text": "The movement began to gather momentum across the",
                "prov": _prov(1, 50, 320, bottom=280),
            },
            {
                "label": "text",
                "text": "region during the following decade.",
                "prov": _prov(2, 50, 680, bottom=640),
            },
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert result["chapters"][0]["markdown"] == (
        "The movement began to gather momentum across the region during the following decade.\n"
    )
    ledger = result["continuation_decisions"]
    validate_continuation_decisions(ledger)
    decision = ledger["decisions"][0]
    assert decision["status"] == "accepted"
    assert decision["kind"] == "cross_source_node"
    assert decision["left_source_node_id"] != decision["right_source_node_id"]


def test_cross_node_paragraph_break_is_rejected() -> None:
    structured = {
        "pages": _docling_pages(),
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {
                "label": "text",
                "text": "A complete paragraph ends here.",
                "prov": _prov(1, 50, 320, bottom=280),
            },
            {
                "label": "text",
                "text": "another paragraph begins.",
                "prov": _prov(2, 50, 680, bottom=640),
            },
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert "A complete paragraph ends here.\n\nanother paragraph begins." in result["chapters"][0]["markdown"]
    assert result["continuation_decisions"]["decisions"][0]["status"] == "rejected"
    assert "terminal_punctuation" in result["continuation_decisions"]["decisions"][0]["reasons"]


def test_cross_node_multi_column_mismatch_is_rejected() -> None:
    structured = {
        "pages": _docling_pages(),
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {
                "label": "text",
                "text": "Left column prose continues without ending",
                "prov": _prov(1, 50, 320, bottom=280, right=260),
            },
            {
                "label": "text",
                "text": "in the right column on the next page.",
                "prov": _prov(2, 320, 680, bottom=640, right=520),
            },
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert result["continuation_decisions"]["decisions"][0]["status"] == "rejected"
    assert "column_or_alignment_mismatch" in result["continuation_decisions"]["decisions"][0]["reasons"]


def test_page_header_noise_does_not_merge_with_body() -> None:
    structured = {
        "pages": _docling_pages(),
        "body": {"children": [{"$ref": f"#/texts/{index}"} for index in range(3)]},
        "texts": [
            {
                "label": "text",
                "text": "Body paragraph continues without ending",
                "prov": _prov(1, 50, 320, bottom=280),
            },
            {
                "label": "page_header",
                "text": "Running Header",
                "prov": _prov(2, 80, 612, bottom=590),
            },
            {
                "label": "text",
                "text": "and resumes after the running header.",
                "prov": _prov(2, 50, 560, bottom=520),
            },
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    decision = result["continuation_decisions"]["decisions"][0]
    assert decision["right_source_node_id"] == "#/texts/2"
    assert decision["status"] == "rejected"
    assert "page_boundary_noise" in decision["reasons"]


def test_cross_node_hyphenation_merge() -> None:
    structured = {
        "pages": _docling_pages(),
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {
                "label": "text",
                "text": "A revolution-",
                "prov": _prov(1, 50, 320, bottom=280),
            },
            {
                "label": "text",
                "text": "ary movement emerged.",
                "prov": _prov(2, 50, 680, bottom=640),
            },
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert result["chapters"][0]["markdown"] == "A revolutionary movement emerged.\n"
    assert result["continuation_decisions"]["decisions"][0]["status"] == "accepted"
    assert result["continuation_decisions"]["decisions"][0]["evidence"]["repair"] == "page_break_hyphen_removed"


def test_reading_units_keep_combined_cross_node_provenance() -> None:
    structured = {
        "pages": _docling_pages(),
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {
                "label": "text",
                "text": "The movement began to gather momentum across the",
                "prov": [{"page_no": 1, "charspan": [0, 54], "bbox": {"l": 50, "t": 320, "b": 280, "r": 500}}],
            },
            {
                "label": "text",
                "text": "region during the following decade.",
                "prov": [{"page_no": 2, "charspan": [0, 35], "bbox": {"l": 50, "t": 680, "b": 640, "r": 500}}],
            },
        ],
        "pictures": [],
        "tables": [],
    }
    book = build_book_reconstruction(structured)
    payload = build_reading_units(book, source_path=Path("book.pdf"))
    paragraph = next(unit for unit in payload["units"] if unit["kind"] == "paragraph")
    joined = "The movement began to gather momentum across the region during the following decade."
    assert paragraph["markdown"] == joined
    assert paragraph["boundary_before"] in {"chapter", "paragraph"}
    assert paragraph["continuation_decision"]["kind"] == "cross_source_node"
    assert len(paragraph["provenance"]) == 2
    assert paragraph["provenance"][0]["source_node_id"] == "#/texts/0"
    assert paragraph["provenance"][1]["source_node_id"] == "#/texts/1"


def test_ledger_and_fingerprints_are_stable_for_unchanged_input() -> None:
    structured = {
        "pages": _docling_pages(),
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {
                "label": "text",
                "text": "The movement began to gather momentum across the",
                "prov": _prov(1, 50, 320, bottom=280),
            },
            {
                "label": "text",
                "text": "region during the following decade.",
                "prov": _prov(2, 50, 680, bottom=640),
            },
        ],
        "pictures": [],
        "tables": [],
    }
    first = build_book_reconstruction(structured)
    second = build_book_reconstruction(structured)
    assert first["continuation_decisions"] == second["continuation_decisions"]
    units_first = build_reading_units(first, source_path=Path("book.pdf"))
    units_second = build_reading_units(second, source_path=Path("book.pdf"))
    assert units_first["document_fingerprint"] == units_second["document_fingerprint"]
    assert [unit["unit_id"] for unit in units_first["units"]] == [
        unit["unit_id"] for unit in units_second["units"]
    ]


def test_same_source_node_output_remains_stable() -> None:
    structured = {
        "pages": _docling_pages(),
        "body": {"children": [{"$ref": "#/texts/0"}]},
        "texts": [
            {
                "label": "text",
                "text": "A revolution- ary movement emerged.",
                "prov": [
                    {"page_no": 1, "charspan": [0, 13], "bbox": {"l": 50, "t": 600, "b": 580, "r": 500}},
                    {"page_no": 2, "charspan": [14, 35], "bbox": {"l": 50, "t": 700, "b": 680, "r": 500}},
                ],
            }
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert result["chapters"][0]["markdown"] == "A revolutionary movement emerged.\n"
    assert len(result["logical_continuations"]) == 1
    assert result["logical_continuations"][0]["repair"] == "page_break_hyphen_removed"
    assert result["continuation_decisions"]["decisions"][0]["status"] == "accepted"
    assert result["continuation_decisions"]["decisions"][0]["kind"] == "same_source_node"


def test_page_relative_geometry_accepts_equivalent_layout_on_scaled_pages() -> None:
    def scaled_structured(scale: float) -> dict:
        return {
            "pages": _docling_pages(height=792.0 * scale, width=612.0 * scale),
            "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
            "texts": [
                {
                    "label": "text",
                    "text": "The movement began to gather momentum across the",
                    "prov": _prov(1, 50 * scale, 320 * scale, bottom=280 * scale, right=500 * scale),
                },
                {
                    "label": "text",
                    "text": "region during the following decade.",
                    "prov": _prov(2, 50 * scale, 680 * scale, bottom=640 * scale, right=500 * scale),
                },
            ],
            "pictures": [],
            "tables": [],
        }

    baseline = build_book_reconstruction(scaled_structured(1.0))
    scaled = build_book_reconstruction(scaled_structured(2.0))

    assert baseline["chapters"][0]["markdown"] == scaled["chapters"][0]["markdown"]
    assert baseline["continuation_decisions"]["decisions"][0]["status"] == "accepted"
    assert scaled["continuation_decisions"]["decisions"][0]["status"] == "accepted"


def test_missing_page_dimensions_fail_closed_for_cross_node_merge() -> None:
    structured = {
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {
                "label": "text",
                "text": "The movement began to gather momentum across the",
                "prov": _prov(1, 50, 320, bottom=280),
            },
            {
                "label": "text",
                "text": "region during the following decade.",
                "prov": _prov(2, 50, 680, bottom=640),
            },
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    decision = result["continuation_decisions"]["decisions"][0]
    assert decision["status"] == "rejected"
    assert "missing_page_dimensions" in decision["reasons"]
    assert "The movement began" in result["chapters"][0]["markdown"]
    assert "region during" in result["chapters"][0]["markdown"]


def test_ledger_fingerprint_changes_when_joined_text_changes() -> None:
    def structured_for(joined_suffix: str) -> dict:
        return {
            "pages": _docling_pages(),
            "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
            "texts": [
                {
                    "label": "text",
                    "text": "The movement began to gather momentum across the",
                    "prov": _prov(1, 50, 320, bottom=280),
                },
                {
                    "label": "text",
                    "text": f"region during the following decade{joined_suffix}",
                    "prov": _prov(2, 50, 680, bottom=640),
                },
            ],
            "pictures": [],
            "tables": [],
        }

    first = build_book_reconstruction(structured_for(""))
    second = build_book_reconstruction(structured_for("."))

    assert (
        first["continuation_decisions"]["document_fingerprint"]
        != second["continuation_decisions"]["document_fingerprint"]
    )
