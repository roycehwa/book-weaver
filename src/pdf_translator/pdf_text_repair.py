from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wordfreq import zipf_frequency


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
# Docling can leak a single glyph wrapped in dashes at a flow boundary.  The
# glyph itself is not stable across books, so detection uses layout and syntax:
# a marker must occupy its own line, follow completed prose at a paragraph
# boundary, or interrupt a coordination with a double dash.
_SINGLE_GLYPH_DASH_FLOW_MARKER = r"-{0,3}[A-Za-z]\s*-{1,3}"
_WRAPPED_SINGLE_GLYPH_DASH_FLOW_MARKER = r"-{1,3}[A-Za-z]\s*-{1,3}"
_STANDALONE_FLOW_MARKER = re.compile(
    rf"^\s*{_SINGLE_GLYPH_DASH_FLOW_MARKER}\s*$",
    re.MULTILINE,
)
_SENTENCE_END_FLOW_MARKER = re.compile(
    rf"(?<=[.!?])\s+{_SINGLE_GLYPH_DASH_FLOW_MARKER}(?=[ \t]*(?:\n\s*\n|\Z))",
    re.MULTILINE,
)
_PROSE_PARAGRAPH_FLOW_MARKER = re.compile(
    rf"(?<=\S)[ \t]+{_WRAPPED_SINGLE_GLYPH_DASH_FLOW_MARKER}"
    r"[ \t]*\n[ \t]*\n[ \t]*(?=[a-z])",
    re.MULTILINE,
)
_COORDINATION_FLOW_MARKER = re.compile(
    r"(?<=\w)\s+[A-Za-z]\s+--\s+(?=(?:and|or|nor|but|yet|so)\b)",
    re.IGNORECASE,
)
_WORD_TOKEN = re.compile(r"[A-Za-z]+")
_HYPHENATED_LINE_BREAK = re.compile(
    r"(?<![A-Za-z-])([A-Za-z]{2,})-\s*\n+\s*([a-z]{2,})(?![A-Za-z])"
)

INGEST_ISSUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("orphan_footnote_y", re.compile(r"\. y\.|^\s*y\.\s*$", re.IGNORECASE | re.MULTILINE)),
    (
        "midword_space",
        re.compile(r"(?<![A-Za-z'’])\b[b-hj-z] [a-z]{3,}\b"),
    ),
    ("glued_words", re.compile(r"\bformalsystems\b", re.IGNORECASE)),
    # A text-only scan cannot prove whether this is a broken word, a valid
    # hyphenated compound, or a page extraction gap.  High-confidence cases
    # are repaired first; residual cases remain visible but cannot freeze the
    # entire book after chapter confirmation.
    ("hyphenated_line_break", _HYPHENATED_LINE_BREAK),
)
BLOCKING_INGEST_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("replacement_character", re.compile("\ufffd")),
    ("soft_hyphen", re.compile("\u00ad")),
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


def _known_words(texts: list[str]) -> set[str]:
    """Return intact words observed elsewhere in this book.

    This evidence is deliberately book-local.  It supports names and technical
    vocabulary without a growing list of fixes for individual books.
    """
    words: set[str] = set()
    for text in texts:
        words.update(token.group(0).casefold() for token in _WORD_TOKEN.finditer(text))
    return words


