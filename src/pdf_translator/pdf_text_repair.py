from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Mid-word breaks from PDF column / hyphen reflow (e.g. "s ingular", "mo dal").
_BROKEN_WORD = re.compile(
    r"\b([a-z]{1,2}) ([a-z]{2,}(?: [a-z]{1,3}){0,2})\b",
    re.IGNORECASE,
)
_ORPHAN_FOOTNOTE_AFTER_SENTENCE = re.compile(r"\. y\.(?=\s|$)", re.IGNORECASE)
_STANDALONE_FOOTNOTE_LINE = re.compile(r"^\s*y\.\s*$", re.IGNORECASE | re.MULTILINE)
_HEADING_TRAILING_FOOTNOTE = re.compile(r"^(#{1,6}\s+.+?)\s+y\.\s*$", re.IGNORECASE | re.MULTILINE)
_GLUE_FORMALS = re.compile(r"\bformalsystems\b", re.IGNORECASE)
_GLUE_EACHOF = re.compile(r"\bEachofthe\b", re.IGNORECASE)
_SPACED_OF_QUOTE = re.compile(r"of'\s*")
_DOUBLE_SPACED_WORD = re.compile(r"\b([a-z]+)  +([a-z]+)\b", re.IGNORECASE)
_LOGIC_SYMBOL_LINE = re.compile(r"[◻◇φ∀∃⊢⊨≤≥]")

INGEST_ISSUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("orphan_footnote_y", re.compile(r"\. y\.|^\s*y\.\s*$", re.IGNORECASE | re.MULTILINE)),
    ("midword_space", re.compile(r"\b[a-z] [a-z]{2,}\b", re.IGNORECASE)),
    ("glued_words", re.compile(r"\bformalsystems\b", re.IGNORECASE)),
)


@dataclass(slots=True)
class IngestQualityReport:
    issue_counts: dict[str, int]
    total_chars: int
    issue_rate_per_1k: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "ingest_quality_report_v1",
            "issue_counts": self.issue_counts,
            "total_chars": self.total_chars,
            "issue_rate_per_1k": round(self.issue_rate_per_1k, 3),
            "acceptable": self.issue_rate_per_1k <= 2.0,
        }


def _merge_broken_word(match: re.Match[str]) -> str:
    prefix = match.group(1)
    rest = match.group(2).replace(" ", "")
    merged = f"{prefix}{rest}"
    if len(merged) < 4 or len(merged) > 28:
        return match.group(0)
    if not merged.isalpha():
        return match.group(0)
    return merged


def repair_pdf_markdown(text: str) -> str:
    if not text:
        return text
    repaired = text
    repaired = _BROKEN_WORD.sub(_merge_broken_word, repaired)
    repaired = _ORPHAN_FOOTNOTE_AFTER_SENTENCE.sub(".", repaired)
    repaired = _STANDALONE_FOOTNOTE_LINE.sub("", repaired)
    repaired = _HEADING_TRAILING_FOOTNOTE.sub(r"\1", repaired)
    repaired = _GLUE_FORMALS.sub("formal systems", repaired)
    repaired = _GLUE_EACHOF.sub("Each of the", repaired)
    repaired = _SPACED_OF_QUOTE.sub("of' ", repaired)
    repaired = _DOUBLE_SPACED_WORD.sub(r"\1 \2", repaired)
    repaired = re.sub(r"\n{3,}", "\n\n", repaired)
    return repaired.strip() + ("\n" if text.endswith("\n") else "")


def scan_ingest_quality(text: str) -> IngestQualityReport:
    issue_counts: dict[str, int] = {}
    for name, pattern in INGEST_ISSUE_PATTERNS:
        issue_counts[name] = len(pattern.findall(text))
    total_chars = max(len(text), 1)
    total_issues = sum(issue_counts.values())
    return IngestQualityReport(
        issue_counts=issue_counts,
        total_chars=total_chars,
        issue_rate_per_1k=(total_issues / total_chars) * 1000.0,
    )


def repair_book_dict(book: dict[str, Any]) -> dict[str, Any]:
    book = dict(book)
    chapters = book.get("chapters")
    if not isinstance(chapters, list):
        return book
    repaired_chapters: list[dict[str, Any]] = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            repaired_chapters.append(chapter)
            continue
        entry = dict(chapter)
        for key in ("markdown", "trace_markdown"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = repair_pdf_markdown(value)
        repaired_chapters.append(entry)
    book["chapters"] = repaired_chapters
    if isinstance(book.get("full_markdown"), str):
        book["full_markdown"] = repair_pdf_markdown(book["full_markdown"])
    if isinstance(book.get("trace_markdown"), str):
        book["trace_markdown"] = repair_pdf_markdown(book["trace_markdown"])
    return book


def write_ingest_quality_report(run_dir: Path, *, source_markdown: str) -> Path:
    report = scan_ingest_quality(source_markdown)
    path = run_dir / "ingest-quality-report.json"
    path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
