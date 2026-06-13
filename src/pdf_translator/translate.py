from __future__ import annotations

import json
import hashlib
import os
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from pathlib import Path

from openai import OpenAI
import requests

from pdf_translator.chunking import split_markdown_into_chunks
from pdf_translator.config import (
    DEFAULT_MINIMAX_MAX_TOKENS,
    CompatibleAPISettings,
    OpenAISettings,
    RunSettings,
)
from pdf_translator.models import BookTranslationResult, TranslatedChapter, TranslationChunk, TranslationResult


TRANSLATION_PROMPT_VERSION = "v3-quality-retry"


SYSTEM_PROMPT = """You are a professional document translator.

Translate the user-provided Markdown into the target language.

Rules:
- Preserve Markdown structure exactly where practical.
- Keep headings, lists, tables, links, and code fences intact.
- Do not translate URLs, code, citation keys, raw numbers, or obvious identifiers.
- Translate natural language in image alt text if present.
- When the target language is Chinese, translate English prose into Chinese. Do not return the source prose unchanged.
- When the target language is Chinese, do not invent bilingual glosses like "perspective（视角）" or "visual culture（视觉文化）".
- If a source English term should be translated, write only the Chinese translation. If the original text did not contain parentheses, do not add parentheses just to show the source English.
- Keep source English only for names, titles, citations, identifiers, or terms that genuinely should remain untranslated.
- Translate completely. Do not summarize, shorten, skip paragraphs, or replace content with an overview.
- Return only translated Markdown, with no commentary.
"""


ENGLISH_THEN_CHINESE_GLOSS_RE = re.compile(
    r"[A-Za-z][A-Za-z'’\-/]*(?:\s+[A-Za-z][A-Za-z'’\-/]*){0,6}\s*[（(][\u4e00-\u9fff][^（）()A-Za-z]{0,80}[）)]"
)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'’-]{3,}\b")
URL_OR_EMAIL_RE = re.compile(r"(?:https?://|www\.)\S+|\S+@\S+")
MARKDOWN_LINK_DEST_RE = re.compile(r"\]\([^)]+\)")
ALLOWED_MIXED_LATIN_WORDS = {
    "press",
    "copyright",
    "license",
    "email",
    "figure",
    "table",
    "chapter",
    "appendix",
    "notes",
    "index",
}


def _translation_prompt(
    chunk: TranslationChunk,
    source_language: str | None,
    target_language: str,
    *,
    quality_retry: str | None = None,
) -> str:
    source = source_language or "auto-detect"
    retry_note = (
        "\nQuality retry: the previous output failed validation because it was not fully translated. "
        "Translate every natural-language sentence completely into the target language now. "
        "Do not return the source text unchanged.\n"
        if quality_retry
        else ""
    )
    return (
        f"Source language: {source}\n"
        f"Target language: {target_language}\n"
        f"Markdown chunk index: {chunk.index}\n"
        f"{retry_note}\n"
        f"{chunk.markdown}"
    )


def _chunk_cache_path(cache_dir: Path, chunk: TranslationChunk) -> Path:
    digest_input = f"{TRANSLATION_PROMPT_VERSION}\n{chunk.markdown}"
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"chunk-{chunk.index:06d}-{digest}.md"


def _read_chunk_cache(cache_dir: Path, chunk: TranslationChunk) -> str:
    """Return cached translation for a chunk, falling back to index-only filenames."""
    cache_path = _chunk_cache_path(cache_dir, chunk)
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8").strip()
    matches = sorted(cache_dir.glob(f"chunk-{chunk.index:06d}-*.md"))
    if len(matches) == 1:
        return matches[0].read_text(encoding="utf-8").strip()
    return ""


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
    if source_ascii < 1000 and translated_cjk >= 160:
        return False
    if _looks_reference_or_note_heavy(source) and (translated_cjk >= 200 or source_ascii < 1200):
        return False
    # For data-heavy content (tables, appendices), high CJK count indicates valid translation
    if translated_cjk >= 1000:
        return False
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


def _english_then_chinese_gloss_count(text: str) -> int:
    return len(ENGLISH_THEN_CHINESE_GLOSS_RE.findall(text))


def _mixed_untranslated_english_signal(text: str) -> tuple[int, int, int]:
    """Return (suspect_word_count, mixed_line_count, max_line_suspects).

    This targets the failure mode where a model returns mostly Chinese but
    leaves natural-language English phrases inside Chinese sentences. It
    deliberately ignores structural/image lines, URLs, emails, Markdown link
    destinations, all-caps acronyms, and likely names.
    """

    suspect_word_count = 0
    mixed_line_count = 0
    max_line_suspects = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "![", "|", ">", "```")):
            continue
        if not CJK_RE.search(line):
            continue
        cleaned = URL_OR_EMAIL_RE.sub(" ", line)
        cleaned = MARKDOWN_LINK_DEST_RE.sub("]", cleaned)
        line_suspects = 0
        for match in LATIN_WORD_RE.finditer(cleaned):
            word = match.group(0).strip("'’”-").lower()
            if not word or word in ALLOWED_MIXED_LATIN_WORDS:
                continue
            original = match.group(0)
            if original.isupper() and len(original) <= 8:
                continue
            if original[:1].isupper() and original[1:].islower():
                # Most remaining title-case words in mixed CJK lines are names.
                continue
            if not any(char.islower() for char in original):
                continue
            line_suspects += 1
        if line_suspects:
            mixed_line_count += 1
            suspect_word_count += line_suspects
            max_line_suspects = max(max_line_suspects, line_suspects)
    return suspect_word_count, mixed_line_count, max_line_suspects