def _split_has_word_evidence(values: list[str], joined: str, known_words: set[str]) -> bool:
    """Require lexical evidence that joining is safer than preserving spaces."""
    joined_score = zipf_frequency(joined, "en")
    phrase_score = zipf_frequency(" ".join(values), "en")
    gain = joined_score - phrase_score
    lowered = [value.casefold() for value in values]
    suspicious_singleton = any(len(value) == 1 and value not in {"a", "i"} for value in lowered)
    short_fragment = any(len(value) <= 2 and value not in {"a", "i"} for value in lowered)
    rare_fragment = any(len(value) > 1 and zipf_frequency(value, "en") < 3.0 for value in lowered)
    all_singletons = all(len(value) == 1 for value in lowered)

    # Exact intact use elsewhere in the same book is the strongest evidence,
    # but still needs a fragment-shaped split to avoid merging open compounds.
    corpus_shape_evidence = (
        suspicious_singleton
        or rare_fragment
        or gain >= 1.5
        or (short_fragment and phrase_score <= joined_score + 0.25)
    )
    if joined.casefold() in known_words and corpus_shape_evidence:
        return joined_score >= 1.5
    if all_singletons:
        return joined_score >= 3.0 and gain >= 0.75
    if suspicious_singleton:
        # A stray consonant followed by a non-word remainder is strong PDF
        # glyph-split evidence even for uncommon scholarly vocabulary (for
        # example ``c oncatenation``).  Common valid one-letter words were
        # excluded above.
        return joined_score >= 2.0 and gain >= 1.25
    return joined_score >= 2.5 and gain >= 1.0 and (suspicious_singleton or rare_fragment)


def _repair_split_words(text: str, known_words: set[str]) -> str:
    """Join high-confidence glyph splits using corpus and language evidence."""
    tokens = list(_WORD_TOKEN.finditer(text))
    candidates: list[tuple[int, int, str]] = []
    for width in (3, 2):
        for index in range(0, len(tokens) - width + 1):
            window = tokens[index:index + width]
            if any(text[left.end():right.start()] != " " for left, right in zip(window, window[1:])):
                continue
            values = [token.group(0) for token in window]
            if width == 2 and values[0].casefold() in {"a", "i"} and len(values[1]) > 1:
                continue
            joined = "".join(values)
            if not 2 <= len(joined) <= 32:
                continue
            if _split_has_word_evidence(values, joined, known_words):
                candidates.append((window[0].start(), window[-1].end(), joined))

    accepted: list[tuple[int, int, str]] = []
    for start, end, joined in sorted(candidates, key=lambda item: (-(item[1] - item[0]), item[0])):
        if any(start < existing_end and end > existing_start for existing_start, existing_end, _ in accepted):
            continue
        accepted.append((start, end, joined))
    for start, end, joined in sorted(accepted, reverse=True):
        text = f"{text[:start]}{joined}{text[end:]}"
    return text


