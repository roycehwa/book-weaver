"""Deterministic Chinese-target Markdown punctuation and spacing cleanup.

Runs after machine translation and before optional model polish. Model-free,
idempotent, and conservative: never deletes or translates words.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdf_translator.source_workspace import atomic_json

RULES_VERSION = "zh_markdown_cleanup_v1"
REPORT_SCHEMA = "zh_markdown_cleanup_report_v1"
EXCERPT_RADIUS = 32

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")

ZH_CLOSING_PUNCT = frozenset("，。；：！？）、】」』》\"'…")
ZH_OPENING_PUNCT = frozenset("（【「『《\"'…、")

ASCII_TO_ZH = {
    ",": "，",
    ".": "。",
    "?": "？",
    "!": "！",
    ":": "：",
    ";": "；",
}

FOOTNOTE_REF_RE = re.compile(r"\[\^[^\]]+\]")
PRESERVE_MARKER_RE = re.compile(r"\[\[PRESERVE_ORIGINAL_BLOCK_\d{4}\]\]")
HTML_TAG_RE = re.compile(r"<[^>]+>")
AUTO_LINK_RE = re.compile(r"<(?:https?://|mailto:)[^>\s]+>")
URL_RE = re.compile(r"(?:https?://|www\.)\S+")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
INLINE_CODE_RE = re.compile(r"`+[^`\n]+`+")
IMAGE_MARKER_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
FENCE_LINE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
VERSIONISH_RE = re.compile(r"(?<![A-Za-z0-9])[vV]?\d+(?:\.\d+)+(?![A-Za-z0-9])")


@dataclass(frozen=True, slots=True)
class CleanupChange:
    rule_id: str
    line: int
    original: str
    replacement: str
    before_excerpt: str
    after_excerpt: str


def should_run_zh_markdown_cleanup(target_language: str, text_operation: str) -> bool:
    if text_operation != "translate":
        return False
    normalized = (target_language or "").strip().lower().replace("_", "-")
    return normalized == "zh" or normalized.startswith("zh-")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _excerpt(line: str, start: int, end: int) -> tuple[str, str]:
    before_start = max(0, start - EXCERPT_RADIUS)
    after_end = min(len(line), end + EXCERPT_RADIUS)
    return line[before_start:start], line[end:after_end]


def _markdown_link_destination_spans(line: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(line):
        if line.startswith("![", index):
            close = line.find("]", index + 2)
            if close != -1 and close + 1 < len(line) and line[close + 1] == "(":
                end = line.find(")", close + 2)
                if end != -1:
                    spans.append((close + 1, end + 1))
                    index = end + 1
                    continue
        if line[index] == "[":
            close = line.find("]", index + 1)
            if close != -1 and close + 1 < len(line) and line[close + 1] == "(":
                end = line.find(")", close + 2)
                if end != -1:
                    spans.append((close + 1, end + 1))
                    index = end + 1
                    continue
        index += 1
    return spans


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    ordered = sorted(spans)
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _protected_spans(line: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in (
        FOOTNOTE_REF_RE,
        PRESERVE_MARKER_RE,
        IMAGE_MARKER_RE,
        INLINE_CODE_RE,
        HTML_TAG_RE,
        AUTO_LINK_RE,
        URL_RE,
        EMAIL_RE,
    ):
        for match in pattern.finditer(line):
            spans.append(match.span())
    spans.extend(_markdown_link_destination_spans(line))
    return _merge_spans(spans)


def _in_spans(index: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in spans)


def _is_table_row(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _is_decimal_dot(line: str, index: int) -> bool:
    if line[index] != ".":
        return False
    return (
        index > 0
        and index + 1 < len(line)
        and line[index - 1].isdigit()
        and line[index + 1].isdigit()
    )


def _is_grouped_number_comma(line: str, index: int) -> bool:
    if line[index] != ",":
        return False
    left = index - 1
    right = index + 1
    if left < 0 or right >= len(line):
        return False
    if not line[left].isdigit() or not line[right].isdigit():
        return False
    window = line[max(0, index - 6) : index + 7]
    return bool(re.search(r"\d{1,3}(?:,\d{3})+", window))


def _is_ascii_word_char(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in {"_", "-"})


def _ascii_punct_in_identifier(line: str, index: int) -> bool:
    char = line[index]
    if char not in ASCII_TO_ZH:
        return False
    left = index - 1
    right = index + 1
    while left >= 0 and line[left].isspace():
        left -= 1
    while right < len(line) and line[right].isspace():
        right += 1
    if left >= 0 and right < len(line):
        if _is_ascii_word_char(line[left]) and _is_ascii_word_char(line[right]):
            return True
    window = line[max(0, index - 12) : min(len(line), index + 13)]
    if VERSIONISH_RE.search(window):
        return True
    return False


def _local_cjk_context(line: str, index: int) -> bool:
    left = line[max(0, index - 4) : index]
    right = line[index + 1 : min(len(line), index + 5)]
    return bool(CJK_RE.search(left) or CJK_RE.search(right))


def _can_convert_ascii_punct(line: str, index: int, spans: list[tuple[int, int]]) -> bool:
    char = line[index]
    if char not in ASCII_TO_ZH or _in_spans(index, spans):
        return False
    if _is_decimal_dot(line, index) or _is_grouped_number_comma(line, index):
        return False
    if _ascii_punct_in_identifier(line, index):
        return False
    if not _local_cjk_context(line, index):
        return False
    if char == "." and index + 1 < len(line) and line[index + 1].isdigit() and index > 0 and line[index - 1].isdigit():
        return False
    return True


def _apply_replacements(
    line: str,
    line_no: int,
    spans: list[tuple[int, int]],
    replacements: list[tuple[int, int, str, str]],
) -> tuple[str, list[CleanupChange]]:
    if not replacements:
        return line, []
    ordered = sorted(replacements, key=lambda item: item[0], reverse=True)
    current = line
    changes: list[CleanupChange] = []
    for start, end, rule_id, new_text in ordered:
        old_text = current[start:end]
        before_excerpt, after_excerpt = _excerpt(current, start, end)
        changes.append(
            CleanupChange(
                rule_id=rule_id,
                line=line_no,
                original=old_text,
                replacement=new_text,
                before_excerpt=before_excerpt,
                after_excerpt=after_excerpt,
            )
        )
        current = current[:start] + new_text + current[end:]
    changes.reverse()
    return current, changes


def _collect_ascii_punct_rules(line: str, spans: list[tuple[int, int]]) -> list[tuple[int, int, str, str]]:
    replacements: list[tuple[int, int, str, str]] = []
    for index, char in enumerate(line):
        if _in_spans(index, spans):
            continue
        if _can_convert_ascii_punct(line, index, spans):
            replacements.append((index, index + 1, "zh_ascii_punct", ASCII_TO_ZH[char]))
    return replacements


def _collect_whitespace_rules(line: str, spans: list[tuple[int, int]]) -> list[tuple[int, int, str, str]]:
    replacements: list[tuple[int, int, str, str]] = []
    index = 0
    while index < len(line):
        if _in_spans(index, spans):
            index += 1
            continue
        char = line[index]
        if char.isspace() and index + 1 < len(line):
            next_char = line[index + 1]
            if next_char in ZH_CLOSING_PUNCT and not _in_spans(index + 1, spans):
                replacements.append((index, index + 1, "zh_ws_before_close", ""))
                index += 1
                continue
        if char in ZH_OPENING_PUNCT and index + 1 < len(line) and line[index + 1].isspace():
            run_end = index + 1
            while run_end < len(line) and line[run_end].isspace() and not _in_spans(run_end, spans):
                run_end += 1
            if run_end > index + 1:
                replacements.append((index + 1, run_end, "zh_ws_after_open", ""))
                index = run_end
                continue
        if char in ZH_CLOSING_PUNCT and index + 1 < len(line) and line[index + 1].isspace():
            run_end = index + 1
            while run_end < len(line) and line[run_end].isspace() and not _in_spans(run_end, spans):
                run_end += 1
            if run_end > index + 1 and run_end < len(line) and CJK_RE.match(line[run_end]):
                replacements.append((index + 1, run_end, "zh_ws_after_close", ""))
                index = run_end
                continue
        index += 1

    punct_class = "".join(ZH_CLOSING_PUNCT | ZH_OPENING_PUNCT)
    pattern = re.compile(
        rf"(?P<left>\s{{2,}})(?P<punct>[{re.escape(punct_class)}])"
        rf"|(?P<open>[{re.escape(''.join(ZH_OPENING_PUNCT))}])(?P<right>\s{{2,}})"
    )
    for match in pattern.finditer(line):
        if match.group("left") is not None:
            start, end = match.start("left"), match.end("left")
            if not any(_in_spans(pos, spans) for pos in range(start, end)):
                replacements.append((start, end, "zh_ws_adjacent_punct", " "))
        else:
            start, end = match.start("right"), match.end("right")
            if not any(_in_spans(pos, spans) for pos in range(start, end)):
                replacements.append((start, end, "zh_ws_adjacent_punct", " "))
    return replacements


def _merge_non_overlapping(
    line: str,
    spans: list[tuple[int, int]],
    replacements: list[tuple[int, int, str, str]],
) -> list[tuple[int, int, str, str]]:
    merged: list[tuple[int, int, str, str]] = []
    for start, end, rule_id, new_text in sorted(replacements, key=lambda item: item[0]):
        if any(_in_spans(pos, spans) for pos in range(start, max(end, start + 1))):
            continue
        overlap = False
        for existing_start, existing_end, _, _ in merged:
            if not (end <= existing_start or start >= existing_end):
                overlap = True
                break
        if not overlap:
            merged.append((start, end, rule_id, new_text))
    return merged


def _process_line(line: str, line_no: int) -> tuple[str, list[CleanupChange]]:
    if _is_table_row(line):
        return line, []
    spans = _protected_spans(line)
    if spans and spans[0][0] == 0 and spans[-1][1] == len(line):
        return line, []
    all_changes: list[CleanupChange] = []
    current = line
    for collector in (_collect_ascii_punct_rules, _collect_whitespace_rules):
        active_spans = _protected_spans(current)
        replacements = _merge_non_overlapping(current, active_spans, collector(current, active_spans))
        current, changes = _apply_replacements(current, line_no, active_spans, replacements)
        all_changes.extend(changes)
    return current, all_changes


def cleanup_zh_markdown(text: str) -> tuple[str, dict[str, Any]]:
    input_fingerprint = _sha256(text)
    lines = text.splitlines(keepends=True)
    processed: list[str] = []
    all_changes: list[CleanupChange] = []
    in_fence = False
    fence_marker: str | None = None
    line_no = 0
    for piece in lines:
        line_no += 1
        content = piece.rstrip("\r\n")
        ending = piece[len(content) :]
        protected = False
        fence_match = FENCE_LINE_RE.match(content)
        if fence_match:
            marker = fence_match.group(2)
            if not in_fence:
                in_fence = True
                fence_marker = marker
                protected = True
            elif fence_marker and marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                protected = True
                in_fence = False
                fence_marker = None
            else:
                protected = True
        elif in_fence:
            protected = True

        if protected or _is_table_row(content):
            cleaned = content
        else:
            cleaned, line_changes = _process_line(content, line_no)
            all_changes.extend(line_changes)
        processed.append(cleaned + ending)
    body = "".join(processed) if lines else text

    output_fingerprint = _sha256(body)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "status": "applied",
        "target_language": None,
        "version": RULES_VERSION,
        "fingerprints": {
            "input_sha256": input_fingerprint,
            "output_sha256": output_fingerprint,
        },
        "changed_count": len(all_changes),
        "changes": [
            {
                "rule_id": change.rule_id,
                "line": change.line,
                "original": change.original,
                "replacement": change.replacement,
                "before_excerpt": change.before_excerpt,
                "after_excerpt": change.after_excerpt,
            }
            for change in all_changes
        ],
    }
    return body, report


def publish_translation_zh_cleanup(
    run_dir: Path,
    raw_markdown: str,
    *,
    target_language: str,
    text_operation: str,
) -> tuple[str, dict[str, str] | None]:
    if not should_run_zh_markdown_cleanup(target_language, text_operation):
        return raw_markdown, None

    run_dir = run_dir.expanduser().resolve()
    raw_path = run_dir / "translated.raw.md"
    cleaned_path = run_dir / "translated.cleaned.md"
    report_path = run_dir / "cleanup-report.json"

    raw_path.write_text(raw_markdown, encoding="utf-8")
    cleaned, report = cleanup_zh_markdown(raw_markdown)
    report["target_language"] = target_language
    cleaned_path.write_text(cleaned, encoding="utf-8")
    atomic_json(report_path, report)

    return cleaned, {
        "translated_raw_markdown": str(raw_path),
        "translated_cleaned_markdown": str(cleaned_path),
        "cleanup_report": str(report_path),
    }
