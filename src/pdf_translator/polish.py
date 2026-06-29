from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import requests

from pdf_translator.epub import render_epub_from_book
from pdf_translator.models import TranslationChunk
from pdf_translator.pipeline import safe_delivery_file_stem
from pdf_translator.translate import (
    BaseTranslator,
    MiniMaxAnthropicTranslator,
    OpenAICompatibleTranslator,
    OpenAITranslator,
    build_translator,
    translate_book_chapters,
)


POLISH_PROMPT_VERSION = "v3-strip-parenthetical-english"


POLISH_SYSTEM_PROMPT = """你是中文译文精修编辑。任务不是重新翻译整段，而是修正中文译文中突兀夹杂的英文词或英文短语。

规则：
1. 每条输入都有 suspects 字段；这些英文词或短语已被判定为高置信度问题，必须尽量译成中文。
2. 保留人名、地名、书名、机构名、专有名词、音译词。
3. 如果英文裸词或短语后面已有中文括注，例如 popularity（流行）、visual culture（视觉文化），改为只保留中文“流行”、“视觉文化”。
4. 如果中文术语后括注英文原文，例如 “感官（senses）”，保留括注。
5. 保留 Markdown、脚注编号、引用编号、图片/表格标记。
6. 可以为了通顺重写整句，但不能删减信息量，不能缩写段落，不能改变引用编号。
7. 不要把 active、manifest、perceived、living、conception、precisely because 等英文裸词原样留在中文句子里。
8. 只返回 JSON 数组，每项包含 line 和 polished_text。

示例：
- “感官是真实和 active 的核心假设” -> “感官是真实且活跃的核心假设”
- “precisely because its vitality is inherent to every living organism” -> “正是因为其活力内在于每一个生命有机体”
- “物质性被人类 denote 为稳定物体的 perceived solidness” -> “物质性被人类标示为稳定物体所呈现出的坚实感”
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

ENGLISH_WORD_RE = re.compile(r"(?<![A-Za-z])([A-Za-z][A-Za-z'’-]{1,30})(?![A-Za-z])")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
ENGLISH_THEN_CHINESE_RE = re.compile(
    r"(?P<english>[A-Za-z][A-Za-z'’\-/]*(?:\s+[A-Za-z][A-Za-z'’\-/]*){0,6})\s*[（(](?P<chinese>[\u4e00-\u9fff][^（）()A-Za-z]{0,80})[）)]"
)


@dataclass(slots=True)
class PolishCandidate:
    line: int
    text: str
    suspects: list[str]
    category: str = "high_confidence"


@dataclass(slots=True)
class PolishResult:
    run_dir: Path
    polished_markdown_path: Path
    polished_epub_path: Path
    report_path: Path
    candidate_count: int
    accepted_count: int
    rejected_count: int
    changed_count: int


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


def _ascii_letter_count(text: str) -> int:
    return sum(1 for char in text if char.isascii() and char.isalpha())


def _cjk_count(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def _is_structural_line(text: str) -> bool:
    stripped = text.strip()
    return not stripped or stripped.startswith(("#", "![", "|", ">", "```"))


def _inside_parenthetical(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 2) : start]
    after = text[end : end + 2]
    return "(" in before or "（" in before or ")" in after or "）" in after


def scan_polish_candidates(markdown_text: str) -> list[PolishCandidate]:
    candidates: list[PolishCandidate] = []
    for line_no, line in enumerate(markdown_text.splitlines(), 1):
        stripped = line.strip()
        if _is_structural_line(stripped) or not CJK_RE.search(stripped):
            continue
        suspects: list[str] = []
        suspects.extend(match.group("english").strip() for match in ENGLISH_THEN_CHINESE_RE.finditer(stripped))
        for match in ENGLISH_WORD_RE.finditer(stripped):
            word = match.group(1).strip("'’-")
            if not word or word not in HIGH_CONFIDENCE_ENGLISH_WORDS:
                continue
            if _inside_parenthetical(stripped, match.start(), match.end()):
                continue
            if word not in suspects:
                suspects.append(word)
        if suspects:
            candidates.append(PolishCandidate(line=line_no, text=stripped, suspects=suspects))
    return candidates


def _candidate_cache_path(cache_dir: Path, candidate: PolishCandidate) -> Path:
    digest_input = f"{POLISH_PROMPT_VERSION}\n{candidate.text}"
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"line-{candidate.line:06d}-{digest}.json"


def _build_polish_prompt(candidates: list[PolishCandidate]) -> str:
    payload = [
        {
            "line": candidate.line,
            "suspects": candidate.suspects,
            "text": candidate.text,
        }
        for candidate in candidates
    ]
    return "请精修以下中文译文行，必须处理 suspects 中列出的英文夹杂。返回 JSON 数组。\n\n" + json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    )


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _rule_based_polish(text: str) -> str:
    def replace_parenthesized_translation(match: re.Match[str]) -> str:
        chinese = match.group("chinese").strip()
        return chinese

    return ENGLISH_THEN_CHINESE_RE.sub(replace_parenthesized_translation, text)


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
        if isinstance(line, int) and isinstance(polished, str) and polished.strip():
            parsed[line] = polished.strip()
    return parsed


def _safe_accept_polish(before: str, after: str) -> tuple[bool, str]:
    if not after.strip():
        return False, "empty"
    if _is_structural_line(before):
        return False, "structural"
    before_cjk = _cjk_count(before)
    after_cjk = _cjk_count(after)
    if before_cjk >= 80 and after_cjk < before_cjk * 0.82:
        return False, "cjk_drop"
    if len(after) < len(before) * 0.62 and _ascii_letter_count(before) < len(before) * 0.35:
        return False, "length_drop"
    if re.search(r"^(以下是|精修|修改后|译文)", after):
        return False, "commentary"
    if before.count("![") != after.count("!["):
        return False, "image_marker_changed"
    if before.count("[^") != after.count("[^"):
        return False, "footnote_marker_changed"
    return True, "accepted"


def _translate_candidates(
    *,
    candidates: list[PolishCandidate],
    translator: BaseTranslator,
    target_language: str,
    cache_dir: Path,
    batch_size: int,
    concurrency: int,
    request_timeout_seconds: float | None,
) -> dict[int, str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    results: dict[int, str] = {}
    uncached: list[PolishCandidate] = []
    for candidate in candidates:
        rule_polished = _rule_based_polish(candidate.text)
        if rule_polished != candidate.text:
            results[candidate.line] = rule_polished
            continue
        cache_path = _candidate_cache_path(cache_dir, candidate)
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cached = {}
            polished = cached.get("polished_text")
            if isinstance(polished, str) and polished.strip():
                results[candidate.line] = polished.strip()
                continue
        uncached.append(candidate)

    batches = [uncached[index : index + batch_size] for index in range(0, len(uncached), batch_size)]

    def run_batch(batch_index: int, batch: list[PolishCandidate]) -> dict[int, str]:
        prompt = _build_polish_prompt(batch)
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
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(min(2**attempt, 8))
        if isinstance(last_error, IncompletePolishBatchError):
            raise last_error
        raise ValueError(f"Polish batch {batch_index} failed: {last_error}") from last_error

    def run_batch_with_fallback(batch_index: int, batch: list[PolishCandidate]) -> dict[int, str]:
        try:
            return run_batch(batch_index, batch)
        except IncompletePolishBatchError:
            if len(batch) == 1:
                return {}
        except Exception:
            return {}
        fallback_results: dict[int, str] = {}
        for offset, candidate in enumerate(batch):
            single_index = (batch_index + 1) * 1000 + offset
            try:
                fallback_results.update(run_batch(single_index, [candidate]))
            except Exception:
                continue
        return fallback_results

    if batches:
        if concurrency <= 1 or len(batches) <= 1:
            for batch_index, batch in enumerate(batches):
                results.update(run_batch_with_fallback(batch_index, batch))
        else:
            with ThreadPoolExecutor(max_workers=min(concurrency, len(batches))) as executor:
                futures = {
                    executor.submit(run_batch_with_fallback, batch_index, batch): batch
                    for batch_index, batch in enumerate(batches)
                }
                for future in as_completed(futures):
                    results.update(future.result())

    for candidate in candidates:
        polished = results.get(candidate.line)
        if not polished:
            continue
        cache_path = _candidate_cache_path(cache_dir, candidate)
        cache_path.write_text(
            json.dumps(
                {
                    "line": candidate.line,
                    "text": candidate.text,
                    "suspects": candidate.suspects,
                    "polished_text": polished,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return results


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
    translated_path = run_dir / "translated.md"
    if not book_path.exists():
        raise FileNotFoundError(f"Missing book.json: {book_path}")
    if not translated_path.exists():
        raise FileNotFoundError(f"Missing translated.md: {translated_path}")

    book = json.loads(book_path.read_text(encoding="utf-8"))
    markdown_text = translated_path.read_text(encoding="utf-8")
    candidates = scan_polish_candidates(markdown_text)
    translator = translator or build_translator(translator_name)
    cache_dir = run_dir / "polish-cache"
    polished_by_line = _translate_candidates(
        candidates=candidates,
        translator=translator,
        target_language=target_language,
        cache_dir=cache_dir,
        batch_size=max(1, batch_size),
        concurrency=max(1, concurrency),
        request_timeout_seconds=request_timeout_seconds,
    )

    original_lines = markdown_text.splitlines()
    polished_lines = list(original_lines)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []

    for candidate in candidates:
        before = original_lines[candidate.line - 1]
        after = polished_by_line.get(candidate.line, before)
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
        ok, reason = _safe_accept_polish(before, after)
        record = {
            "line": candidate.line,
            "suspects": candidate.suspects,
            "before": before,
            "after": after,
            "decision": reason,
        }
        if ok:
            polished_lines[candidate.line - 1] = after
            accepted.append(record)
        else:
            rejected.append(record)

    polished_markdown = "\n".join(polished_lines) + "\n"
    polished_markdown_path = run_dir / "translated.polished.md"
    polished_markdown_path.write_text(polished_markdown, encoding="utf-8")

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

    report = {
        "schema": "polish_report_v1",
        "run_dir": str(run_dir),
        "target_language": target_language,
        "translator": translator.name,
        "candidate_count": len(candidates),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "unchanged_count": len(unchanged),
        "outputs": {
            "translated_polished_markdown": str(polished_markdown_path),
            "translated_polished_epub": str(polished_epub_path),
            "polish_cache_dir": str(cache_dir),
        },
        "accepted": accepted,
        "rejected": rejected,
        "unchanged": unchanged,
    }
    report_path = run_dir / "polish-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return PolishResult(
        run_dir=run_dir,
        polished_markdown_path=polished_markdown_path,
        polished_epub_path=polished_epub_path,
        report_path=report_path,
        candidate_count=len(candidates),
        accepted_count=len(accepted),
        rejected_count=len(rejected),
        changed_count=len(accepted),
    )
