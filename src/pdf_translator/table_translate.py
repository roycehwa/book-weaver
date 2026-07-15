from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import requests

from pdf_translator.epub import render_epub_from_book
from pdf_translator.pipeline import safe_delivery_file_stem
from pdf_translator.translate import (
    _ascii_letter_count,
    _cjk_count,
    build_translator,
)

TABLE_SYSTEM_PROMPT = (
    "You translate markdown tables from English into Simplified Chinese (zh-CN).\n"
    "Rules:\n"
    "- Preserve exact markdown table syntax: same rows, columns, and pipe layout.\n"
    "- Translate human-readable English in cells only.\n"
    "- Keep unchanged: URLs, checkboxes □, blank fields ___, ph#:, ![image](...) markdown, "
    "HTML-like tags, and proper nouns that must stay Latin.\n"
    "- Do not add or remove rows or columns.\n"
    "- Return ONLY the translated markdown table block with no commentary."
)


@dataclass(slots=True)
class TableTranslateResult:
    run_dir: Path
    translated_table_count: int
    skipped_table_count: int
    translated_markdown_path: Path
    polished_markdown_path: Path
    translated_epub_path: Path
    polished_epub_path: Path
    report_path: Path


def _is_pipe_markdown_table(block: str) -> bool:
    stripped = block.strip()
    if not stripped.startswith("|"):
        return False
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    return any(line.startswith("|") and "---" in line for line in lines[1:4])


def _is_english_heavy_table(block: str) -> bool:
    return _cjk_count(block) < 40 and _ascii_letter_count(block) > 200


def _table_row_count(block: str) -> int:
    return sum(1 for line in block.splitlines() if line.strip().startswith("|"))


