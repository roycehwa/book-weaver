from __future__ import annotations

import json
import hashlib
import os
import time
import copy
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from pathlib import Path
from typing import Protocol

from openai import OpenAI
import requests

from pdf_translator.chunking import split_markdown_into_chunks
from pdf_translator.glossary import glossary_terms_missing_in_translation, select_glossary_entries_for_text
from pdf_translator.glossary_convergence import sanitize_translation_output
from pdf_translator.config import (
    DEFAULT_MINIMAX_MAX_TOKENS,
    CompatibleAPISettings,
    DeepLSettings,
    OpenAISettings,
    RunSettings,
    _load_local_env,
)
from pdf_translator.models import BookTranslationResult, TranslatedChapter, TranslationChunk, TranslationResult


TRANSLATION_PROMPT_VERSION = "v5-delimited-glossary-controls"
FOOTNOTE_TRANSLATION_INSTRUCTION = (
    "Translate explanatory footnote prose into the target language. "
    "Preserve bibliographic titles, personal names, archival identifiers, and quoted source titles "
    "in their original language when translation would reduce citation accuracy."
)
SEMANTIC_TRANSLATION_POLICY = FOOTNOTE_TRANSLATION_INSTRUCTION
SEMANTIC_SPAN_BOUNDARY = "<!--__SEMANTIC_SPAN_BOUNDARY__-->"


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


def build_translation_prompt(
    markdown: str,
    *,
    source_language: str | None,
    target_language: str,
    chunk_index: int = 0,
    glossary_entries: list[dict] | None = None,
    prompt_instruction: str | None = None,
) -> str:
    source = source_language or "auto-detect"
    controls = (
        f"Source language: {source}\n"
        f"Target language: {target_language}\n"
        f"Markdown chunk index: {chunk_index}\n\n"
        f"{FOOTNOTE_TRANSLATION_INSTRUCTION}"
    )
    if glossary_entries:
        glossary_lines = "\n".join(
            f"- {entry['source']} => {entry.get('target') or ''}".rstrip()
            for entry in glossary_entries
        )
        controls += (
            "\n\nMANDATORY GLOSSARY (when a source term appears, use the exact Chinese wording):\n"
            f"{glossary_lines}"
        )
    if prompt_instruction:
        controls += f"\n\n{prompt_instruction}"
    return (
        f"{controls}\n\n"
        "Return only the translated contents of SOURCE_MARKDOWN. "
        "Do not repeat control instructions or glossary mappings.\n\n"
        f"<SOURCE_MARKDOWN>\n{markdown}\n</SOURCE_MARKDOWN>"
    )


def _translation_prompt(
    chunk: TranslationChunk,
    source_language: str | None,
    target_language: str,
    *,
    quality_retry: str | None = None,
) -> str:
    retry_note = ""
    if quality_retry:
        if "missing mandatory glossary terms" in quality_retry:
            retry_note = (
                f"\nGlossary retry: {quality_retry}\n"
                "Use the exact mandatory Chinese wording from the glossary for every matching source term.\n"
            )
        else:
            retry_note = (
                "\nQuality retry: the previous output failed validation because it was not fully translated. "
                "Translate every natural-language sentence completely into the target language now. "
                "Do not return the source text unchanged.\n"
            )
    prompt = build_translation_prompt(
        chunk.markdown,
        source_language=source_language,
        target_language=target_language,
        chunk_index=chunk.index,
        glossary_entries=chunk.glossary_entries,
        prompt_instruction=chunk.prompt_instruction,
    )
    if retry_note:
        marker = f"Markdown chunk index: {chunk.index}\n\n"
        if marker in prompt:
            return prompt.replace(marker, f"Markdown chunk index: {chunk.index}\n{retry_note}\n", 1)
    return prompt


class TranslationObserver(Protocol):
    def attempt_start(self, *, chunk_index: int, input_hash: str, attempt: int) -> None: ...

    def attempt_success(self, *, chunk_index: int, input_hash: str, cache_path: Path | None) -> None: ...

    def attempt_failure(
        self,
        *,
        chunk_index: int,
        input_hash: str,
        attempt: int,
        error_type: str,
        message: str,
        retryable: bool,
    ) -> None: ...

    def cache_hit(self, *, chunk_index: int, input_hash: str, cache_path: Path) -> None: ...

    def cache_invalidated(self, *, chunk_index: int, input_hash: str, cache_path: Path, reason: str) -> None: ...


