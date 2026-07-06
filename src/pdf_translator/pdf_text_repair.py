from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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
    (
        "midword_space",
        re.compile(r"(?<![A-Za-z'’])\b[b-hj-z] [a-z]{3,}\b", re.IGNORECASE),
    ),
    ("glued_words", re.compile(r"\bformalsystems\b", re.IGNORECASE)),
)
BLOCKING_INGEST_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("replacement_character", re.compile("\ufffd")),
    ("soft_hyphen", re.compile("\u00ad")),
    ("hyphenated_line_break", re.compile(r"(?<=[^\W\d_])-\s*\n\s*(?=[^\W\d_])", re.UNICODE)),
    ("control_character", re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")),
)


class IngestQualityError(ValueError):
    pass


@dataclass(slots=True)
class IngestQualityReport:
    issue_counts: dict[str, int]
    total_chars: int
    issue_rate_per_1k: float
    blocking_issues: list[dict[str, Any]] = field(default_factory=list)
    warning_issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def acceptable(self) -> bool:
        return not self.blocking_issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "ingest_quality_report_v1",
            "issue_counts": self.issue_counts,
            "total_chars": self.total_chars,
            "issue_rate_per_1k": round(self.issue_rate_per_1k, 3),
            "acceptable": self.acceptable,
            "blocking_issues": self.blocking_issues,
            "warning_issues": self.warning_issues,
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
    for name, pattern in BLOCKING_INGEST_PATTERNS:
        issue_counts[name] = len(pattern.findall(text))
    total_chars = max(len(text), 1)
    total_issues = sum(issue_counts.values())
    blocking_issues = _quality_evidence(text, BLOCKING_INGEST_PATTERNS)
    warning_issues = _quality_evidence(text, INGEST_ISSUE_PATTERNS)
    return IngestQualityReport(
        issue_counts=issue_counts,
        total_chars=total_chars,
        issue_rate_per_1k=(total_issues / total_chars) * 1000.0,
        blocking_issues=blocking_issues,
        warning_issues=warning_issues,
    )


def _quality_evidence(
    text: str,
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    headings = [
        (match.start(), match.group(1))
        for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", text, flags=re.MULTILINE)
    ]
    for code, pattern in patterns:
        for match in pattern.finditer(text):
            chapter = next(
                (title for offset, title in reversed(headings) if offset <= match.start()),
                None,
            )
            start = max(0, match.start() - 48)
            end = min(len(text), match.end() + 48)
            excerpt = text[start:end].replace("\n", "\\n")
            evidence.append(
                {
                    "code": code,
                    "chapter": chapter,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "excerpt": excerpt,
                }
            )
            if len(evidence) >= 100:
                return evidence
    return evidence


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


def write_ingest_quality_report(
    run_dir: Path,
    *,
    source_markdown: str,
    block_on_errors: bool = False,
) -> Path:
    report = scan_ingest_quality(source_markdown)
    path = run_dir / "ingest-quality-report.json"
    path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if block_on_errors and not report.acceptable:
        codes = sorted({str(issue["code"]) for issue in report.blocking_issues})
        raise IngestQualityError(
            "EPUB ingest quality gate blocked downstream processing: "
            + ", ".join(codes)
            + f". See {path}."
        )
    return path
