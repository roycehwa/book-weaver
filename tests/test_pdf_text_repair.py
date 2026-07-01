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
