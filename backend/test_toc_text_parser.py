from __future__ import annotations

from toc_text_parser import (
    detect_toc_page_range,
    entries_to_chapters,
    extract_text_toc_from_pdf,
    filter_toc_entries,
    infer_page_offset,
    parse_multiline_toc_lines,
    parse_toc_text,
    TocTextEntry,
)

SAMPLE_TOC = """
Contents
Preface
1
1
Aims and Ends
5
1.1
The Quick Road to Paradox
7
1.2
The Direct Way to Paradox
11
2
Technical Preliminaries
13
3
Predicates and Conceptual Analysis
25
"""


def test_parse_multiline_toc_text() -> None:
    entries = parse_toc_text(SAMPLE_TOC)
    assert len(entries) >= 4
    assert entries[0].title == "Preface"
    assert entries[0].printed_page == 1
    assert any(entry.title.endswith("Aims and Ends") or entry.title == "1 Aims and Ends" for entry in entries)


def test_filter_top_level_entries() -> None:
    entries = parse_toc_text(SAMPLE_TOC)
    filtered = filter_toc_entries(entries, max_depth=1)
    titles = [entry.title for entry in filtered]
    assert "Preface" in titles
    assert not any(title.startswith("1.1") for title in titles)


def test_entries_to_chapters_applies_offset() -> None:
    entries = [
        TocTextEntry(title="Preface", printed_page=1),
        TocTextEntry(title="Chapter One", printed_page=5, section="1"),
    ]
    chapters = entries_to_chapters(entries, page_offset=11, total_pages=200)
    assert chapters[0]["start_page"] == 12
    assert chapters[1]["start_page"] == 16


def test_entries_to_chapters_parent_and_section_share_start_page() -> None:
    entries = [
        TocTextEntry(title="Chapter One", printed_page=1, section="1"),
        TocTextEntry(title="Section One", printed_page=1, section="1.1"),
        TocTextEntry(title="Section Two", printed_page=5, section="1.2"),
        TocTextEntry(title="Chapter Two", printed_page=10, section="2"),
    ]
    chapters = entries_to_chapters(entries, page_offset=0, total_pages=100)
    assert chapters[0]["start_page"] == 1
    assert chapters[0]["end_page"] == 9
    assert chapters[1]["start_page"] == 1
    assert chapters[1]["end_page"] == 4
    assert chapters[2]["start_page"] == 5
    assert chapters[2]["end_page"] == 9


def test_detect_toc_page_range_on_sample_pdf(tmp_path) -> None:
    fitz = __import__("fitz")
    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), SAMPLE_TOC)
    doc.save(str(pdf_path))
    doc.close()

    reopened = fitz.open(str(pdf_path))
    detected = detect_toc_page_range(reopened)
    reopened.close()
    assert detected == (1, 1)

    result = extract_text_toc_from_pdf(str(pdf_path), page_start=1, page_end=1, page_offset=0)
    assert result is not None
    assert len(result["chapters"]) >= 2


def test_infer_page_offset_skips_toc_pages() -> None:
    fitz = __import__("fitz")
    doc = fitz.open()
    toc_page = doc.new_page()
    toc_page.insert_text((72, 72), "Contents\nPreface\n1")
    body_page = doc.new_page()
    body_page.insert_text((72, 72), "Preface\nChapter body")
    entries = [TocTextEntry(title="Preface", printed_page=1)]
    offset = infer_page_offset(doc, entries, toc_page_start=1, toc_page_end=1)
    doc.close()
    assert offset == 1