def translate_semantic_footnote(
    note: dict,
    *,
    translator: "BaseTranslator",
    source_language: str | None,
    target_language: str,
) -> dict:
    translated = copy.deepcopy(note)
    for index, span in enumerate(translated.get("spans", [])):
        source_text = str(span.get("source_text") or "")
        if span.get("kind") != "prose":
            span["translated_text"] = source_text
            continue
        chunk = TranslationChunk(
            index=index,
            markdown=source_text,
            prompt_instruction=SEMANTIC_TRANSLATION_POLICY,
        )
        span["translated_text"] = translator.translate_chunk(
            chunk=chunk,
            source_language=source_language,
            target_language=target_language,
        ).strip()
    return translated


def _chunk_input_hash(chunk: TranslationChunk) -> str:
    glossary_part = ""
    if chunk.glossary_entries:
        glossary_part = json.dumps(chunk.glossary_entries, sort_keys=True, ensure_ascii=False)
    instruction_part = chunk.prompt_instruction or ""
    digest_input = f"{TRANSLATION_PROMPT_VERSION}\n{glossary_part}\n{instruction_part}\n{chunk.markdown}"
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]


def _chunk_source_fingerprint(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def _chunk_cache_path(cache_dir: Path, chunk: TranslationChunk) -> Path:
    return cache_dir / f"chunk-{chunk.index:06d}-{_chunk_input_hash(chunk)}.md"


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


def _is_newsletter_boilerplate_block(source: str) -> bool:
    lower = source.lower()
    return "newsletter sign-up" in lower or "newslettersignup" in lower or "authoralerts" in lower


def _looks_untranslated_for_target(source: str, translated: str, target_language: str) -> bool:
    if _is_newsletter_boilerplate_block(source):
        return False
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
    translated = sanitize_translation_output(translated)
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
    require_glossary: bool = True,
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
    if (
        require_glossary
        and chunk.glossary_entries
        and target_language.lower().startswith("zh")
    ):
        missing = glossary_terms_missing_in_translation(
            chunk.markdown,
            translated,
            chunk.glossary_entries,
        )
        if missing:
            terms = ", ".join(
                f"{item['source']} => {item['target']}" for item in missing[:6]
            )
            raise ValueError(
                f"Translation for chunk {chunk.index} missing mandatory glossary terms: {terms}"
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


def _is_glossary_quality_error(exc: Exception) -> bool:
    return isinstance(exc, ValueError) and "missing mandatory glossary terms" in str(exc)


def _is_untranslated_quality_error(exc: Exception) -> bool:
    if not isinstance(exc, ValueError):
        return False
    message = str(exc).lower()
    return "looks untranslated" in message or "looks incomplete" in message


def _should_try_fallback_translation(exc: Exception | None, *, had_sensitive_failure: bool) -> bool:
    if had_sensitive_failure:
        return True
    if exc is None:
        return False
    return _is_untranslated_quality_error(exc) or _is_glossary_quality_error(exc)


def _persist_chunk_translation(
    *,
    chunk: TranslationChunk,
    translated: str,
    cache_path: Path | None,
    observer: TranslationObserver | None,
    input_hash: str,
) -> str:
    if cache_path is not None:
        _write_chunk_cache(cache_path, chunk=chunk, translated=translated)
    if observer is not None:
        observer.attempt_success(
            chunk_index=chunk.index,
            input_hash=input_hash,
            cache_path=cache_path,
        )
    return translated


def _write_chunk_cache(
    cache_path: Path,
    *,
    chunk: TranslationChunk,
    translated: str,
) -> None:
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_path.write_text(translated + "\n", encoding="utf-8")
    tmp_path.replace(cache_path)
    cache_path.with_suffix(".source.json").write_text(
        json.dumps(
            {
                "schema": "translation_cache_source_v1",
                "source_fingerprint": _chunk_source_fingerprint(chunk.markdown),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _repair_glossary_in_chunk(
    *,
    chunk: TranslationChunk,
    translated: str,
    missing: list[dict[str, str]],
    source_language: str | None,
    target_language: str,
    translator: BaseTranslator,
) -> str:
    lines = "\n".join(f"- {item['source']} => {item['target']}" for item in missing)
    repair_chunk = TranslationChunk(
        index=chunk.index,
        markdown=chunk.markdown,
        glossary_entries=chunk.glossary_entries,
        prompt_instruction=(
            "Glossary repair pass. Revise the existing Chinese translation so every listed "
            "mandatory term appears with the exact Chinese wording when its English source "
            f"concept appears in the source:\n{lines}\n\n"
            f"CURRENT TRANSLATION TO REVISE:\n{translated}"
        ),
    )
    return translator.translate_chunk(
        chunk=repair_chunk,
        source_language=source_language,
        target_language=target_language,
    ).strip()


def _is_transient_translation_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if "token plan" in message or "(2062)" in message:
        return False
    if "timeout" in message or "connection" in message or "temporarily" in message:
        return True
    if "http 404" in message or "http 429" in message or "http 5" in message:
        return True
    if "page not found" in message:
        return True
    return False


def _is_permanent_translation_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "token plan" in message or "(2062)" in message


def _translate_chunk_resumable(
    *,
    chunk: TranslationChunk,
    source_language: str | None,
    target_language: str,
    translator: BaseTranslator,
    cache_dir: Path | None,
    retry_count: int = 3,
    allow_sensitive_split: bool = True,
    observer: TranslationObserver | None = None,
) -> str:
    input_hash = _chunk_input_hash(chunk)
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
                except ValueError as exc:
                    if observer is not None:
                        observer.cache_invalidated(
                            chunk_index=chunk.index,
                            input_hash=input_hash,
                            cache_path=cache_path,
                            reason=str(exc),
                        )
                    cache_path.unlink(missing_ok=True)
                else:
                    if observer is not None:
                        observer.cache_hit(chunk_index=chunk.index, input_hash=input_hash, cache_path=cache_path)
                    return cached
        else:
            legacy_paths = sorted(
                cache_dir.glob(f"chunk-{chunk.index:06d}-*.md"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for legacy_path in legacy_paths:
                source_path = legacy_path.with_suffix(".source.json")
                if not source_path.exists():
                    continue
                try:
                    source_metadata = json.loads(
                        source_path.read_text(encoding="utf-8")
                    )
                except json.JSONDecodeError:
                    continue
                if source_metadata.get(
                    "source_fingerprint"
                ) != _chunk_source_fingerprint(chunk.markdown):
                    continue
                legacy = sanitize_translation_output(
                    legacy_path.read_text(encoding="utf-8")
                )
                if not legacy:
                    continue
                try:
                    _assert_translation_quality(
                        chunk=chunk,
                        translated=legacy,
                        target_language=target_language,
                        translator_name=translator.name,
                    )
                    return _persist_chunk_translation(
                        chunk=chunk,
                        translated=legacy,
                        cache_path=cache_path,
                        observer=observer,
                        input_hash=input_hash,
                    )
                except ValueError as exc:
                    if not _is_glossary_quality_error(exc):
                        continue
                    missing = glossary_terms_missing_in_translation(
                        chunk.markdown,
                        legacy,
                        chunk.glossary_entries or [],
                    )
                    try:
                        repaired = sanitize_translation_output(
                            _repair_glossary_in_chunk(
                                chunk=chunk,
                                translated=legacy,
                                missing=missing,
                                source_language=source_language,
                                target_language=target_language,
                                translator=translator,
                            )
                        )
                        _assert_translation_quality(
                            chunk=chunk,
                            translated=repaired,
                            target_language=target_language,
                            translator_name=translator.name,
                        )
                    except Exception:
                        continue
                    return _persist_chunk_translation(
                        chunk=chunk,
                        translated=repaired,
                        cache_path=cache_path,
                        observer=observer,
                        input_hash=input_hash,
                    )

    last_error: Exception | None = None
    had_sensitive_failure = False
    last_glossary_candidate: str | None = None
    max_attempts = max(1, retry_count) + 4
    for attempt in range(max_attempts):
        attempt_no = attempt + 1
        try:
            if observer is not None:
                observer.attempt_start(chunk_index=chunk.index, input_hash=input_hash, attempt=attempt_no)
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
            return _persist_chunk_translation(
                chunk=chunk,
                translated=translated,
                cache_path=cache_path,
                observer=observer,
                input_hash=input_hash,
            )
        except Exception as exc:
            last_error = exc
            if _is_glossary_quality_error(exc):
                last_glossary_candidate = translated
            if "new_sensitive" in str(exc).lower():
                had_sensitive_failure = True
            permanent = _is_permanent_translation_error(exc)
            retryable = attempt_no < max_attempts and not permanent
            if allow_sensitive_split and "new_sensitive" in str(exc).lower():
                retryable = False
                try:
                    translated = _translate_sensitive_chunk_parts(
                        chunk=chunk,
                        source_language=source_language,
                        target_language=target_language,
                        translator=translator,
                    )
                    _assert_translation_quality(
                        chunk=chunk,
                        translated=translated,
                        target_language=target_language,
                        translator_name=translator.name,
                    )
                    return _persist_chunk_translation(
                        chunk=chunk,
                        translated=translated,
                        cache_path=cache_path,
                        observer=observer,
                        input_hash=input_hash,
                    )
                except Exception as split_exc:
                    last_error = split_exc
                    if "new_sensitive" in str(split_exc).lower():
                        had_sensitive_failure = True
                break
            if observer is not None:
                observer.attempt_failure(
                    chunk_index=chunk.index,
                    input_hash=input_hash,
                    attempt=attempt_no,
                    error_type=exc.__class__.__name__,
                    message=str(exc),
                    retryable=retryable,
                )
            if permanent:
                break
            if _is_transient_translation_error(exc):
                time.sleep(min(2 ** min(attempt, 5), 30))
                if attempt_no < max_attempts:
                    continue
            if not _is_transient_translation_error(exc) and attempt_no >= max(1, retry_count):
                break
            if attempt_no >= max_attempts:
                break
            time.sleep(min(2**attempt, 8))

    fallback_translated: str | None = None
    if _should_try_fallback_translation(last_error, had_sensitive_failure=had_sensitive_failure):
        fallback_translated = _try_fallback_translation(
            chunk=chunk,
            source_language=source_language,
            target_language=target_language,
            primary_translator=translator,
            cache_path=cache_path,
        )
    if fallback_translated is not None:
        if observer is not None:
            observer.attempt_success(chunk_index=chunk.index, input_hash=input_hash, cache_path=cache_path)
        return fallback_translated

    if last_error and _is_untranslated_quality_error(last_error):
        try:
            split_translated = _translate_sensitive_chunk_parts(
                chunk=chunk,
                source_language=source_language,
                target_language=target_language,
                translator=translator,
            )
            split_translated = _strip_generated_english_chinese_glosses(
                chunk.markdown,
                split_translated,
                target_language,
            )
            _assert_translation_quality(
                chunk=chunk,
                translated=split_translated,
                target_language=target_language,
                translator_name=translator.name,
                require_glossary=False,
            )
            return _persist_chunk_translation(
                chunk=chunk,
                translated=split_translated,
                cache_path=cache_path,
                observer=observer,
                input_hash=input_hash,
            )
        except Exception:
            pass

    if last_error and _is_glossary_quality_error(last_error) and last_glossary_candidate:
        missing = glossary_terms_missing_in_translation(
            chunk.markdown,
            last_glossary_candidate,
            chunk.glossary_entries or [],
        )
        if missing:
            try:
                repaired = _strip_generated_english_chinese_glosses(
                    chunk.markdown,
                    _repair_glossary_in_chunk(
                        chunk=chunk,
                        translated=last_glossary_candidate,
                        missing=missing,
                        source_language=source_language,
                        target_language=target_language,
                        translator=translator,
                    ),
                    target_language,
                )
                _assert_translation_quality(
                    chunk=chunk,
                    translated=repaired,
                    target_language=target_language,
                    translator_name=translator.name,
                    require_glossary=True,
                )
                return _persist_chunk_translation(
                    chunk=chunk,
                    translated=repaired,
                    cache_path=cache_path,
                    observer=observer,
                    input_hash=input_hash,
                )
            except Exception:
                pass
    raise ValueError(f"Translation failed for chunk {chunk.index} after {retry_count} attempts: {last_error}") from last_error


def _deepl_usage_state_path() -> Path:
    return Path.home() / ".hermes" / "state" / "deepl-usage.json"


def _deepl_monthly_char_budget() -> int:
    raw = os.getenv("DEEPL_MONTHLY_CHAR_BUDGET", "1800000").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 1_800_000


def _deepl_load_usage() -> dict[str, object]:
    path = _deepl_usage_state_path()
    if not path.exists():
        return {"month": "", "characters": 0, "chunks": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"month": "", "characters": 0, "chunks": 0}
    if not isinstance(payload, dict):
        return {"month": "", "characters": 0, "chunks": 0}
    return payload


def _deepl_current_month_key() -> str:
    return time.strftime("%Y-%m")


def _deepl_characters_used_this_month() -> int:
    usage = _deepl_load_usage()
    if str(usage.get("month") or "") != _deepl_current_month_key():
        return 0
    return int(usage.get("characters") or 0)


def _deepl_budget_allows(char_count: int) -> bool:
    budget = _deepl_monthly_char_budget()
    if budget <= 0:
        return False
    return _deepl_characters_used_this_month() + char_count <= budget


def _deepl_record_usage(char_count: int, *, chunk_index: int) -> None:
    path = _deepl_usage_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    month = _deepl_current_month_key()
    usage = _deepl_load_usage()
    if str(usage.get("month") or "") != month:
        usage = {"month": month, "characters": 0, "chunks": 0}
    usage["characters"] = int(usage.get("characters") or 0) + char_count
    usage["chunks"] = int(usage.get("chunks") or 0) + 1
    usage["last_chunk_index"] = chunk_index
    path.write_text(json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _deepl_language_code(language: str | None, *, role: str) -> str | None:
    if not language:
        return None
    normalized = language.strip().lower().replace("_", "-")
    if normalized in {"en", "en-us", "en-gb"}:
        return "EN"
    if normalized.startswith("zh"):
        if any(token in normalized for token in ("hant", "tw", "hk", "traditional")):
            return "ZH-HANT"
        return "ZH"
    if role == "target":
        raise ValueError(f"Unsupported DeepL target language: {language}")
    return None


def _resolve_fallback_translator(*, primary_name: str) -> BaseTranslator | None:
    _load_local_env()
    fallback_name = (os.getenv("TRANSLATION_FALLBACK") or "").strip().lower()
    if not fallback_name:
        if os.getenv("DEEPL_AUTH_KEY") or os.getenv("DEEPL_API_KEY"):
            fallback_name = "deepl"
        else:
            return None
    if fallback_name == primary_name.strip().lower():
        return None
    try:
        return build_translator(fallback_name)
    except ValueError:
        return None


def _try_fallback_translation(
    *,
    chunk: TranslationChunk,
    source_language: str | None,
    target_language: str,
    primary_translator: BaseTranslator,
    cache_path: Path | None,
) -> str | None:
    fallback = _resolve_fallback_translator(primary_name=primary_translator.name)
    if fallback is None:
        return None

    source_chars = len(chunk.markdown)
    if not _deepl_budget_allows(source_chars):
        return None

    last_error: Exception | None = None
    for attempt in (
        lambda: fallback.translate_chunk(
            chunk=chunk,
            source_language=source_language,
            target_language=target_language,
        ),
        lambda: _translate_sensitive_chunk_parts(
            chunk=chunk,
            source_language=source_language,
            target_language=target_language,
            translator=fallback,
        ),
    ):
        try:
            translated = attempt().strip()
            if not translated:
                raise ValueError(f"Empty fallback translation returned for chunk {chunk.index}.")
            translated = _strip_generated_english_chinese_glosses(chunk.markdown, translated, target_language)
            _assert_translation_quality(
                chunk=chunk,
                translated=translated,
                target_language=target_language,
                translator_name=fallback.name,
                require_glossary=True,
            )
            if cache_path is not None:
                _write_chunk_cache(cache_path, chunk=chunk, translated=translated)
            if fallback.name == "deepl":
                _deepl_record_usage(source_chars, chunk_index=chunk.index)
            return translated
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        return None
    return None


def _split_sensitive_source(source: str, *, max_part_chars: int) -> list[str]:
    paragraphs = [part.strip() for part in source.split("\n\n") if part.strip()]
    if not paragraphs:
        return [source]
    parts: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_part_chars:
            current = candidate
            continue
        if current:
            parts.append(current)
        if len(paragraph) <= max_part_chars:
            current = paragraph
            continue
        lines = paragraph.splitlines()
        block = ""
        for line in lines:
            line_candidate = f"{block}\n{line}".strip() if block else line
            if len(line_candidate) <= max_part_chars:
                block = line_candidate
                continue
            if block:
                parts.append(block)
            block = line
        current = block
    if current:
        parts.append(current)
    return parts


def _translate_sensitive_chunk_parts(
    *,
    chunk: TranslationChunk,
    source_language: str | None,
    target_language: str,
    translator: BaseTranslator,
) -> str:
    last_error: Exception | None = None
    for max_part_chars in (2800, 1400, 900, 500):
        try:
            translated_parts = [
                _translate_sensitive_part(
                    chunk=TranslationChunk(
                        index=chunk.index * 1000 + offset,
                        markdown=part,
                    ),
                    source_language=source_language,
                    target_language=target_language,
                    translator=translator,
                    retry_count=3,
                )
                for offset, part in enumerate(
                    _split_sensitive_source(chunk.markdown, max_part_chars=max_part_chars)
                )
            ]
            translated = "\n\n".join(part.strip() for part in translated_parts if part.strip())
            _assert_translation_quality(
                chunk=chunk,
                translated=translated,
                target_language=target_language,
                translator_name=translator.name,
            )
            return translated
        except ValueError as exc:
            last_error = exc
            message = str(exc).lower()
            if "new_sensitive" in message or "looks untranslated" in message or "looks incomplete" in message:
                continue
            raise
    assert last_error is not None
    raise last_error


def _translate_sensitive_part(
    *,
    chunk: TranslationChunk,
    source_language: str | None,
    target_language: str,
    translator: BaseTranslator,
    retry_count: int,
) -> str:
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
            return _strip_generated_english_chinese_glosses(
                chunk.markdown,
                translated,
                target_language,
            )
        except Exception as exc:
            last_error = exc
            if "new_sensitive" in str(exc).lower():
                break
            if attempt + 1 >= max(1, retry_count):
                break
            time.sleep(min(2**attempt, 8))
    raise ValueError(
        f"Sensitive split translation failed for chunk {chunk.index} "
        f"after {retry_count} attempts: {last_error}"
    ) from last_error


def _translate_chunks_ordered(
    *,
    chunks: list[TranslationChunk],
    source_language: str | None,
    target_language: str,
    translator: BaseTranslator,
    cache_dir: Path | None,
    retry_count: int,
    concurrency: int,
    observer: TranslationObserver | None = None,
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
                observer=observer,
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
                observer=observer,
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
            status_code = exc.response.status_code if exc.response is not None else "?"
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


class DeepLTranslator(BaseTranslator):
    name = "deepl"

    def __init__(self, settings: DeepLSettings) -> None:
        self.auth_key = settings.auth_key
        self.base_url = settings.base_url.rstrip("/")
        self.http_timeout = float(os.getenv("DEEPL_HTTP_TIMEOUT_SECONDS", "120"))

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        payload: dict[str, object] = {
            "text": [chunk.markdown],
            "target_lang": _deepl_language_code(target_language, role="target"),
            "preserve_formatting": True,
        }
        source_lang = _deepl_language_code(source_language, role="source")
        if source_lang:
            payload["source_lang"] = source_lang

        try:
            response = requests.post(
                f"{self.base_url}/v2/translate",
                json=payload,
                headers={
                    "Authorization": f"DeepL-Auth-Key {self.auth_key}",
                    "Content-Type": "application/json",
                },
                timeout=(10, self.http_timeout),
            )
            response.raise_for_status()
            response_data = response.json()
        except requests.HTTPError as exc:
            error_body = exc.response.text if exc.response is not None else ""
            status_code = exc.response.status_code if exc.response is not None else "?"
            raise ValueError(
                f"DeepL translation failed for chunk {chunk.index}: "
                f"HTTP {status_code}: {error_body[:500]}"
            ) from exc
        except requests.RequestException as exc:
            raise ValueError(f"DeepL translation failed for chunk {chunk.index}: {exc}") from exc

        translations = response_data.get("translations")
        if not isinstance(translations, list) or not translations:
            raise ValueError(f"Empty DeepL translation returned for chunk {chunk.index}.")
        first = translations[0]
        if not isinstance(first, dict):
            raise ValueError(f"Malformed DeepL translation returned for chunk {chunk.index}.")
        text = str(first.get("text") or "").strip()
        if not text:
            raise ValueError(f"Empty DeepL translation returned for chunk {chunk.index}.")
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
            status_code = exc.response.status_code if exc.response is not None else "?"
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
    if normalized == "deepl":
        return DeepLTranslator(DeepLSettings.from_env())
    raise ValueError(f"Unsupported translator backend: {name}")


def translate_markdown(
    *,
    chunks: list[TranslationChunk],
    settings: RunSettings,
    translator: BaseTranslator,
    cache_dir: Path | None = None,
    retry_count: int = 6,
    concurrency: int = 1,
    observer: TranslationObserver | None = None,
) -> TranslationResult:
    glossary_entries = settings.glossary_entries or []
    enriched_chunks = chunks
    if glossary_entries:
        enriched_chunks = [
            TranslationChunk(
                index=chunk.index,
                markdown=chunk.markdown,
                glossary_entries=select_glossary_entries_for_text(
                    chunk.markdown,
                    glossary_entries,
                    chapter_id=None,
                )
                or None,
                prompt_instruction=chunk.prompt_instruction,
            )
            for chunk in chunks
        ]
    _write_glossary_constraints(settings.output_dir, enriched_chunks, reset=True)
    translated_chunks = _translate_chunks_ordered(
        chunks=enriched_chunks,
        source_language=settings.source_language,
        target_language=settings.target_language,
        translator=translator,
        cache_dir=cache_dir,
        retry_count=retry_count,
        concurrency=concurrency,
        observer=observer,
    )

    return TranslationResult(
        translated_markdown="\n\n".join(translated_chunks).strip() + "\n",
        source_language=settings.source_language,
        target_language=settings.target_language,
        translator=translator.name,
        chunk_count=len(chunks),
    )


def _write_glossary_constraints(
    run_dir: Path,
    chunks: list[TranslationChunk],
    *,
    reset: bool,
) -> None:
    if not run_dir.exists():
        return
    path = run_dir / "jobs" / "glossary-constraints.json"
    existing: dict[str, object] = {}
    if not reset and path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    by_index = {
        int(item["chunk_index"]): item
        for item in existing.get("chunks", [])
        if isinstance(item, dict) and item.get("chunk_index") is not None
    }
    for chunk in chunks:
        terms = [
            {
                **dict(entry),
                "source": str(entry.get("source") or "").strip(),
                "target": str(entry.get("target") or "").strip(),
            }
            for entry in chunk.glossary_entries or []
            if str(entry.get("source") or "").strip()
            and str(entry.get("target") or "").strip()
        ]
        by_index[chunk.index] = {"chunk_index": chunk.index, "terms": terms}
    payload = {
        "schema": "translation_glossary_constraints_v1",
        "chunks": [by_index[index] for index in sorted(by_index)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
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
    if re.match(r"^-\s+\[\*\*\d+\.\*\*\]\([^)]+\)", first_line):
        return False
    return False


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


def estimate_semantic_translation_chunk_count(
    book: dict,
    max_chunk_chars: int,
) -> int:
    groups = 0
    current_length = 0
    semantic = book.get("semantic_content")
    if not isinstance(semantic, dict):
        return 0
    for note in semantic.get("footnotes", []):
        if not isinstance(note, dict):
            continue
        for span in note.get("spans", []):
            if not isinstance(span, dict) or span.get("kind") != "prose":
                continue
            source_length = len(str(span.get("source_text") or ""))
            added = source_length + (len(SEMANTIC_SPAN_BOUNDARY) if current_length else 0)
            if current_length and current_length + added > max_chunk_chars:
                groups += 1
                current_length = source_length
            else:
                current_length += added
    return groups + (1 if current_length else 0)


def translate_book_chapters(
    *,
    book: dict,
    settings: RunSettings,
    translator: BaseTranslator,
    cache_dir: Path | None = None,
    retry_count: int = 6,
    concurrency: int = 1,
    observer: TranslationObserver | None = None,
) -> BookTranslationResult:
    translated_chapters: list[TranslatedChapter] = []
    translated_markdown_parts: list[str] = []
    chunk_index = 0
    glossary_entries = settings.glossary_entries or []
    run_dir = cache_dir.parent if cache_dir is not None else settings.output_dir
    _write_glossary_constraints(run_dir, [], reset=True)

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
            chapter_id = str(chapter.get("chapter_id") or chapter.get("id") or "") or None
            global_chunks = []
            for offset, source_chunk in enumerate(source_chunks):
                selected = (
                    select_glossary_entries_for_text(
                        source_chunk.markdown,
                        glossary_entries,
                        chapter_id=chapter_id,
                    )
                    if glossary_entries
                    else []
                )
                global_chunks.append(
                    TranslationChunk(
                        index=chunk_index + offset,
                        markdown=source_chunk.markdown,
                        glossary_entries=selected or None,
                    )
                )
            _write_glossary_constraints(run_dir, global_chunks, reset=False)
            translated_parts.extend(
                _translate_chunks_ordered(
                    chunks=global_chunks,
                    source_language=settings.source_language,
                    target_language=settings.target_language,
                    translator=translator,
                    cache_dir=cache_dir,
                    retry_count=retry_count,
                    concurrency=concurrency,
                    observer=observer,
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

    semantic_content = copy.deepcopy(book.get("semantic_content"))
    if isinstance(semantic_content, dict):
        prose_spans: list[dict] = []
        semantic_chunks: list[TranslationChunk] = []
        for note in semantic_content.get("footnotes", []):
            if not isinstance(note, dict):
                continue
            for span in note.get("spans", []):
                if not isinstance(span, dict):
                    continue
                source_text = str(span.get("source_text") or "")
                if span.get("kind") != "prose":
                    span["translated_text"] = source_text
                    continue
                prose_spans.append(span)
        semantic_span_groups: list[list[dict]] = []
        for span in prose_spans:
            source_text = str(span.get("source_text") or "")
            if (
                semantic_span_groups
                and sum(
                    len(str(item.get("source_text") or ""))
                    for item in semantic_span_groups[-1]
                )
                + len(SEMANTIC_SPAN_BOUNDARY)
                + len(source_text)
                <= settings.max_chunk_chars
            ):
                semantic_span_groups[-1].append(span)
            else:
                semantic_span_groups.append([span])
        semantic_chunks = [
            TranslationChunk(
                index=chunk_index + index,
                markdown=(
                    f"\n\n{SEMANTIC_SPAN_BOUNDARY}\n\n".join(
                        str(span.get("source_text") or "") for span in group
                    )
                ),
                prompt_instruction=(
                    f"{SEMANTIC_TRANSLATION_POLICY}. Preserve every "
                    f"{SEMANTIC_SPAN_BOUNDARY} marker exactly."
                ),
            )
            for index, group in enumerate(semantic_span_groups)
        ]
        if semantic_chunks:
            translated_spans = _translate_chunks_ordered(
                chunks=semantic_chunks,
                source_language=settings.source_language,
                target_language=settings.target_language,
                translator=translator,
                cache_dir=cache_dir,
                retry_count=retry_count,
                concurrency=concurrency,
                observer=observer,
            )
            fallback_chunk_count = 0
            for group, translated_text in zip(
                semantic_span_groups,
                translated_spans,
            ):
                parts = [
                    part.strip()
                    for part in translated_text.split(SEMANTIC_SPAN_BOUNDARY)
                ]
                if len(parts) != len(group):
                    fallback_chunks = [
                        TranslationChunk(
                            index=(
                                chunk_index
                                + len(semantic_chunks)
                                + fallback_chunk_count
                                + index
                            ),
                            markdown=str(span.get("source_text") or ""),
                            prompt_instruction=SEMANTIC_TRANSLATION_POLICY,
                        )
                        for index, span in enumerate(group)
                    ]
                    parts = _translate_chunks_ordered(
                        chunks=fallback_chunks,
                        source_language=settings.source_language,
                        target_language=settings.target_language,
                        translator=translator,
                        cache_dir=cache_dir,
                        retry_count=retry_count,
                        concurrency=concurrency,
                        observer=observer,
                    )
                    fallback_chunk_count += len(fallback_chunks)
                for span, part in zip(group, parts):
                    span["translated_text"] = part
            chunk_index += len(semantic_chunks) + fallback_chunk_count

    return BookTranslationResult(
        translated_markdown="\n\n".join(translated_markdown_parts).strip() + "\n",
        translated_chapters=translated_chapters,
        source_language=settings.source_language,
        target_language=settings.target_language,
        translator=translator.name,
        chunk_count=chunk_index,
        semantic_content=semantic_content if isinstance(semantic_content, dict) else None,
    )