def _repair_hyphenated_line_breaks(text: str, known_words: set[str]) -> str:
    """Resolve lexical line-wrap hyphens only when dictionary evidence is strong.

    The replacement may either remove the layout hyphen (``trans-\nformation``)
    or retain a genuine compound hyphen (``self-\nconscious``).  If neither
    spelling has adequate evidence, the source stays unchanged and becomes a
    nonblocking quality warning.
    """

    def replace(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        joined = f"{left}{right}"
        hyphenated = f"{left}-{right}"
        joined_score = zipf_frequency(joined, "en")
        hyphenated_score = zipf_frequency(hyphenated, "en")
        if joined.casefold() in known_words:
            return joined
        if hyphenated_score >= 2.5 and hyphenated_score > joined_score + 0.6:
            return hyphenated
        if joined_score >= 2.5 and joined_score >= hyphenated_score - 0.25:
            return joined
        return match.group(0)

    return _HYPHENATED_LINE_BREAK.sub(replace, text)


def repair_pdf_markdown(text: str, *, known_words: set[str] | None = None) -> str:
    if not text:
        return text
    repaired = text
    # Plain text cannot distinguish a broken word from valid short words
    # ("in the", "of the", "as a"). Report suspicious spacing; never guess here.
    repaired = _ORPHAN_FOOTNOTE_AFTER_SENTENCE.sub(".", repaired)
    repaired = _STANDALONE_FOOTNOTE_LINE.sub("", repaired)
    repaired = _HEADING_TRAILING_FOOTNOTE.sub(r"\1", repaired)
    repaired = _GLUE_FORMALS.sub("formal systems", repaired)
    repaired = _GLUE_EACHOF.sub("Each of the", repaired)
    repaired = _SPACED_OF_QUOTE.sub("of' ", repaired)
    repaired = _STANDALONE_FLOW_MARKER.sub("", repaired)
    repaired = _SENTENCE_END_FLOW_MARKER.sub("", repaired)
    repaired = _PROSE_PARAGRAPH_FLOW_MARKER.sub(" ", repaired)
    repaired = _COORDINATION_FLOW_MARKER.sub(" ", repaired)
    repaired = _repair_hyphenated_line_breaks(repaired, known_words or _known_words([repaired]))
    repaired = _repair_split_words(repaired, known_words or _known_words([repaired]))
    repaired = _DOUBLE_SPACED_WORD.sub(r"\1 \2", repaired)
    repaired = re.sub(r"[ \t]+(?=\n|$)", "", repaired)
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
                    "match": match.group(0),
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
    corpus = [
        value
        for chapter in chapters
        if isinstance(chapter, dict)
        for value in (chapter.get("markdown"), chapter.get("trace_markdown"))
        if isinstance(value, str)
    ]
    for key in ("full_markdown", "trace_markdown"):
        value = book.get(key)
        if isinstance(value, str):
            corpus.append(value)
    known_words = _known_words(corpus)
    repaired_chapters: list[dict[str, Any]] = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            repaired_chapters.append(chapter)
            continue
        entry = dict(chapter)
        for key in ("markdown", "trace_markdown"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = repair_pdf_markdown(value, known_words=known_words)
        repaired_chapters.append(entry)
    book["chapters"] = repaired_chapters
    if isinstance(book.get("full_markdown"), str):
        book["full_markdown"] = repair_pdf_markdown(book["full_markdown"], known_words=known_words)
    if isinstance(book.get("trace_markdown"), str):
        book["trace_markdown"] = repair_pdf_markdown(book["trace_markdown"], known_words=known_words)
    return book


def write_ingest_quality_report(
    run_dir: Path,
    *,
    source_markdown: str,
    page_texts: dict[int, str] | None = None,
    source_format: str | None = None,
    block_on_errors: bool = False,
) -> Path:
    report = scan_ingest_quality(source_markdown)
    if page_texts:
        issues = [*report.blocking_issues, *report.warning_issues]
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for issue in issues:
            needle = str(issue.get("match") or "")
            if not needle:
                continue
            grouped.setdefault((str(issue.get("code") or ""), needle), []).append(issue)

        for (_, needle), matching_issues in grouped.items():
            locations: list[tuple[int, int, str]] = []
            for page, text in sorted(page_texts.items()):
                # Find the issue in the complete page before assigning a block.
                # A broken word can itself cross a blank-line separator, so
                # looking for the complete match inside already split blocks
                # loses the page location precisely where it is most useful.
                starts: list[int] = []
                cursor = 0
                while True:
                    start = text.find(needle, cursor)
                    if start < 0:
                        break
                    starts.append(start)
                    cursor = start + max(1, len(needle))
                if not starts:
                    continue
                block_spans = [
                    (match.start(), match.end(), match.group(0).strip())
                    for match in re.finditer(r"(?ms)\S(?:.*?\S)?(?=\n\s*\n|\Z)", text)
                    if match.group(0).strip()
                ]
                for start in starts:
                    containing = next(
                        (
                            (block_index, block)
                            for block_index, (block_start, block_end, block) in enumerate(
                                block_spans,
                                start=1,
                            )
                            if block_start <= start < block_end
                        ),
                        None,
                    )
                    if containing is None:
                        continue
                    block_index, block = containing
                    locations.append((int(page), block_index, block))
            # Repeated damage is still locatable: scan results and page
            # occurrences are both in reading order, so pair them only when
            # their counts agree.  A count mismatch remains unlocated rather
            # than sending the user to the wrong page.
            if len(locations) != len(matching_issues):
                continue
            for issue, (page, block_index, block) in zip(matching_issues, locations):
                issue["page"] = page
                issue["block_index"] = block_index
                issue["block_excerpt"] = block[:180]
                if source_format in {"pdf", "epub"}:
                    issue["source_format"] = source_format
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
