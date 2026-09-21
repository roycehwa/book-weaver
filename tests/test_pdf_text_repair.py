from __future__ import annotations

import json

from pdf_translator.pdf_text_repair import (
    repair_pdf_markdown,
    scan_ingest_quality,
    write_ingest_quality_report,
)


def test_repair_pdf_markdown_fixes_common_extraction_artifacts() -> None:
    source = (
        "Examples of this kind have been used to argue against the conception of "
        "that-clauses as s ingular terms.\n\n"
        "## It is future that Nigel is in Norway. y.\n"
        "Eachofthe modal operators ◻ and ◇ behave syntactically like formalsystems."
    )
    repaired = repair_pdf_markdown(source)
    assert "singular terms" in repaired
    assert ". y." not in repaired
    assert "formal systems" in repaired
    assert "Each of the" in repaired


def test_repair_does_not_glue_normal_short_words():
    text = "In the beginning we saw a book and a rise in interest. It is in the room, on a desk, as an example of the method, from t to x."
    assert repair_pdf_markdown(text) == text


def test_repair_joins_high_confidence_single_glyph_word_splits() -> None:
    text = (
        "Husserl is central here. Henry e mphasizes that an d hundreds of examples c ould clarify the point. "
        "Hus s erl describes the pri mar y circumstances a s pathos and praxis."
    )

    repaired = repair_pdf_markdown(text)

    assert "emphasizes" in repaired
    assert "and hundreds" in repaired
    assert "could" in repaired
    assert "Husserl" in repaired
    assert "primary" in repaired
    assert "as pathos" in repaired


def test_repair_uses_book_local_evidence_for_uncommon_multi_fragment_words() -> None:
    text = "Modal logic is discussed first. Later the same mo dal operator returns."

    repaired = repair_pdf_markdown(text)

    assert "same modal operator" in repaired


def test_repair_joins_uncommon_word_after_stray_single_glyph() -> None:
    repaired = repair_pdf_markdown(
        "Birth is an interesting c oncatenation of necessity and contingency."
    )

    assert "interesting concatenation" in repaired


def test_scan_ingest_quality_reports_issues() -> None:
    report = scan_ingest_quality("that-clauses as s ingular terms. y.")
    assert report.issue_counts["midword_space"] >= 1
    assert report.issue_rate_per_1k > 0


def test_scan_ingest_quality_does_not_treat_normal_one_letter_words_as_corruption() -> None:
    report = scan_ingest_quality("I set off as a student a few years ago.")

    assert report.issue_counts["midword_space"] == 0
    assert report.blocking_issues == []


def test_scan_ingest_quality_records_blocking_character_corruption_with_evidence() -> None:
    report = scan_ingest_quality("# Preface\n\nA bro\u00adken word and a replacement \ufffd character.")

    assert report.acceptable is False
    assert {issue["code"] for issue in report.blocking_issues} == {
        "replacement_character",
        "soft_hyphen",
    }
    assert all(issue["line"] == 3 for issue in report.blocking_issues)
    assert all(issue["chapter"] == "Preface" for issue in report.blocking_issues)
    assert all(issue["excerpt"] for issue in report.blocking_issues)


def test_scan_ingest_quality_keeps_ambiguous_hyphenated_break_nonblocking() -> None:
    report = scan_ingest_quality("# Chapter\n\nsteadfast sup-\n\nnevitably changed")

    assert report.acceptable is True
    assert report.blocking_issues == []
    assert report.warning_issues[0]["code"] == "hyphenated_line_break"
    assert report.warning_issues[0]["chapter"] == "Chapter"
    assert report.warning_issues[0]["line"] == 3
    assert "sup-\\n\\nnevitably" in report.warning_issues[0]["excerpt"]


def test_repair_resolves_lexical_line_wrap_hyphens_and_preserves_compounds() -> None:
    repaired = repair_pdf_markdown(
        "A trans-\nformation needs steadfast sup-\n\nport and self-\nconscious care."
    )

    assert "transformation" in repaired
    assert "support" in repaired
    assert "self-conscious" in repaired
    assert scan_ingest_quality(repaired).issue_counts["hyphenated_line_break"] == 0