def _looks_reference_or_note_heavy(source: str) -> bool:
    lower = source.lower()
    if re.search(r"^#{1,3}\s+(?:notes?|references|bibliography|selective\s*bibliography|works cited|secondary sources|case law)\b", source, re.MULTILINE | re.IGNORECASE):
        return True
    citation_markers = len(re.findall(r"\b(?:vol\.|no\.|pp\.|doi:|https?://|www\.|press|university|journal|review|chronicle|proceedings)\b", lower))
    year_markers = len(re.findall(r"\((?:1[5-9]\d{2}|20\d{2})\)|\b(?:1[5-9]\d{2}|20\d{2})\b", source))
    note_markers = len(re.findall(r"^\s*(?:\d+|[*†‡])\s+", source, re.MULTILINE))
    return citation_markers + year_markers + note_markers >= 8


def _allow_mixed_english_for_target(source: str, translated: str, target_language: str) -> bool:
    if not target_language.lower().startswith("zh"):
        return False
    translated_cjk = _cjk_count(translated)
    if translated_cjk < 160:
        return False
    source_ascii = _ascii_letter_count(source)
    if source_ascii < 1000:
        return True
    if _looks_reference_or_note_heavy(source) and translated_cjk >= 200:
        return True
    translated_ascii = _ascii_letter_count(translated)
    # Dense scholarly prose often preserves technical terms, quoted terms, names, and citations.
    # The gross untranslated / incomplete checks above catch the real failure modes; this gate
    # should not reject otherwise substantial Chinese output for retained terminology.
    return translated_cjk >= max(300, int(source_ascii * 0.12)) and translated_ascii <= translated_cjk * 3.5


def _strip_generated_english_chinese_glosses(source: str, translated: str, target_language: str) -> str:
    if not target_language.lower().startswith("zh"):
        return translated
    source_count = _english_then_chinese_gloss_count(source)
    translated_count = _english_then_chinese_gloss_count(translated)
    if translated_count == 0 or translated_count <= source_count:
        return translated
    return ENGLISH_THEN_CHINESE_GLOSS_RE.sub(lambda match: re.search(r"[（(](.*)[）)]", match.group(0)).group(1), translated)


def _looks_polluted_by_generated_glosses(source: str, translated: str, target_language: str) -> bool:
    if not target_language.lower().startswith("zh"):
        return False
    translated_count = _english_then_chinese_gloss_count(translated)
    if translated_count < 2:
        return False
    source_count = _english_then_chinese_gloss_count(source)
    if translated_count <= 5 and source_count == 0:
        return False
    # A small number can exist in source notes/tables. The failure mode here is generated repeatedly in output.
    return translated_count > source_count + 1


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
    if _looks_polluted_by_generated_glosses(chunk.markdown, translated, target_language):
        raise ValueError(
            f"Translation for chunk {chunk.index} contains generated English-Chinese glosses "
            f"(source_glosses={_english_then_chinese_gloss_count(chunk.markdown)}, "
            f"translated_glosses={_english_then_chinese_gloss_count(translated)})."
        )
    if target_language.lower().startswith("zh") and _ascii_letter_count(chunk.markdown) < 1000 and _cjk_count(translated) >= 160:
        return
    suspect_words, mixed_lines, max_line_suspects = _mixed_untranslated_english_signal(translated)
    if suspect_words >= 18 and mixed_lines >= 2 and not _allow_mixed_english_for_target(chunk.markdown, translated, target_language):
        raise ValueError(
            f"Translation for chunk {chunk.index} contains mixed untranslated English "
            f"(suspect_words={suspect_words}, mixed_lines={mixed_lines}, max_line={max_line_suspects})."
        )
    if max_line_suspects >= 10 and not _allow_mixed_english_for_target(chunk.markdown, translated, target_language):
        raise ValueError(
            f"Translation for chunk {chunk.index} contains a heavily mixed English line "
            f"(suspect_words={suspect_words}, mixed_lines={mixed_lines}, max_line={max_line_suspects})."
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
                cached = _strip_generated_english_chinese_glosses(chunk.markdown, cached, target_language)
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
            translated = _complete_translation_attempt(
                translator=translator,
                chunk=chunk,
                source_language=source_language,
                target_language=target_language,
                quality_retry=str(last_error) if isinstance(last_error, ValueError) else None,
            ).strip()
            if not translated:
                raise ValueError(f"Empty translation returned for chunk {chunk.index}.")
            translated = _strip_generated_english_chinese_glosses(chunk.markdown, translated, target_language)
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

        try:
            response = requests.post(
                self.endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "Connection": "close",
                },
                timeout=(10, self.http_timeout),
            )
            response.raise_for_status()
            response_data = response.json()
        except requests.HTTPError as exc:
            error_body = exc.response.text if exc.response is not None else ""
            status_code = exc.response.status_code if exc.response else "?"
            raise ValueError(
                f"MiniMax translation failed for chunk {chunk.index}: "
                f"HTTP {status_code}: {error_body[:500]}"
            ) from exc
        except requests.RequestException as exc:
            raise ValueError(f"MiniMax translation failed for chunk {chunk.index}: {exc}") from exc

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


