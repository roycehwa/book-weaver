from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.chunking import split_markdown_into_chunks
from pdf_translator.models import TranslationChunk
from pdf_translator.translate import (
    BaseTranslator,
    _assert_translation_quality,
    _chapter_markdown_for_translation,
    _is_preserved_apparatus_block,
    _split_markdown_media_segments,
    _split_sensitive_source,
    _strip_generated_english_chinese_glosses,
    _translate_sensitive_part,
    _try_fallback_translation,
    _chunk_cache_path,
    build_translator,
)


def iter_global_chunks(book: dict, *, max_chunk_chars: int = 9000) -> list[TranslationChunk]:
    chunks: list[TranslationChunk] = []
    chunk_index = 0
    for chapter in book.get("chapters", []):
        chapter_source = _chapter_markdown_for_translation(chapter)
        if not bool(chapter.get("translate", True)):
            continue
        media_segments = (
            [("media", chapter_source.strip())]
            if _is_preserved_apparatus_block(chapter_source)
            else _split_markdown_media_segments(chapter_source)
        )
        for segment_kind, segment_markdown in media_segments:
            if segment_kind == "media":
                continue
            for source_chunk in split_markdown_into_chunks(segment_markdown, max_chunk_chars):
                chunks.append(TranslationChunk(index=chunk_index, markdown=source_chunk.markdown))
                chunk_index += 1
    return chunks


def load_run_book(run_dir: Path) -> dict:
    book_path = run_dir / "book.json"
    if not book_path.exists():
        raise FileNotFoundError(f"Missing book.json in {run_dir}")
    return json.loads(book_path.read_text(encoding="utf-8"))


def extract_global_chunk(run_dir: Path, chunk_index: int, *, max_chunk_chars: int = 9000) -> str:
    book = load_run_book(run_dir)
    chunks = iter_global_chunks(book, max_chunk_chars=max_chunk_chars)
    for chunk in chunks:
        if chunk.index == chunk_index:
            return chunk.markdown
    raise ValueError(f"Chunk {chunk_index} not found in {run_dir}")


def inject_global_chunk_cache(
    run_dir: Path,
    chunk_index: int,
    translated: str,
    *,
    max_chunk_chars: int = 9000,
) -> Path:
    source = extract_global_chunk(run_dir, chunk_index, max_chunk_chars=max_chunk_chars)
    chunk = TranslationChunk(index=chunk_index, markdown=source)
    cache_dir = run_dir / "translation-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _chunk_cache_path(cache_dir, chunk)
    cache_path.write_text(translated.strip() + "\n", encoding="utf-8")
    return cache_path


def _split_for_sensitive_translation(source: str, *, max_part_chars: int = 2800) -> list[str]:
    return _split_sensitive_source(source, max_part_chars=max_part_chars)


def _translate_split_parts(
    *,
    chunk_index: int,
    parts: list[str],
    source_language: str | None,
    target_language: str,
    translator: BaseTranslator,
) -> str:
    translated_parts: list[str] = []
    for offset, part in enumerate(parts):
        part_chunk = TranslationChunk(index=chunk_index * 1000 + offset, markdown=part)
        translated_parts.append(
            _translate_sensitive_part(
                chunk=part_chunk,
                source_language=source_language,
                target_language=target_language,
                translator=translator,
                retry_count=3,
            )
        )
    return "\n\n".join(part.strip() for part in translated_parts if part.strip())