def test_repair_does_not_guess_when_broken_fragments_have_no_lexical_evidence() -> None:
    source = "forever grateful for steadfast sup-\n\nnevitably changed"

    repaired = repair_pdf_markdown(source)

    assert repaired == source
    report = scan_ingest_quality(repaired)
    assert report.acceptable is True
    assert report.issue_counts["hyphenated_line_break"] == 1


def test_ingest_quality_report_maps_issue_to_pdf_page_and_block(tmp_path) -> None:
    path = write_ingest_quality_report(
        tmp_path,
        source_markdown="# Acknowledgments\n\nsteadfast sup-\n\nnevitably changed",
        page_texts={338: "## Acknowledgments\n\nsteadfast sup-\n\nnevitably changed"},
        source_format="pdf",
    )

    report = json.loads(path.read_text(encoding="utf-8"))
    issue = report["warning_issues"][0]
    assert issue["page"] == 338
    assert issue["block_index"] == 2
    assert "steadfast sup-" in issue["block_excerpt"]
    assert issue["source_format"] == "pdf"


def test_ingest_quality_report_maps_repeated_issues_in_reading_order(tmp_path) -> None:
    path = write_ingest_quality_report(
        tmp_path,
        source_markdown="bad \ufffd first\n\nbad \ufffd second",
        page_texts={7: "bad \ufffd first", 8: "bad \ufffd second"},
        source_format="pdf",
    )

    report = json.loads(path.read_text(encoding="utf-8"))
    assert [issue["page"] for issue in report["blocking_issues"]] == [7, 8]
    assert [issue["block_index"] for issue in report["blocking_issues"]] == [1, 1]


def test_repair_removes_structural_flow_markers_regardless_of_glyph() -> None:
    source = (
        "The argument ends here. -q-\n\n"
        "the next paragraph continues the thought.\n\n"
        "The next paragraph ends here. -m-\n\n"
        "more prose continues below.\n\n"
        "A third paragraph ends here. ---z-\n\n"
        "still more lowercase continuation.\n\n"
        "The involuntary r -- and voluntary remain distinct.\n\n"
        "Let -x- denote the inverse in this sentence.\n\n"
        "The variable x-\n\n"
        "A real trans-\nformation break remains."
    )

    repaired = repair_pdf_markdown(source)

    assert "-q-" not in repaired
    assert "-m-" not in repaired
    assert "---z-" not in repaired
    assert "r --" not in repaired
    assert "involuntary and voluntary" in repaired
    assert "Let -x- denote" in repaired
    assert "The variable x-" in repaired
    assert "transformation" in repaired
    report = scan_ingest_quality(repaired)
    assert report.issue_counts["hyphenated_line_break"] == 0


def test_repair_removes_docling_flow_marker_before_lowercase_paragraph_break() -> None:
    source = (
        "The division—namely, -x-\n\n"
        "the subjective side of the argument continues.\n\n"
        "This description -x-\n\n"
        "into question remains readable after cleanup."
    )

    repaired = repair_pdf_markdown(source)

    assert "-x-" not in repaired
    assert "division—namely, the subjective" in repaired
    assert "This description into question" in repaired
    assert "Let -x- denote" in repair_pdf_markdown("Let -x- denote a placeholder.\n\n")
    assert "The variable x-\n\naxis" in repair_pdf_markdown("The variable x-\n\naxis remains explicit.")
    report = scan_ingest_quality(repaired)
    assert report.issue_counts["hyphenated_line_break"] == 0
    assert report.blocking_issues == []


def test_scan_ignores_docling_flow_marker_hyphen_before_lowercase_paragraph() -> None:
    unrepaired = "description -x-\n\ninto question"

    report = scan_ingest_quality(unrepaired)

    assert report.issue_counts["hyphenated_line_break"] == 0
