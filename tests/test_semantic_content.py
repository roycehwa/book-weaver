from __future__ import annotations

from pdf_translator.semantic_content import (
    SemanticContentError,
    build_semantic_footnote,
    stable_semantic_id,
)


def test_mixed_note_is_split_without_losing_source_text() -> None:
    source = (
        "For the argument, see Charles Tilly, "
        "Coercion, Capital, and European States, pp. 20–22."
    )

    note = build_semantic_footnote(
        page_no=12,
        marker="14",
        raw_text=source,
        bbox=[72.0, 650.0, 520.0, 710.0],
    )

    assert note["footnote_id"] == stable_semantic_id("footnote", 12, "14", source)
    assert [span["kind"] for span in note["spans"]] == ["prose", "citation"]
    assert "".join(span["source_text"] for span in note["spans"]) == source
    assert note["source_bboxes"] == [[72.0, 650.0, 520.0, 710.0]]


def test_citation_only_note_remains_bibliographic() -> None:
    source = "Charles Tilly, Coercion, Capital, and European States, pp. 20–22."

    note = build_semantic_footnote(page_no=12, marker="15", raw_text=source)

    assert [span["kind"] for span in note["spans"]] == ["citation"]
    assert note["spans"][0]["translatable"] is False


def test_stable_id_ignores_inconsequential_spacing() -> None:
    first = stable_semantic_id("footnote", 12, "14", "For the argument, see  Tilly.")
    second = stable_semantic_id("footnote", 12, "14", "For the argument, see Tilly.")

    assert first == second


def test_empty_note_is_rejected() -> None:
    try:
        build_semantic_footnote(page_no=12, marker="14", raw_text="  ")
    except SemanticContentError as exc:
        assert "empty footnote" in str(exc)
    else:
        raise AssertionError("empty footnote should be rejected")
