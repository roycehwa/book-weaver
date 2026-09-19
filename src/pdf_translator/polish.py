from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import requests

from pdf_translator.chunking import markdown_block_structure
from pdf_translator.epub import render_epub_from_book
from pdf_translator.models import TranslationChunk
from pdf_translator.pipeline import safe_delivery_file_stem
from pdf_translator.source_workspace import atomic_json
from pdf_translator.translate import (
    BaseTranslator,
    MiniMaxAnthropicTranslator,
    OpenAICompatibleTranslator,
    OpenAITranslator,
    build_translator,
    translate_book_chapters,
)
from pdf_translator.zh_markdown_cleanup import (
    FENCE_LINE_RE,
    RULES_VERSION,
    TRANSLATION_CLEANUP_REPORT_FILENAME,
    is_indented_code_line,
    is_table_row,
    protected_spans,
)


POLISH_PROMPT_VERSION = "v4-constrained-suspect-only"


POLISH_SYSTEM_PROMPT = """你是中文译文精修编辑。任务不是重新翻译，而是仅针对 suspects 中列出的英文夹杂做最小修改。

硬性规则：
1. 只处理 suspects 字段中列出的可疑英文词或短语；不得改动其他文字。
2. 禁止整句重写、扩写、缩写或改写段落；不得改变行数或段落边界。
3. 必须完整保留 Markdown 结构、链接语法与数量、锚点、图片标记、脚注与引用编号、数字字面量、专有名词，以及已批准术语表中的目标词与源词。
4. 人名、地名、书名、机构名、音译词保留；中文术语后括注英文（如“感官（senses）”）保留括注。
5. 若某 suspect 不应翻译，polished_text 必须与原文 text 完全一致。
6. 只返回 JSON 数组，每项包含 line 和 polished_text；不要解释、不要 Markdown 围栏。
"""

HIGH_CONFIDENCE_ENGLISH_WORDS = {
    "active",
    "emergent",
    "vital",
    "denote",
    "perceived",
    "solidness",
    "mere",
    "following",
    "serve",
    "action",
    "sites",
    "animate",
    "inanimate",
    "context",
    "emergence",
    "cosmopolitan",
    "affinity",
    "complex",
    "milieus",
    "memorization",
    "ecumene",
    "conception",
    "precisely",
    "because",
    "its",
    "vitality",
    "inherent",
    "living",
    "organism",
    "embedded",
    "within",
    "Conditioning",
    "act",
    "famously",
    "claimed",
    "voice",
    "kind",
    "sound",
    "characteristic",
    "what",
    "has",
    "popularity",
    "breakout",
    "digression",
    "vignette",
    "heteroglossia",
    "life",
    "necessarily",
    "repertoires",
    "audiovocal",
    "aural",
    "eyeness",
    "local",
    "domestic",
    "rural",
    "industries",
    "strong",
    "lived",
    "clusters",
    "inclusive",
    "repository",
    "deeply",
    "manifest",
    "manifested",
    "pulsating",
    "vibrant",
    "throughout",
    "integral",
    "built",
    "constellation",
    "governing",
    "ambition",
    "dominant",
    "postcolonial",
    "alternative",
    "cross-fertilization",
}

_APOSTROPHE = "'\u2019"
ENGLISH_WORD_RE = re.compile(
    rf"(?<![A-Za-z])([A-Za-z][A-Za-z{_APOSTROPHE}-]{{1,30}})(?![A-Za-z])"
)
ENGLISH_THEN_CHINESE_RE = re.compile(
    rf"(?P<english>[A-Za-z][A-Za-z{_APOSTROPHE}\-/]*(?:\s+[A-Za-z][A-Za-z{_APOSTROPHE}\-/]*){{0,6}})\s*[（(](?P<chinese>[\u4e00-\u9fff][^（）()A-Za-z]{{0,80}})[）)]"
)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_WORD_RE = re.compile(
    rf"(?<![A-Za-z])([A-Za-z][A-Za-z{_APOSTROPHE}\-/]*)(?![A-Za-z])"
)
NUMERIC_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+)(?![A-Za-z0-9])"
)
EXCESSIVE_EDIT_MIN_RATIO = 0.72
EXCESSIVE_EDIT_MAX_GROWTH = 1.35
PolishOutcome = Literal["applied", "needs_review", "no_candidates"]


@dataclass(slots=True)
class PolishCandidate:
    line: int
    text: str
    suspects: list[str]
    category: str = "high_confidence"


@dataclass(slots=True)
class CandidateModelResult:
    line: int
    polished_text: str | None
    decision: str


@dataclass(slots=True)
class PolishAcceptContext:
    suspects: list[str]
    protected_literals: list[str]
    glossary_terms: list[str]
    book_title: str | None
    book_author: str | None


@dataclass(slots=True)
class PolishResult:
    run_dir: Path
    polished_markdown_path: Path
    polished_epub_path: Path
    report_path: Path
    detected_candidate_count: int
    candidate_count: int
    accepted_count: int
    rejected_count: int
    changed_count: int
    unchanged_count: int
    needs_review_count: int
    outcome: PolishOutcome
    protected_skip_count: int
    manual_skip_count: int