def salvage_chunk_via_split_translation(
    run_dir: Path,
    chunk_index: int,
    *,
    translator_name: str = "minimax",
    source_language: str | None = "en",
    target_language: str = "zh-CN",
    max_chunk_chars: int = 9000,
    max_part_chars: int = 2800,
    translator: BaseTranslator | None = None,
) -> Path:
    source = extract_global_chunk(run_dir, chunk_index, max_chunk_chars=max_chunk_chars)
    active_translator = translator or build_translator(translator_name)
    chunk = TranslationChunk(index=chunk_index, markdown=source)

    try:
        translated = active_translator.translate_chunk(
            chunk=chunk,
            source_language=source_language,
            target_language=target_language,
        ).strip()
        if translated:
            translated = _strip_generated_english_chinese_glosses(source, translated, target_language)
            _assert_translation_quality(
                chunk=chunk,
                translated=translated,
                target_language=target_language,
                translator_name=active_translator.name,
            )
            return inject_global_chunk_cache(run_dir, chunk_index, translated, max_chunk_chars=max_chunk_chars)
    except ValueError as exc:
        if "new_sensitive" not in str(exc).lower():
            pass
        else:
            fallback_translated = _try_fallback_translation(
                chunk=chunk,
                source_language=source_language,
                target_language=target_language,
                primary_translator=active_translator,
                cache_path=None,
            )
            if fallback_translated is not None:
                return inject_global_chunk_cache(
                    run_dir,
                    chunk_index,
                    fallback_translated,
                    max_chunk_chars=max_chunk_chars,
                )

    part_sizes = []
    for size in (max_part_chars, 1400, 900, 500):
        if size not in part_sizes:
            part_sizes.append(size)

    last_error: Exception | None = None
    translated = ""
    sensitive_failure = False
    for part_size in part_sizes:
        try:
            translated = _translate_split_parts(
                chunk_index=chunk_index,
                parts=_split_for_sensitive_translation(source, max_part_chars=part_size),
                source_language=source_language,
                target_language=target_language,
                translator=active_translator,
            )
            chunk = TranslationChunk(index=chunk_index, markdown=source)
            _assert_translation_quality(
                chunk=chunk,
                translated=translated,
                target_language=target_language,
                translator_name=active_translator.name,
            )
            break
        except ValueError as exc:
            last_error = exc
            message = str(exc).lower()
            if "new_sensitive" in message:
                sensitive_failure = True
                break
            if "looks untranslated" in message or "looks incomplete" in message:
                continue
            raise
    else:
        chunk = TranslationChunk(index=chunk_index, markdown=source)
        fallback_translated = None
        if sensitive_failure:
            fallback_translated = _try_fallback_translation(
                chunk=chunk,
                source_language=source_language,
                target_language=target_language,
                primary_translator=active_translator,
                cache_path=None,
            )
        if fallback_translated is not None:
            return inject_global_chunk_cache(
                run_dir,
                chunk_index,
                fallback_translated,
                max_chunk_chars=max_chunk_chars,
            )
        assert last_error is not None
        raise last_error

    return inject_global_chunk_cache(run_dir, chunk_index, translated, max_chunk_chars=max_chunk_chars)


def first_missing_chunk_index(run_dir: Path, *, max_chunk_chars: int = 9000) -> int | None:
    book = load_run_book(run_dir)
    total = len(iter_global_chunks(book, max_chunk_chars=max_chunk_chars))
    cached = {int(path.name.split("-")[1]) for path in (run_dir / "translation-cache").glob("chunk-*.md")}
    for index in range(total):
        if index not in cached:
            return index
    return None


def list_missing_chunk_indices(run_dir: Path, *, max_chunk_chars: int = 9000) -> list[int]:
    book = load_run_book(run_dir)
    total = len(iter_global_chunks(book, max_chunk_chars=max_chunk_chars))
    cached = {int(path.name.split("-")[1]) for path in (run_dir / "translation-cache").glob("chunk-*.md")}
    return [index for index in range(total) if index not in cached]


def salvage_all_missing_chunks(
    run_dir: Path,
    *,
    translator_name: str = "minimax",
    source_language: str | None = "en",
    target_language: str = "zh-CN",
    max_chunks: int = 20,
) -> tuple[int, list[tuple[int, str]]]:
    salvaged = 0
    failures: list[tuple[int, str]] = []
    for _ in range(max_chunks):
        chunk_index = first_missing_chunk_index(run_dir)
        if chunk_index is None:
            break
        try:
            salvage_chunk_via_split_translation(
                run_dir,
                chunk_index,
                translator_name=translator_name,
                source_language=source_language,
                target_language=target_language,
            )
            salvaged += 1
        except Exception as exc:
            failures.append((chunk_index, str(exc)))
            break
    return salvaged, failures
