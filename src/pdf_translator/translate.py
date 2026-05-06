from __future__ import annotations

import json
import hashlib
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI

from pdf_translator.chunking import split_markdown_into_chunks
from pdf_translator.config import (
    DEFAULT_MINIMAX_MAX_TOKENS,
    CompatibleAPISettings,
    OpenAISettings,
    RunSettings,
)
from pdf_translator.models import BookTranslationResult, TranslatedChapter, TranslationChunk, TranslationResult


SYSTEM_PROMPT = """You are a professional document translator.

Translate the user-provided Markdown into the target language.

Rules:
- Preserve Markdown structure exactly where practical.
- Keep headings, lists, tables, links, and code fences intact.
- Do not translate URLs, code, citation keys, raw numbers, or obvious identifiers.
- Translate natural language in image alt text if present.
- When the target language is Chinese, translate English prose into Chinese. Do not return the source prose unchanged.
- Translate completely. Do not summarize, shorten, skip paragraphs, or replace content with an overview.
- Return only translated Markdown, with no commentary.
"""


def _translation_prompt(chunk: TranslationChunk, source_language: str | None, target_language: str) -> str:
    source = source_language or "auto-detect"
    return (
        f"Source language: {source}\n"
        f"Target language: {target_language}\n"
        f"Markdown chunk index: {chunk.index}\n\n"
        f"{chunk.markdown}"
    )


