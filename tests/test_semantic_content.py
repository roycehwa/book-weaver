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


def test_explanatory_cue_is_prose_before_single_author_citation() -> None:
    source = (
        "For example, Lüthi, The Sino–Soviet Split; "
        "Radchenko, Two Suns in the Heavens."
    )

    note = build_semantic_footnote(page_no=21, marker="9", raw_text=source)

    assert [span["kind"] for span in note["spans"]] == ["prose", "citation"]
    assert note["spans"][0]["source_text"] == "For example, "
    assert note["spans"][1]["source_text"].startswith("Lüthi,")


def test_short_quoted_foreign_title_is_citation_only() -> None:
    source = '"Suiyue huimou: zhengrong suiyue cong zheli kaishi."'

    note = build_semantic_footnote(page_no=17, marker="4", raw_text=source)

    assert [span["kind"] for span in note["spans"]] == ["citation"]


def test_author_title_list_without_page_number_is_citation_only() -> None:
    source = (
        "Bian, The Making of the State Enterprise System in Modern China; "
        "Kubo, Gendai Chūgoku no genkei no shutsugen."
    )

    note = build_semantic_footnote(page_no=24, marker="22", raw_text=source)

    assert [span["kind"] for span in note["spans"]] == ["citation"]


def test_initial_citation_and_trailing_explanation_are_separate() -> None:
    source = (
        "Guojia tongji ju, Zhongguo gongye jingji tongji ziliao, 31. "
        "The share of state ownership includes public-private cooperatives."
    )

    note = build_semantic_footnote(page_no=22, marker="14", raw_text=source)

    assert [span["kind"] for span in note["spans"]] == ["citation", "prose"]
    assert "".join(span["source_text"] for span in note["spans"]) == source