def _translate_table_with_minimax(
    block: str,
    *,
    translator,
    source_language: str | None,
    target_language: str,
    retry_count: int = 3,
) -> str:
    source_rows = _table_row_count(block)
    last_error: Exception | None = None
    last_translated_rows: int | None = None
    for attempt in range(max(1, retry_count)):
        extra = ""
        if attempt and last_translated_rows is not None:
            extra = (
                f"\nQuality retry {attempt}: your previous output had {last_translated_rows} pipe rows. "
                f"The source table has exactly {source_rows} markdown pipe rows. "
                "Return all rows with the same pipe structure.\n"
            )
        prompt = (
            f"Source language: {source_language or 'en'}\n"
            f"Target language: {target_language}\n"
            f"The table must contain exactly {source_rows} markdown pipe rows.\n"
            f"{extra}\n"
            f"{block.strip()}"
        )
        payload = {
            "model": translator.model,
            "max_tokens": translator.max_tokens,
            "system": TABLE_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            response = requests.post(
                translator.endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {translator.api_key}",
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "Connection": "close",
                },
                timeout=(10, translator.http_timeout),
                proxies={"http": None, "https": None},
            )
            response.raise_for_status()
            response_data = response.json()
            text_parts = [
                str(item.get("text") or "")
                for item in response_data.get("content", [])
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            text = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
            if not text:
                raise ValueError("Empty table translation returned.")
            if text.startswith("```"):
                text = re.sub(r"^```(?:markdown)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text).strip()
            if _table_row_count(text) != source_rows:
                last_translated_rows = _table_row_count(text)
                raise ValueError(
                    f"Table row count mismatch: source={source_rows} translated={last_translated_rows}"
                )
            if not _is_pipe_markdown_table(text):
                raise ValueError("Translated output is not a markdown pipe table.")
            return text.strip() + "\n"
        except Exception as exc:
            last_error = exc
    raise ValueError(f"Table translation failed after {retry_count} attempts: {last_error}") from last_error


def _cache_path(cache_dir: Path, block: str) -> Path:
    digest = hashlib.sha256(block.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"table-{digest}.md"


def translate_tables_in_markdown(
    markdown: str,
    *,
    translator,
    source_language: str | None,
    target_language: str,
    cache_dir: Path | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> tuple[str, int, int]:
    blocks = markdown.split("\n\n")
    translated_count = 0
    skipped_count = 0
    output_blocks: list[str] = []

    for block in blocks:
        stripped = block.strip()
        if not _is_pipe_markdown_table(stripped):
            output_blocks.append(block)
            continue
        if not _is_english_heavy_table(stripped):
            skipped_count += 1
            output_blocks.append(block)
            continue

        cache_file = _cache_path(cache_dir, stripped) if cache_dir is not None else None
        if cache_file is not None and cache_file.exists():
            translated = cache_file.read_text(encoding="utf-8")
        else:
            try:
                translated = _translate_table_with_minimax(
                    stripped,
                    translator=translator,
                    source_language=source_language,
                    target_language=target_language,
                )
            except Exception as exc:
                if errors is not None:
                    errors.append({"preview": stripped[:160], "error": str(exc)})
                output_blocks.append(block)
                continue
            if cache_file is not None:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(translated, encoding="utf-8")
        translated_count += 1
        output_blocks.append(translated.rstrip("\n"))

    return "\n\n".join(output_blocks) + ("\n" if markdown.endswith("\n") else ""), translated_count, skipped_count


from pdf_translator.book_views import join_chapter_delivery_markdown
    *,
    run_dir: Path,
    target_language: str = "zh-CN",
    translator_name: str = "minimax",
    source_language: str | None = "en",
) -> TableTranslateResult:
    run_dir = run_dir.expanduser().resolve()
    book_path = run_dir / "book.json"
    chapters_path = run_dir / "translated-chapters.json"
    if not book_path.exists():
        raise FileNotFoundError(f"Missing book.json: {book_path}")
    if not chapters_path.exists():
        raise FileNotFoundError(f"Missing translated-chapters.json: {chapters_path}")

    book = json.loads(book_path.read_text(encoding="utf-8"))
    chapters = json.loads(chapters_path.read_text(encoding="utf-8"))
    if not isinstance(chapters, list):
        raise ValueError("translated-chapters.json must contain a list.")

    translator = build_translator(translator_name)
    cache_dir = run_dir / "table-translation-cache"
    report_records: list[dict[str, Any]] = []
    error_records: list[dict[str, Any]] = []
    total_translated = 0
    total_skipped = 0

    patched_chapters: list[dict[str, Any]] = []
    for chapter in chapters:
        markdown = str(chapter.get("markdown") or "")
        updated, translated_count, skipped_count = translate_tables_in_markdown(
            markdown,
            translator=translator,
            source_language=source_language,
            target_language=target_language,
            cache_dir=cache_dir,
            errors=error_records,
        )
        total_translated += translated_count
        total_skipped += skipped_count
        if translated_count:
            report_records.append(
                {
                    "chapter_index": chapter.get("index"),
                    "chapter_title": chapter.get("title"),
                    "translated_tables": translated_count,
                }
            )
        patched_chapters.append({**chapter, "markdown": updated})

    chapters_path.write_text(json.dumps(patched_chapters, ensure_ascii=False, indent=2), encoding="utf-8")
    translated_markdown = join_chapter_delivery_markdown(patched_chapters)
    translated_markdown_path = run_dir / "translated.md"
    polished_markdown_path = run_dir / "translated.polished.md"
    translated_markdown_path.write_text(translated_markdown, encoding="utf-8")
    polished_markdown_path.write_text(translated_markdown, encoding="utf-8")

    delivery_stem = safe_delivery_file_stem(Path(run_dir.name), target_language)
    polished_delivery_stem = safe_delivery_file_stem(Path(run_dir.name), f"{target_language} polished")
    translated_epub_path = run_dir / f"{delivery_stem}.epub"
    polished_epub_path = run_dir / f"{polished_delivery_stem}.epub"
    title_base = run_dir.name

    render_epub_from_book(
        book=book,
        translated_chapters=patched_chapters,
        output_path=translated_epub_path,
        title=f"{title_base} ({target_language})",
        language=target_language,
    )
    render_epub_from_book(
        book=book,
        translated_chapters=patched_chapters,
        output_path=polished_epub_path,
        title=f"{title_base} ({target_language} polished)",
        language=target_language,
    )

    report_path = run_dir / "table-translation-report.json"
    report_path.write_text(
        json.dumps(
            {
                "target_language": target_language,
                "translator": translator_name,
                "translated_table_count": total_translated,
                "skipped_table_count": total_skipped,
                "chapters": report_records,
                "errors": error_records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return TableTranslateResult(
        run_dir=run_dir,
        translated_table_count=total_translated,
        skipped_table_count=total_skipped,
        translated_markdown_path=translated_markdown_path,
        polished_markdown_path=polished_markdown_path,
        translated_epub_path=translated_epub_path,
        polished_epub_path=polished_epub_path,
        report_path=report_path,
    )