def _chunk_cache_path(cache_dir: Path, chunk: TranslationChunk) -> Path:
    digest = hashlib.sha256(chunk.markdown.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"chunk-{chunk.index:06d}-{digest}.md"


def _ascii_letter_count(text: str) -> int:
    return sum(1 for char in text if char.isascii() and char.isalpha())


def _cjk_count(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def _looks_untranslated_for_target(source: str, translated: str, target_language: str) -> bool:
    if not target_language.lower().startswith("zh"):
        return False
    source_ascii = _ascii_letter_count(source)
    if source_ascii < 300:
        return False
    translated_ascii = _ascii_letter_count(translated)
    translated_cjk = _cjk_count(translated)
    # A valid zh translation may preserve names/citations, but it should not be overwhelmingly ASCII.
    if translated_cjk < 80 and translated_ascii > 250:
        return True
    return translated_ascii / max(translated_ascii + translated_cjk, 1) > 0.72


def _looks_incomplete_for_target(source: str, translated: str, target_language: str) -> bool:
    if not target_language.lower().startswith("zh"):
        return False
    source_alpha = _ascii_letter_count(source)
    if source_alpha < 1200:
        return False
    translated_signal = _cjk_count(translated) + int(_ascii_letter_count(translated) * 0.35)
    # English-to-Chinese usually compresses, but not by an order of magnitude.
    return translated_signal < source_alpha * 0.18


def _assert_translation_quality(
    *,
    chunk: TranslationChunk,
    translated: str,
    target_language: str,
    translator_name: str,
) -> None:
    if translator_name == "mock":
        return
    if _looks_untranslated_for_target(chunk.markdown, translated, target_language):
        raise ValueError(
            f"Translation for chunk {chunk.index} looks untranslated "
            f"(ascii={_ascii_letter_count(translated)}, cjk={_cjk_count(translated)})."
        )
    if _looks_incomplete_for_target(chunk.markdown, translated, target_language):
        raise ValueError(
            f"Translation for chunk {chunk.index} looks incomplete "
            f"(source_ascii={_ascii_letter_count(chunk.markdown)}, "
            f"translated_ascii={_ascii_letter_count(translated)}, cjk={_cjk_count(translated)})."
        )


def _translate_chunk_resumable(
    *,
    chunk: TranslationChunk,
    source_language: str | None,
    target_language: str,
    translator: BaseTranslator,
    cache_dir: Path | None,
    retry_count: int = 3,
) -> str:
    cache_path: Path | None = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = _chunk_cache_path(cache_dir, chunk)
        if cache_path.exists():
            cached = cache_path.read_text(encoding="utf-8").strip()
            if cached:
                try:
                    _assert_translation_quality(
                        chunk=chunk,
                        translated=cached,
                        target_language=target_language,
                        translator_name=translator.name,
                    )
                except ValueError:
                    cache_path.unlink(missing_ok=True)
                else:
                    return cached

    last_error: Exception | None = None
    for attempt in range(max(1, retry_count)):
        try:
            translated = translator.translate_chunk(
                chunk=chunk,
                source_language=source_language,
                target_language=target_language,
            ).strip()
            if not translated:
                raise ValueError(f"Empty translation returned for chunk {chunk.index}.")
            _assert_translation_quality(
                chunk=chunk,
                translated=translated,
                target_language=target_language,
                translator_name=translator.name,
            )
            if cache_path is not None:
                tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
                tmp_path.write_text(translated + "\n", encoding="utf-8")
                tmp_path.replace(cache_path)
            return translated
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= max(1, retry_count):
                break
            time.sleep(min(2**attempt, 8))

    raise ValueError(f"Translation failed for chunk {chunk.index} after {retry_count} attempts: {last_error}") from last_error


def _translate_chunks_ordered(
    *,
    chunks: list[TranslationChunk],
    source_language: str | None,
    target_language: str,
    translator: BaseTranslator,
    cache_dir: Path | None,
    retry_count: int,
    concurrency: int,
) -> list[str]:
    if concurrency <= 1 or len(chunks) <= 1:
        return [
            _translate_chunk_resumable(
                chunk=chunk,
                source_language=source_language,
                target_language=target_language,
                translator=translator,
                cache_dir=cache_dir,
                retry_count=retry_count,
            )
            for chunk in chunks
        ]

    translated: list[str | None] = [None] * len(chunks)
    with ThreadPoolExecutor(max_workers=min(concurrency, len(chunks))) as executor:
        futures = {
            executor.submit(
                _translate_chunk_resumable,
                chunk=chunk,
                source_language=source_language,
                target_language=target_language,
                translator=translator,
                cache_dir=cache_dir,
                retry_count=retry_count,
            ): position
            for position, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            translated[futures[future]] = future.result()

    return [part or "" for part in translated]


class BaseTranslator(ABC):
    name: str

    @abstractmethod
    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        raise NotImplementedError


class MockTranslator(BaseTranslator):
    name = "mock"

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        return chunk.markdown


class OpenAITranslator(BaseTranslator):
    name = "openai"

    def __init__(self, settings: OpenAISettings) -> None:
        client_kwargs: dict[str, str] = {"api_key": settings.api_key}
        if settings.base_url:
            client_kwargs["base_url"] = settings.base_url
        self.client = OpenAI(**client_kwargs)
        self.model = settings.model

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        prompt = _translation_prompt(chunk, source_language, target_language)
        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.output_text.strip()
        if not text:
            raise ValueError(f"Empty translation returned for chunk {chunk.index}.")
        return text


class OpenAICompatibleTranslator(BaseTranslator):
    name = "compatible"

    def __init__(self, settings: CompatibleAPISettings, *, name: str = "compatible") -> None:
        self.name = name
        self.client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
        self.model = settings.model

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        prompt = _translation_prompt(chunk, source_language, target_language)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError(f"Empty translation returned for chunk {chunk.index}.")
        return text


class MiniMaxAnthropicTranslator(BaseTranslator):
    name = "minimax"

    def __init__(self, settings: CompatibleAPISettings) -> None:
        self.api_key = settings.api_key
        self.endpoint = settings.base_url
        self.model = settings.model
        self.max_tokens = settings.max_tokens or DEFAULT_MINIMAX_MAX_TOKENS
        # Long-form translation can exceed a 2-minute round-trip; override with MINIMAX_HTTP_TIMEOUT_SECONDS.
        self.http_timeout = float(os.getenv("MINIMAX_HTTP_TIMEOUT_SECONDS", "600"))

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": _translation_prompt(chunk, source_language, target_language),
                }
            ],
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.http_timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise ValueError(
                f"MiniMax translation failed for chunk {chunk.index}: "
                f"HTTP {exc.code}: {error_body[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"MiniMax translation failed for chunk {chunk.index}: {exc.reason}") from exc

        text_parts: list[str] = []
        for item in response_data.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                text_parts.append(item)

        text = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
        if not text:
            raise ValueError(f"Empty MiniMax translation returned for chunk {chunk.index}.")
        if response_data.get("stop_reason") == "max_tokens":
            raise ValueError(
                f"MiniMax translation was truncated for chunk {chunk.index} "
                f"(stop_reason=max_tokens, max_tokens={self.max_tokens}). "
                "Increase MINIMAX_MAX_TOKENS (or MINIMAX_HTTP_TIMEOUT_SECONDS if the request timed out early). "
                "Only reduce --max-chunk-chars if raising max_tokens is not enough."
            )
        return text


def build_translator(name: str) -> BaseTranslator:
    normalized = name.strip().lower()
    if normalized == "mock":
        return MockTranslator()
    if normalized == "openai":
        return OpenAITranslator(OpenAISettings.from_env())
    if normalized in {"compatible", "openai-compatible"}:
        return OpenAICompatibleTranslator(CompatibleAPISettings.from_env("compatible"))
    if normalized == "minimax":
        return MiniMaxAnthropicTranslator(CompatibleAPISettings.from_env("minimax"))
    raise ValueError(f"Unsupported translator backend: {name}")


def translate_markdown(
    *,
    chunks: list[TranslationChunk],
    settings: RunSettings,
    translator: BaseTranslator,
    cache_dir: Path | None = None,
    retry_count: int = 3,
    concurrency: int = 1,
) -> TranslationResult:
    translated_chunks = _translate_chunks_ordered(
        chunks=chunks,
        source_language=settings.source_language,
        target_language=settings.target_language,
        translator=translator,
        cache_dir=cache_dir,
        retry_count=retry_count,
        concurrency=concurrency,
    )

    return TranslationResult(
        translated_markdown="\n\n".join(translated_chunks).strip() + "\n",
        source_language=settings.source_language,
        target_language=settings.target_language,
        translator=translator.name,
        chunk_count=len(chunks),
    )


def _chapter_markdown_for_translation(chapter: dict) -> str:
    title = str(chapter.get("title") or f"Chapter {chapter.get('index', '')}").strip()
    markdown = str(chapter.get("markdown") or "").strip()
    if title.startswith("Untitled Section"):
        return markdown + "\n" if markdown else ""
    return f"# {title}\n\n{markdown}\n" if markdown else f"# {title}\n"


def _is_markdown_table_block(block: str) -> bool:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    if lines[0].startswith("**Table "):
        return True
    return any(line.startswith("|") and "---" in line for line in lines[1:3])


def _is_preserved_media_block(block: str) -> bool:
    stripped = block.lstrip()
    return stripped.startswith("![") or _is_markdown_table_block(block)


def _protect_media_blocks(markdown_text: str) -> tuple[str, dict[str, str]]:
    blocks = markdown_text.split("\n\n")
    replacements: dict[str, str] = {}
    protected_blocks: list[str] = []
    for block in blocks:
        if _is_preserved_media_block(block):
            token = f"[[PRESERVE_ORIGINAL_BLOCK_{len(replacements):04d}]]"
            replacements[token] = block
            protected_blocks.append(token)
        else:
            protected_blocks.append(block)
    return "\n\n".join(protected_blocks), replacements


def _restore_media_blocks(markdown_text: str, replacements: dict[str, str]) -> str:
    restored = markdown_text
    for token, original in replacements.items():
        restored = restored.replace(token, original)
        restored = restored.replace(token.replace("[[", "").replace("]]", ""), original)
    return restored


def _split_markdown_media_segments(markdown_text: str) -> list[tuple[str, str]]:
    blocks = markdown_text.split("\n\n")
    segments: list[tuple[str, str]] = []
    text_buffer: list[str] = []

    def flush_text() -> None:
        if not text_buffer:
            return
        text = "\n\n".join(block.strip() for block in text_buffer if block.strip()).strip()
        if text:
            segments.append(("text", text))
        text_buffer.clear()

    index = 0
    while index < len(blocks):
        block = blocks[index]
        stripped = block.strip()
        next_block = blocks[index + 1] if index + 1 < len(blocks) else ""
        if stripped.startswith("**Table ") and _is_markdown_table_block(next_block):
            flush_text()
            segments.append(("media", f"{stripped}\n\n{next_block.strip()}"))
            index += 2
            continue
        if _is_preserved_media_block(block):
            flush_text()
            segments.append(("media", block.strip()))
        else:
            text_buffer.append(block)
        index += 1

    flush_text()
    return segments


def estimate_translation_chunk_count(markdown_text: str, max_chunk_chars: int) -> int:
    count = 0
    for segment_kind, segment_markdown in _split_markdown_media_segments(markdown_text):
        if segment_kind == "media":
            continue
        count += len(split_markdown_into_chunks(segment_markdown, max_chunk_chars))
    return count


def translate_book_chapters(
    *,
    book: dict,
    settings: RunSettings,
    translator: BaseTranslator,
    cache_dir: Path | None = None,
    retry_count: int = 3,
    concurrency: int = 1,
) -> BookTranslationResult:
    translated_chapters: list[TranslatedChapter] = []
    translated_markdown_parts: list[str] = []
    chunk_index = 0

    for chapter in book.get("chapters", []):
        chapter_source_markdown = _chapter_markdown_for_translation(chapter)
        if not bool(chapter.get("translate", True)):
            translated_markdown = chapter_source_markdown.strip()
            if translated_markdown:
                translated_markdown += "\n"
                translated_markdown_parts.append(translated_markdown.strip())
            sip = chapter.get("source_internal_path")
            translated_chapters.append(
                TranslatedChapter(
                    index=int(chapter.get("index", len(translated_chapters) + 1)),
                    title=str(chapter.get("title") or f"Chapter {len(translated_chapters) + 1}"),
                    page_start=chapter.get("page_start"),
                    page_end=chapter.get("page_end"),
                    source_pages=[int(page_no) for page_no in chapter.get("source_pages", [])],
                    markdown=translated_markdown,
                    source_internal_path=sip if isinstance(sip, str) else None,
                    toc=bool(chapter.get("toc", True)),
                )
            )
            continue

        translated_parts: list[str] = []
        for segment_kind, segment_markdown in _split_markdown_media_segments(chapter_source_markdown):
            if segment_kind == "media":
                translated_parts.append(segment_markdown)
                continue

            source_chunks = split_markdown_into_chunks(segment_markdown, settings.max_chunk_chars)
            global_chunks = [
                TranslationChunk(index=chunk_index + offset, markdown=source_chunk.markdown)
                for offset, source_chunk in enumerate(source_chunks)
            ]
            translated_parts.extend(
                _translate_chunks_ordered(
                    chunks=global_chunks,
                    source_language=settings.source_language,
                    target_language=settings.target_language,
                    translator=translator,
                    cache_dir=cache_dir,
                    retry_count=retry_count,
                    concurrency=concurrency,
                )
            )
            chunk_index += len(global_chunks)

        translated_markdown = "\n\n".join(part.strip() for part in translated_parts if part.strip()).strip()
        if translated_markdown:
            translated_markdown += "\n"
            translated_markdown_parts.append(translated_markdown.strip())

        sip = chapter.get("source_internal_path")
        translated_chapters.append(
            TranslatedChapter(
                index=int(chapter.get("index", len(translated_chapters) + 1)),
                title=str(chapter.get("title") or f"Chapter {len(translated_chapters) + 1}"),
                page_start=chapter.get("page_start"),
                page_end=chapter.get("page_end"),
                source_pages=[int(page_no) for page_no in chapter.get("source_pages", [])],
                markdown=translated_markdown,
                source_internal_path=sip if isinstance(sip, str) else None,
                toc=bool(chapter.get("toc", True)),
            )
        )

    return BookTranslationResult(
        translated_markdown="\n\n".join(translated_markdown_parts).strip() + "\n",
        translated_chapters=translated_chapters,
        source_language=settings.source_language,
        target_language=settings.target_language,
        translator=translator.name,
        chunk_count=chunk_index,
    )
