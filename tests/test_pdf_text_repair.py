from __future__ import annotations

from pdf_translator.pdf_text_repair import repair_pdf_markdown, scan_ingest_quality


def test_repair_pdf_markdown_fixes_common_extraction_artifacts() -> None:
    source = (
        "Examples of this kind have been used to argue against the conception of "
        "that-clauses as s ingular terms.\n\n"
        "## It is future that Nigel is in Norway. y.\n"
        "Eachofthe modal operators ◻ and ◇ behave syntactically like formalsystems."
    )
    repaired = repair_pdf_markdown(source)
    assert "singular terms" in repaired
    assert "s ingular" not in repaired
    assert ". y." not in repaired
    assert "formal systems" in repaired
    assert "Each of the" in repaired


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


def test_scan_ingest_quality_blocks_unrepaired_hyphenated_line_break() -> None:
    report = scan_ingest_quality("# Chapter\n\ntrans-\nformation failed")

    assert report.acceptable is False
    assert report.blocking_issues[0]["code"] == "hyphenated_line_break"
    assert report.blocking_issues[0]["chapter"] == "Chapter"
    assert report.blocking_issues[0]["line"] == 3
    assert "trans-\\nformation" in report.blocking_issues[0]["excerpt"]