def _complete_translation_attempt(
    *,
    translator: BaseTranslator,
    chunk: TranslationChunk,
    source_language: str | None,
    target_language: str,
    quality_retry: str | None = None,
) -> str:
    if quality_retry is None:
        return translator.translate_chunk(
            chunk=chunk,
            source_language=source_language,
            target_language=target_language,
        )

    prompt = _translation_prompt(
        chunk,
        source_language,
        target_language,
        quality_retry=quality_retry,
    )
    if isinstance(translator, MiniMaxAnthropicTranslator):
        payload = {
            "model": translator.model,
            "max_tokens": translator.max_tokens,
            "system": SYSTEM_PROMPT,
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
            )
            response.raise_for_status()
            response_data = response.json()
        except requests.HTTPError as exc:
            error_body = exc.response.text if exc.response is not None else ""
            status_code = exc.response.status_code if exc.response else "?"
            raise ValueError(
                f"MiniMax translation failed for chunk {chunk.index}: "
                f"HTTP {status_code}: {error_body[:500]}"
            ) from exc
        except requests.RequestException as exc:
            raise ValueError(f"MiniMax translation failed for chunk {chunk.index}: {exc}") from exc
        text_parts = [
            str(item.get("text") or "")
            for item in response_data.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        text = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
        if not text:
            raise ValueError(f"Empty MiniMax translation returned for chunk {chunk.index}.")
        if response_data.get("stop_reason") == "max_tokens":
            raise ValueError(
                f"MiniMax translation was truncated for chunk {chunk.index} "
                f"(stop_reason=max_tokens, max_tokens={translator.max_tokens}). "
                "Increase MINIMAX_MAX_TOKENS (or MINIMAX_HTTP_TIMEOUT_SECONDS if the request timed out early). "
                "Only reduce --max-chunk-chars if raising max_tokens is not enough."
            )
        return text

    if isinstance(translator, OpenAITranslator):
        response = translator.client.responses.create(
            model=translator.model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.output_text.strip()

    if isinstance(translator, OpenAICompatibleTranslator):
        response = translator.client.chat.completions.create(
            model=translator.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    retry_chunk = TranslationChunk(
        index=chunk.index,
        markdown=(
            "QUALITY RETRY: translate the SOURCE_MARKDOWN completely. "
            "Return only the translated Markdown.\n\n"
            "SOURCE_MARKDOWN:\n"
            f"{chunk.markdown}"
        ),
    )
    return translator.translate_chunk(
        chunk=retry_chunk,
        source_language=source_language,
        target_language=target_language,
    )


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
    retry_count: int = 6,
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


def _is_preserved_apparatus_block(block: str) -> bool:
    stripped = block.strip()
    if not stripped:
        return False
    first_line = stripped.splitlines()[0].strip().lower()
    if first_line in {"# list of illustrations", "# list of tables", "# list of figures"}:
        return True
    link_count = stripped.count("](")
    if link_count < 8:
        return False
    ascii_count = _ascii_letter_count(stripped)
    # Link-heavy figure/table lists are navigation apparatus, not reading prose.
    return link_count / max(len(stripped), 1) > 0.006 and ascii_count > 500


def _protect_media_blocks(markdown_text: str) -> tuple[str, dict[str, str]]:
    blocks = markdown_text.split("\n\n")
    replacements: dict[str, str] = {}
    protected_blocks: list[str] = []
    for block in blocks:
        if _is_preserved_media_block(block) or _is_preserved_apparatus_block(block):
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
        if _is_preserved_media_block(block) or _is_preserved_apparatus_block(block):
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
    retry_count: int = 6,
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
                    chapter_id=str(chapter.get("chapter_id") or "") or None,
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
        media_segments = [("media", chapter_source_markdown.strip())] if _is_preserved_apparatus_block(chapter_source_markdown) else _split_markdown_media_segments(chapter_source_markdown)
        for segment_kind, segment_markdown in media_segments:
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
                chapter_id=str(chapter.get("chapter_id") or "") or None,
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