@dataclass(slots=True)
class PolishScanResult:
    candidates: list[PolishCandidate]
    detected_candidate_count: int
    protected_skip_count: int


class IncompletePolishBatchError(ValueError):
    pass


class CacheOnlyTranslator(BaseTranslator):
    name = "cache-only"

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        raise ValueError(f"missing cached translation for chunk {chunk.index}")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text_preserve_newlines(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_text_preserve_newlines(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _in_spans(index: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in spans)


def _span_overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(not (end <= span_start or start >= span_end) for span_start, span_end in spans)


def _is_structural_line(text: str) -> bool:
    stripped = text.strip()
    return not stripped or stripped.startswith(("#", "![", "|", ">", "```"))


def _inside_parenthetical(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 2) : start]
    after = text[end : end + 2]
    return "(" in before or "（" in before or ")" in after or "）" in after


def _collect_line_suspects(line: str, *, respect_protected_spans: bool) -> list[str]:
    stripped = line.strip()
    if _is_structural_line(stripped) or not CJK_RE.search(stripped):
        return []
    spans = protected_spans(stripped) if respect_protected_spans else []
    suspects: list[str] = []
    apostrophe_strip = f"{_APOSTROPHE}-"
    for match in ENGLISH_THEN_CHINESE_RE.finditer(stripped):
        if respect_protected_spans and _span_overlaps(match.start(), match.end(), spans):
            continue
        english = match.group("english").strip()
        if english and english not in suspects:
            suspects.append(english)
    for match in ENGLISH_WORD_RE.finditer(stripped):
        if respect_protected_spans and _in_spans(match.start(), spans):
            continue
        word = match.group(1).strip(apostrophe_strip)
        if not word or word not in HIGH_CONFIDENCE_ENGLISH_WORDS:
            continue
        if _inside_parenthetical(stripped, match.start(), match.end()):
            continue
        if word not in suspects:
            suspects.append(word)
    return suspects


def _scan_line_candidates(line: str) -> list[str]:
    return _collect_line_suspects(line, respect_protected_spans=True)


def _line_is_structurally_protected(
    content: str,
    *,
    in_fence: bool,
    prev_line: str | None,
    next_line: str | None,
) -> bool:
    if in_fence or is_indented_code_line(content):
        return True
    return is_table_row(content, prev_line=prev_line, next_line=next_line)


def scan_polish_candidates_internal(markdown_text: str) -> PolishScanResult:
    bare_lines = [piece.rstrip("\r\n") for piece in markdown_text.splitlines(keepends=True)]
    candidates: list[PolishCandidate] = []
    protected_skip_count = 0
    in_fence = False
    fence_marker: str | None = None
    line_no = 0
    for line_index, content in enumerate(bare_lines):
        line_no += 1
        prev_line = bare_lines[line_index - 1] if line_index > 0 else None
        next_line = bare_lines[line_index + 1] if line_index + 1 < len(bare_lines) else None
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
        actionable_suspects = _scan_line_candidates(content)
        masked_suspects = _collect_line_suspects(content, respect_protected_spans=False)
        if not actionable_suspects and not masked_suspects:
            continue
        if _line_is_structurally_protected(
            content,
            in_fence=protected or in_fence,
            prev_line=prev_line,
            next_line=next_line,
        ):
            protected_skip_count += 1
            continue
        if not actionable_suspects:
            protected_skip_count += 1
            continue
        candidates.append(
            PolishCandidate(line=line_no, text=content, suspects=actionable_suspects)
        )
    return PolishScanResult(
        candidates=candidates,
        detected_candidate_count=len(candidates) + protected_skip_count,
        protected_skip_count=protected_skip_count,
    )


def scan_polish_candidates(markdown_text: str) -> list[PolishCandidate]:
    return scan_polish_candidates_internal(markdown_text).candidates


def _candidate_cache_path(
    cache_dir: Path,
    candidate: PolishCandidate,
    *,
    cleanup_rules_version: str,
    prompt_context: str,
) -> Path:
    digest_input = (
        f"{POLISH_PROMPT_VERSION}\n{cleanup_rules_version}\n{prompt_context}\n{candidate.text}"
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"line-{candidate.line:06d}-{digest}.json"


def _build_polish_prompt(candidates: list[PolishCandidate], *, protected_terms: dict[int, list[str]]) -> str:
    payload = [
        {
            "line": candidate.line,
            "suspects": candidate.suspects,
            "text": candidate.text,
            "protected_terms": protected_terms.get(candidate.line, []),
        }
        for candidate in candidates
    ]
    return "请仅对 suspects 做最小修改。返回 JSON 数组。\n\n" + json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    )


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _complete_with_translator(
    *,
    translator: BaseTranslator,
    prompt: str,
    target_language: str,
    index: int,
    request_timeout_seconds: float | None = None,
) -> str:
    if isinstance(translator, MiniMaxAnthropicTranslator):
        http_timeout = (
            float(request_timeout_seconds)
            if request_timeout_seconds is not None and request_timeout_seconds > 0
            else float(os.getenv("POLISH_HTTP_TIMEOUT_SECONDS", str(translator.http_timeout)))
        )
        payload = {
            "model": translator.model,
            "max_tokens": translator.max_tokens,
            "system": POLISH_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            response = requests.post(
                translator.endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {translator.api_key}",
                    "Content-Type": "application/json",
                    "Connection": "close",
                    "anthropic-version": "2023-06-01",
                },
                timeout=(10, http_timeout),
            )
            response.raise_for_status()
            response_data = response.json()
        except requests.HTTPError as exc:
            error_body = exc.response.text if exc.response is not None else ""
            status_code = exc.response.status_code if exc.response else "?"
            raise ValueError(
                f"MiniMax polish failed for batch {index}: HTTP {status_code}: {error_body[:500]}"
            ) from exc
        except requests.RequestException as exc:
            raise ValueError(f"MiniMax polish failed for batch {index}: {exc}") from exc
        text_parts = [
            str(item.get("text") or "")
            for item in response_data.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(part.strip() for part in text_parts if part.strip()).strip()

    if isinstance(translator, OpenAITranslator):
        response = translator.client.responses.create(
            model=translator.model,
            input=[
                {"role": "system", "content": POLISH_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.output_text.strip()

    if isinstance(translator, OpenAICompatibleTranslator):
        response = translator.client.chat.completions.create(
            model=translator.model,
            messages=[
                {"role": "system", "content": POLISH_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    return translator.translate_chunk(
        TranslationChunk(index=index, markdown=prompt),
        source_language=None,
        target_language=target_language,
    )


def _parse_polish_response(text: str) -> dict[int, str]:
    data = json.loads(_strip_json_fence(text))
    if not isinstance(data, list):
        raise ValueError("Polish response must be a JSON array.")
    parsed: dict[int, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        line = item.get("line")
        polished = item.get("polished_text")
        if isinstance(line, int) and isinstance(polished, str):
            if not polished.strip():
                continue
            parsed[line] = polished
    return parsed


def _protected_literals(line: str) -> list[str]:
    spans = protected_spans(line)
    return [line[start:end] for start, end in spans]


def _find_matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _markdown_link_specs(text: str) -> list[tuple[bool, str]]:
    specs: list[tuple[bool, str]] = []
    index = 0
    while index < len(text):
        is_image = text.startswith("![", index)
        if is_image or text[index] == "[":
            start = index + 2 if is_image else index + 1
            close = text.find("]", start)
            if close != -1 and close + 1 < len(text) and text[close + 1] == "(":
                end = _find_matching_paren(text, close + 1)
                if end != -1:
                    destination = text[close + 2 : end]
                    specs.append((is_image, destination))
                    index = end + 1
                    continue
        index += 1
    return specs


def _mask_suspect_phrases(text: str, suspects: list[str]) -> str:
    masked = text
    for suspect in sorted({item for item in suspects if item.strip()}, key=len, reverse=True):
        pattern = re.compile(re.escape(suspect), re.IGNORECASE)
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


def _latin_word_tokens(text: str, spans: list[tuple[int, int]]) -> list[str]:
    tokens: list[str] = []
    for match in LATIN_WORD_RE.finditer(text):
        if _span_overlaps(match.start(), match.end(), spans):
            continue
        token = match.group(1)
        if token:
            tokens.append(token)
    return tokens


def _non_suspect_latin_tokens(text: str, suspects: list[str], spans: list[tuple[int, int]]) -> list[str]:
    suspect_set = _normalize_suspects(suspects)
    masked = _mask_suspect_phrases(text, suspects)
    return [
        token
        for token in _latin_word_tokens(masked, spans)
        if token.lower() not in suspect_set
    ]


def _suspect_still_present(text: str, suspects: list[str], spans: list[tuple[int, int]]) -> bool:
    for suspect in suspects:
        if not suspect.strip():
            continue
        pattern = re.compile(re.escape(suspect), re.IGNORECASE)
        for match in pattern.finditer(text):
            if not _span_overlaps(match.start(), match.end(), spans):
                return True
    return False


def _numeric_literals(text: str, spans: list[tuple[int, int]]) -> list[str]:
    values: list[str] = []
    for match in NUMERIC_LITERAL_RE.finditer(text):
        if _span_overlaps(match.start(), match.end(), spans):
            continue
        values.append(match.group(0))
    return values


def _ascii_letter_count(text: str) -> int:
    return sum(1 for char in text if char.isascii() and char.isalpha())


def _cjk_count(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def _normalize_suspects(suspects: list[str]) -> set[str]:
    return {item.strip().lower() for item in suspects if item.strip()}


def _safe_accept_polish(
    before: str,
    after: str,
    *,
    context: PolishAcceptContext,
    whole_before: str,
    whole_after: str,
) -> tuple[bool, str]:
    if not after.strip():
        return False, "empty"
    if before[: len(before) - len(before.lstrip())] != after[: len(after) - len(after.lstrip())]:
        return False, "leading_whitespace_changed"
    if before[len(before.rstrip()) :] != after[len(after.rstrip()) :]:
        return False, "trailing_whitespace_changed"
    if before.count("\n") != after.count("\n"):
        return False, "newline_changed"
    if len(before.splitlines()) != len(after.splitlines()):
        return False, "line_count_changed"
    if _is_structural_line(before):
        return False, "structural"
    if markdown_block_structure(before) != markdown_block_structure(after):
        return False, "markdown_structure_changed"
    if _markdown_link_specs(before) != _markdown_link_specs(after):
        return False, "link_structure_changed"
    if before.count("![") != after.count("!["):
        return False, "image_marker_changed"
    if before.count("[^") != after.count("[^"):
        return False, "footnote_marker_changed"
    before_spans = protected_spans(before)
    after_spans = protected_spans(after)
    if _protected_literals(before) != _protected_literals(after):
        return False, "protected_literal_changed"
    if _numeric_literals(before, before_spans) != _numeric_literals(after, after_spans):
        return False, "numeric_literal_changed"
    if _non_suspect_latin_tokens(before, context.suspects, before_spans) != _non_suspect_latin_tokens(
        after, context.suspects, after_spans
    ):
        return False, "non_suspect_latin_changed"
    for term in context.glossary_terms:
        if term and term in before and term not in after:
            return False, "glossary_term_changed"
    if context.book_title and context.book_title in before and context.book_title not in after:
        return False, "book_metadata_changed"
    if context.book_author and context.book_author in before and context.book_author not in after:
        return False, "book_metadata_changed"
    before_cjk = _cjk_count(before)
    after_cjk = _cjk_count(after)
    if before_cjk >= 80 and after_cjk < before_cjk * 0.82:
        return False, "cjk_drop"
    if len(after) < len(before) * 0.62 and _ascii_letter_count(before) < len(before) * 0.35:
        return False, "length_drop"
    if len(after) > int(len(before) * EXCESSIVE_EDIT_MAX_GROWTH) + 8:
        return False, "excessive_edit"
    if before and len(after) >= len(before):
        if SequenceMatcher(None, before, after).ratio() < EXCESSIVE_EDIT_MIN_RATIO and len(after) - len(before) > 12:
            return False, "excessive_edit"
    if re.search(r"^(以下是|精修|修改后|译文)", after):
        return False, "commentary"
    if markdown_block_structure(whole_before) != markdown_block_structure(whole_after):
        return False, "document_markdown_structure_changed"
    if len(whole_before.splitlines()) != len(whole_after.splitlines()):
        return False, "document_line_count_changed"
    return True, "accepted"


def _append_glossary_entry_terms(entries: list[dict[str, str]], entry: object) -> None:
    if not isinstance(entry, dict):
        return
    source = entry.get("source")
    target = entry.get("target")
    preferred = entry.get("preferred_translation")
    record: dict[str, str] = {}
    if isinstance(source, str) and source.strip():
        record["source"] = source.strip()
    if isinstance(target, str) and target.strip():
        record["target"] = target.strip()
    if isinstance(preferred, str) and preferred.strip():
        record["preferred_translation"] = preferred.strip()
    if record:
        entries.append(record)


def _load_glossary_entries(run_dir: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for relative in ("glossary/active.json", "jobs/glossary-constraints.json"):
        path = run_dir / relative
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if relative.endswith("active.json") and isinstance(payload.get("entries"), list):
            for entry in payload["entries"]:
                _append_glossary_entry_terms(entries, entry)
        if relative.endswith("glossary-constraints.json"):
            if isinstance(payload.get("terms"), list):
                for entry in payload["terms"]:
                    _append_glossary_entry_terms(entries, entry)
            if isinstance(payload.get("chunks"), list):
                for chunk in payload["chunks"]:
                    if not isinstance(chunk, dict):
                        continue
                    for entry in chunk.get("terms") or []:
                        _append_glossary_entry_terms(entries, entry)
    return entries


def _load_glossary_terms(run_dir: Path) -> list[str]:
    terms: list[str] = []
    for entry in _load_glossary_entries(run_dir):
        for key in ("source", "target", "preferred_translation"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                terms.append(value.strip())
    return terms


def _protected_terms_for_candidate(
    candidate: PolishCandidate,
    glossary_entries: list[dict[str, str]],
    *,
    book_title: str | None,
    book_author: str | None,
) -> list[str]:
    protected: list[str] = []
    haystack = candidate.text
    for entry in glossary_entries:
        for key in ("source", "target", "preferred_translation"):
            value = entry.get(key)
            if isinstance(value, str) and value and value in haystack:
                protected.append(value)
    if book_title and book_title in haystack:
        protected.append(book_title)
    if book_author and book_author in haystack:
        protected.append(book_author)
    return sorted(set(protected))


def _load_cleanup_version(run_dir: Path) -> str:
    report_path = run_dir / TRANSLATION_CLEANUP_REPORT_FILENAME
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return RULES_VERSION
        version = report.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    return RULES_VERSION


def _prompt_context_for_candidate(candidate: PolishCandidate, protected_terms: list[str]) -> str:
    return json.dumps(
        {"suspects": candidate.suspects, "protected_terms": protected_terms},
        ensure_ascii=False,
        sort_keys=True,
    )


def _translate_candidates(
    *,
    candidates: list[PolishCandidate],
    translator: BaseTranslator,
    target_language: str,
    cache_dir: Path,
    batch_size: int,
    concurrency: int,
    request_timeout_seconds: float | None,
    cleanup_rules_version: str,
    protected_terms_by_line: dict[int, list[str]],
) -> dict[int, CandidateModelResult]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    results: dict[int, CandidateModelResult] = {}
    uncached: list[PolishCandidate] = []
    for candidate in candidates:
        prompt_context = _prompt_context_for_candidate(
            candidate,
            protected_terms_by_line.get(candidate.line, []),
        )
        cache_path = _candidate_cache_path(
            cache_dir,
            candidate,
            cleanup_rules_version=cleanup_rules_version,
            prompt_context=prompt_context,
        )
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cached = {}
            polished = cached.get("polished_text")
            decision = cached.get("decision")
            if (
                isinstance(polished, str)
                and isinstance(decision, str)
                and decision == "model_suggested"
            ):
                results[candidate.line] = CandidateModelResult(
                    line=candidate.line,
                    polished_text=polished if polished.strip() else None,
                    decision=decision,
                )
                continue
        uncached.append(candidate)

    batches = [uncached[index : index + batch_size] for index in range(0, len(uncached), batch_size)]

    def run_batch(batch_index: int, batch: list[PolishCandidate]) -> dict[int, str]:
        prompt = _build_polish_prompt(batch, protected_terms=protected_terms_by_line)
        expected_lines = {candidate.line for candidate in batch}
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = _complete_with_translator(
                    translator=translator,
                    prompt=prompt,
                    target_language=target_language,
                    index=batch_index,
                    request_timeout_seconds=request_timeout_seconds,
                )
                parsed = _parse_polish_response(response)
                missing_lines = expected_lines - set(parsed)
                if missing_lines:
                    raise IncompletePolishBatchError(f"missing polish lines: {sorted(missing_lines)}")
                return {line: parsed[line] for line in expected_lines}
            except IncompletePolishBatchError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(min(2**attempt, 8))
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(min(2**attempt, 8))
        if isinstance(last_error, IncompletePolishBatchError):
            raise last_error
        raise ValueError(f"Polish batch {batch_index} failed: {last_error}") from last_error

    def run_batch_safe(batch_index: int, batch: list[PolishCandidate]) -> dict[int, CandidateModelResult]:
        try:
            parsed = run_batch(batch_index, batch)
            return {
                line: CandidateModelResult(line=line, polished_text=text, decision="model_suggested")
                for line, text in parsed.items()
            }
        except IncompletePolishBatchError:
            return {
                candidate.line: CandidateModelResult(
                    line=candidate.line,
                    polished_text=None,
                    decision="incomplete_response",
                )
                for candidate in batch
            }
        except Exception:
            return {
                candidate.line: CandidateModelResult(
                    line=candidate.line,
                    polished_text=None,
                    decision="model_unavailable",
                )
                for candidate in batch
            }

    if batches:
        if concurrency <= 1 or len(batches) <= 1:
            for batch_index, batch in enumerate(batches):
                results.update(run_batch_safe(batch_index, batch))
        else:
            with ThreadPoolExecutor(max_workers=min(concurrency, len(batches))) as executor:
                futures = {
                    executor.submit(run_batch_safe, batch_index, batch): batch
                    for batch_index, batch in enumerate(batches)
                }
                for future in as_completed(futures):
                    results.update(future.result())

    for candidate in candidates:
        model_result = results.get(candidate.line)
        if model_result is None or model_result.decision != "model_suggested":
            continue
        cache_path = _candidate_cache_path(
            cache_dir,
            candidate,
            cleanup_rules_version=cleanup_rules_version,
            prompt_context=_prompt_context_for_candidate(
                candidate,
                protected_terms_by_line.get(candidate.line, []),
            ),
        )
        cache_path.write_text(
            json.dumps(
                {
                    "line": candidate.line,
                    "text": candidate.text,
                    "suspects": candidate.suspects,
                    "polished_text": model_result.polished_text or "",
                    "decision": model_result.decision,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return results


def _document_line_parts(markdown_text: str) -> tuple[list[str], list[str], str, bool]:
    pieces = markdown_text.splitlines(keepends=True)
    if not pieces and markdown_text:
        pieces = [markdown_text]
    bare_lines: list[str] = []
    endings: list[str] = []
    for piece in pieces:
        bare = piece.rstrip("\r\n")
        bare_lines.append(bare)
        endings.append(piece[len(bare) :])
    newline_style = "\r\n" if any(ending == "\r\n" for ending in endings) else "\n"
    has_final_newline = markdown_text.endswith("\n") or markdown_text.endswith("\r\n")
    return bare_lines, endings, newline_style, has_final_newline


def _join_document_lines(
    bare_lines: list[str],
    endings: list[str],
    newline_style: str,
    has_final_newline: bool,
) -> str:
    if not bare_lines:
        return "" if not has_final_newline else newline_style
    joined_parts: list[str] = []
    for index, bare in enumerate(bare_lines):
        ending = endings[index] if index < len(endings) else newline_style
        joined_parts.append(bare + ending)
    body = "".join(joined_parts)
    if has_final_newline and not body.endswith(("\n", "\r\n")):
        body += newline_style
    if not has_final_newline:
        body = body.rstrip("\r\n")
    return body


TOP_LEVEL_HEADING_RE = re.compile(r"(?m)^#\s+.+$")


def _split_top_level_heading_sections(markdown_text: str) -> list[str]:
    matches = list(TOP_LEVEL_HEADING_RE.finditer(markdown_text))
    if not matches:
        stripped = markdown_text.strip()
        return [stripped + "\n"] if stripped else []

    sections: list[str] = []
    leading = markdown_text[: matches[0].start()].strip()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown_text)
        section = markdown_text[match.start() : end].strip()
        if index == 0 and leading:
            section = f"{leading}\n\n{section}"
        if section:
            sections.append(section + "\n")
    return sections


def _split_by_original_chapter_titles(book: dict[str, Any], markdown_text: str) -> list[dict[str, Any]]:
    chapters = book.get("chapters") or []
    if not chapters:
        return [{"index": 1, "title": "Book", "markdown": markdown_text}]

    chapter_payloads: list[dict[str, Any]] = []
    cursor = 0
    for index, chapter in enumerate(chapters):
        title = str(chapter.get("title") or f"Chapter {index + 1}")
        marker = f"# {title}"
        start = markdown_text.find(marker, cursor)
        if start < 0:
            start = cursor
        end = len(markdown_text)
        for next_chapter in chapters[index + 1 :]:
            next_title = str(next_chapter.get("title") or "")
            next_marker = f"# {next_title}"
            if not next_title:
                continue
            next_start = markdown_text.find(next_marker, start + len(marker))
            if next_start >= 0:
                end = next_start
                break
        chapter_payloads.append({**chapter, "markdown": markdown_text[start:end].strip() + "\n"})
        cursor = end
    return chapter_payloads


def _split_polished_markdown_into_chapters(book: dict[str, Any], markdown_text: str) -> list[dict[str, Any]]:
    chapters = book.get("chapters") or []
    if not chapters:
        return [{"index": 1, "title": "Book", "markdown": markdown_text}]

    sections = _split_top_level_heading_sections(markdown_text)
    if len(sections) >= len(chapters):
        chapter_payloads: list[dict[str, Any]] = []
        for index, chapter in enumerate(chapters):
            section = sections[index]
            if index == len(chapters) - 1 and len(sections) > len(chapters):
                section = "\n".join(section.strip() for section in sections[index:]).strip() + "\n"
            chapter_payloads.append({**chapter, "markdown": section})
        return chapter_payloads

    return _split_by_original_chapter_titles(book, markdown_text)


def _load_translated_chapter_payloads(run_dir: Path, book: dict[str, Any], target_language: str) -> list[dict[str, Any]] | None:
    chapters_path = run_dir / "translated-chapters.json"
    if chapters_path.exists():
        try:
            data = json.loads(chapters_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list) and data:
            return [item for item in data if isinstance(item, dict)]

    cache_dir = run_dir / "translation-cache"
    manifest_path = run_dir / "manifest.json"
    if not cache_dir.exists() or not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    translation_settings = manifest.get("translation") if isinstance(manifest.get("translation"), dict) else {}
    settings = SimpleNamespace(
        max_chunk_chars=int(translation_settings.get("max_chunk_chars") or 6500),
        source_language=manifest.get("source_language"),
        target_language=manifest.get("target_language") or target_language,
    )
    try:
        translated = translate_book_chapters(
            book=book,
            settings=settings,
            translator=CacheOnlyTranslator(),
            cache_dir=cache_dir,
            retry_count=1,
            concurrency=1,
        )
    except Exception:
        return None

    payloads = [
        {
            "index": chapter.index,
            "chapter_id": chapter.chapter_id,
            "title": chapter.title,
            "page_start": chapter.page_start,
            "page_end": chapter.page_end,
            "markdown": chapter.markdown,
            "source_pages": chapter.source_pages,
            "source_internal_path": chapter.source_internal_path,
            "toc": chapter.toc,
        }
        for chapter in translated.translated_chapters
    ]
    if payloads:
        chapters_path.write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")
    return payloads or None


def _apply_polish_replacements_to_chapters(
    chapters: list[dict[str, Any]], accepted: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    replacements = {
        str(record["before"]).strip(): str(record["after"]).strip()
        for record in accepted
        if str(record.get("before") or "").strip() and str(record.get("after") or "").strip()
    }
    if not replacements:
        return chapters

    patched: list[dict[str, Any]] = []
    for chapter in chapters:
        lines = []
        for line in str(chapter.get("markdown") or "").splitlines():
            leading = line[: len(line) - len(line.lstrip())]
            trailing = line[len(line.rstrip()) :]
            replacement = replacements.get(line.strip())
            lines.append(f"{leading}{replacement}{trailing}" if replacement is not None else line)
        markdown = "\n".join(lines).strip()
        patched.append({**chapter, "markdown": markdown + "\n" if markdown else ""})
    return patched


def _collect_protected_manual_lines(run_dir: Path) -> set[str]:
    from pdf_translator.translation_failures import read_failures

    protected_lines: set[str] = set()
    failure_items = read_failures(run_dir).get("items", {})
    if isinstance(failure_items, dict):
        for item in failure_items.values():
            if not isinstance(item, dict):
                continue
            resolution = item.get("resolution", {})
            if not isinstance(resolution, dict):
                continue
            for line in str(resolution.get("text") or "").splitlines():
                stripped = line.strip()
                if stripped:
                    protected_lines.add(stripped)
    review_state_path = run_dir / "review_state.json"
    if review_state_path.exists():
        try:
            state = json.loads(review_state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
        decisions = state.get("decisions", {})
        if isinstance(decisions, dict):
            for decision in decisions.values():
                if not isinstance(decision, dict):
                    continue
                for line in str(decision.get("approved_text") or "").splitlines():
                    stripped = line.strip()
                    if stripped:
                        protected_lines.add(stripped)
    return protected_lines


def _compute_outcome(
    *,
    candidate_count: int,
    accepted_count: int,
    rejected_count: int,
    unresolved_count: int,
    unchanged_count: int,
) -> PolishOutcome:
    if candidate_count == 0:
        return "no_candidates"
    if rejected_count > 0 or unresolved_count > 0 or unchanged_count > 0:
        return "needs_review"
    if accepted_count == candidate_count and accepted_count > 0:
        return "applied"
    return "needs_review"


def run_polish(
    *,
    run_dir: Path,
    target_language: str = "zh-CN",
    translator_name: str = "minimax",
    translator: BaseTranslator | None = None,
    batch_size: int = 8,
    concurrency: int = 6,
    request_timeout_seconds: float | None = None,
) -> PolishResult:
    run_dir = run_dir.expanduser().resolve()
    book_path = run_dir / "book.json"
    cleaned_path = run_dir / "translated.cleaned.md"
    if not book_path.exists():
        raise FileNotFoundError(f"Missing book.json: {book_path}")
    if not cleaned_path.is_file():
        raise FileNotFoundError(f"Missing translated.cleaned.md: {cleaned_path}")

    book = json.loads(book_path.read_text(encoding="utf-8"))
    markdown_text = _read_text_preserve_newlines(cleaned_path)
    cleaned_input_sha256 = _sha256(markdown_text)
    cleanup_rules_version = _load_cleanup_version(run_dir)
    glossary_entries = _load_glossary_entries(run_dir)
    glossary_terms = _load_glossary_terms(run_dir)
    metadata = book.get("metadata") if isinstance(book.get("metadata"), dict) else {}
    book_title = metadata.get("title") if isinstance(metadata.get("title"), str) else None
    book_author = metadata.get("author") if isinstance(metadata.get("author"), str) else None

    scan_result = scan_polish_candidates_internal(markdown_text)
    all_candidates = scan_result.candidates
    protected_skip_count = scan_result.protected_skip_count
    detected_candidate_count = scan_result.detected_candidate_count
    protected_lines = _collect_protected_manual_lines(run_dir)
    manual_skip_count = sum(
        1 for candidate in all_candidates if candidate.text.strip() in protected_lines
    )
    candidates = [
        candidate for candidate in all_candidates if candidate.text.strip() not in protected_lines
    ]
    protected_terms_by_line = {
        candidate.line: _protected_terms_for_candidate(
            candidate,
            glossary_entries,
            book_title=book_title,
            book_author=book_author,
        )
        for candidate in candidates
    }

    cache_dir = run_dir / "polish-cache"
    model_results: dict[int, CandidateModelResult] = {}
    if candidates:
        active_translator = translator or build_translator(translator_name)
        model_results = _translate_candidates(
            candidates=candidates,
            translator=active_translator,
            target_language=target_language,
            cache_dir=cache_dir,
            batch_size=max(1, batch_size),
            concurrency=max(1, concurrency),
            request_timeout_seconds=request_timeout_seconds,
            cleanup_rules_version=cleanup_rules_version,
            protected_terms_by_line=protected_terms_by_line,
        )
        translator_name_for_report = active_translator.name
    else:
        translator_name_for_report = translator.name if translator is not None else translator_name

    bare_lines, endings, newline_style, has_final_newline = _document_line_parts(markdown_text)
    polished_bare = list(bare_lines)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for candidate in candidates:
        line_index = candidate.line - 1
        if line_index < 0 or line_index >= len(polished_bare):
            continue
        before = polished_bare[line_index]
        model_result = model_results.get(candidate.line)
        if model_result is None:
            unresolved.append(
                {
                    "line": candidate.line,
                    "suspects": candidate.suspects,
                    "text": before,
                    "decision": "model_unavailable",
                }
            )
            continue
        if model_result.decision in {"model_unavailable", "incomplete_response"}:
            unresolved.append(
                {
                    "line": candidate.line,
                    "suspects": candidate.suspects,
                    "text": before,
                    "decision": model_result.decision,
                }
            )
            continue
        after = model_result.polished_text if model_result.polished_text is not None else before
        candidate_glossary_terms = protected_terms_by_line.get(candidate.line, glossary_terms)
        if after == before:
            unchanged.append(
                {
                    "line": candidate.line,
                    "suspects": candidate.suspects,
                    "text": before,
                    "decision": "unchanged",
                }
            )
            continue
        after_spans = protected_spans(after)
        if _suspect_still_present(after, candidate.suspects, after_spans):
            rejected.append(
                {
                    "line": candidate.line,
                    "suspects": candidate.suspects,
                    "before": before,
                    "after": after,
                    "decision": "suspect_unresolved",
                }
            )
            continue
        accept_context = PolishAcceptContext(
            suspects=candidate.suspects,
            protected_literals=_protected_literals(before),
            glossary_terms=candidate_glossary_terms,
            book_title=book_title,
            book_author=book_author,
        )
        trial_lines = list(polished_bare)
        trial_lines[line_index] = after
        whole_after = _join_document_lines(trial_lines, endings, newline_style, has_final_newline)
        whole_before = _join_document_lines(polished_bare, endings, newline_style, has_final_newline)
        ok, reason = _safe_accept_polish(
            before,
            after,
            context=accept_context,
            whole_before=whole_before,
            whole_after=whole_after,
        )
        record = {
            "line": candidate.line,
            "suspects": candidate.suspects,
            "before": before,
            "after": after,
            "decision": reason,
        }
        if ok:
            polished_bare[line_index] = after
            accepted.append(record)
        else:
            rejected.append(record)

    polished_markdown = _join_document_lines(polished_bare, endings, newline_style, has_final_newline)
    if markdown_block_structure(markdown_text) != markdown_block_structure(polished_markdown):
        raise ValueError("Polish output changed document Markdown block structure.")
    if len(markdown_text.splitlines()) != len(polished_markdown.splitlines()):
        raise ValueError("Polish output changed document line count.")

    polished_markdown_path = run_dir / "translated.polished.md"
    _write_text_preserve_newlines(polished_markdown_path, polished_markdown)

    translated_chapters = _load_translated_chapter_payloads(run_dir, book, target_language)
    if translated_chapters:
        polished_chapters = _apply_polish_replacements_to_chapters(translated_chapters, accepted)
    else:
        polished_chapters = _split_polished_markdown_into_chapters(book, polished_markdown)
    delivery_stem = safe_delivery_file_stem(Path(run_dir.name), f"{target_language} polished")
    polished_epub_path = run_dir / f"{delivery_stem}.epub"
    render_epub_from_book(
        book=book,
        translated_chapters=polished_chapters,
        output_path=polished_epub_path,
        title=f"{run_dir.name} ({target_language} polished)",
        language=target_language,
    )

    needs_review_count = len(rejected) + len(unresolved) + len(unchanged)
    outcome = _compute_outcome(
        candidate_count=len(candidates),
        accepted_count=len(accepted),
        rejected_count=len(rejected),
        unresolved_count=len(unresolved),
        unchanged_count=len(unchanged),
    )
    report = {
        "schema": "polish_report_v1",
        "run_dir": str(run_dir),
        "target_language": target_language,
        "translator": translator_name_for_report,
        "polish_prompt_version": POLISH_PROMPT_VERSION,
        "zh_cleanup_rules_version": cleanup_rules_version,
        "cleaned_input_sha256": cleaned_input_sha256,
        "output_sha256": _sha256(polished_markdown),
        "detected_candidate_count": detected_candidate_count,
        "candidate_count": len(candidates),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "unchanged_count": len(unchanged),
        "needs_review_count": needs_review_count,
        "outcome": outcome,
        "protected_skip_count": protected_skip_count,
        "manual_skip_count": manual_skip_count,
        "outputs": {
            "translated_polished_markdown": str(polished_markdown_path),
            "translated_polished_epub": str(polished_epub_path),
            "polish_cache_dir": str(cache_dir),
            "translated_cleaned_markdown": str(cleaned_path),
        },
        "accepted": accepted,
        "rejected": rejected,
        "unchanged": unchanged,
        "unresolved": unresolved,
        "decisions": accepted + rejected + unchanged + unresolved,
    }
    report_path = run_dir / "polish-report.json"
    atomic_json(report_path, report)

    return PolishResult(
        run_dir=run_dir,
        polished_markdown_path=polished_markdown_path,
        polished_epub_path=polished_epub_path,
        report_path=report_path,
        detected_candidate_count=detected_candidate_count,
        candidate_count=len(candidates),
        accepted_count=len(accepted),
        rejected_count=len(rejected),
        changed_count=len(accepted),
        unchanged_count=len(unchanged),
        needs_review_count=needs_review_count,
        outcome=outcome,
        protected_skip_count=protected_skip_count,
        manual_skip_count=manual_skip_count,
    )
