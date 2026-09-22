from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import time
import copy
import uuid
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
import re
from pathlib import Path
from typing import Protocol

from openai import OpenAI
import requests

from pdf_translator.zh_markdown_cleanup import FOOTNOTE_REF_RE, HTML_TAG_RE

from pdf_translator.book_views import (
    ensure_chapter_top_heading,
    join_chapter_delivery_markdown,
    pop_leading_markdown_heading,
    rebuild_delivery_toc_chapters,
    resolve_translated_chapter_heading,
)
from pdf_translator.chunking import split_markdown_into_chunks, join_chunk_texts, markdown_block_structure, untranslated_prose_blocks, markdown_source_blocks
from pdf_translator.segment_conservation import (
    translatable_segment_ids,
    verify_segment_processing_order,
    write_segment_conservation_report,
)
from pdf_translator.chapter_kind import classify_chapter, should_translate_chapter
from pdf_translator.chapter_segments import chapter_segments_for_translation
from pdf_translator.glossary import (
    apply_glossary_source_substitutions,
    glossary_terms_missing_in_translation,
    select_glossary_entries_for_text,
)
from pdf_translator.provider_traffic import ProviderTrafficController
from pdf_translator.glossary_convergence import sanitize_translation_output
from pdf_translator.config import (
    DEFAULT_MINIMAX_HTTP_TIMEOUT_SECONDS,
    DEFAULT_MINIMAX_MAX_TOKENS,
    DEFAULT_TRANSLATION_RETRY_COUNT,
    CompatibleAPISettings,
    DeepLSettings,
    OpenAISettings,
    RunSettings,
    _load_local_env,
)
from pdf_translator.models import BookTranslationResult, TranslatedChapter, TranslationChunk, TranslationResult


TRANSLATION_PROMPT_VERSION = "v6-footnote-full-translation"
FOOTNOTE_TRANSLATION_INSTRUCTION = (
    "Translate explanatory footnote prose into the target language. "
    "Translate the entire footnote into the target language. "
    "Every sentence and clause of the explanatory prose must be rendered in the target language, "
    "including sentences that introduce a citation. "
    "Preserve bibliographic titles when translation would reduce citation accuracy. "
    "Inside the footnote, only the following tokens stay in the source language: "
    "(a) text wrapped in straight or curly quotation marks that is a verbatim quotation, "
    "(b) archival identifiers, manuscript shelfmarks, and URLs. "
    "Personal names, place names, and book titles mentioned in the explanatory prose must be "
    "transliterated or translated as natural in the target language. "
    "Do not leave entire footnotes in the source language just because they contain a citation."
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
- Preserve paragraph boundaries: do not merge separate paragraphs or split a paragraph into extra paragraphs.
- Return only translated Markdown, with no commentary.
- Never invent link destinations or add translation notes. Preserve existing link targets exactly.
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
        "Translate every natural-language sentence and clause, including quoted prose, "
        "into the target language. Preserve URLs, citation identifiers and code. "
        "A fragment may start or end mid-sentence: translate the supplied fragment "
        "without inventing missing text. Do not copy source prose into the translation."
    )
    if glossary_entries:
        hard_entries = [
            entry
            for entry in glossary_entries
            if str(entry.get("enforcement") or "").lower() == "hard"
            or (
                not entry.get("enforcement")
                and entry.get("updated_by") != "user"
            )
        ]
        preferred_entries = [entry for entry in glossary_entries if entry not in hard_entries]
        glossary_lines = "\n".join(
            f"- {entry['source']} => {entry.get('target') or ''}".rstrip()
            for entry in hard_entries
        )
        if glossary_lines:
            controls += (
                "\n\nMANDATORY GLOSSARY (when a source term appears, use the exact Chinese wording):\n"
                f"{glossary_lines}"
            )
        preferred_lines = "\n".join(
            f"- {entry['source']} => {entry.get('target') or ''}".rstrip()
            for entry in preferred_entries
        )
        if preferred_lines:
            controls += (
                "\n\nPREFERRED GLOSSARY (use when natural in context; grammatical variants are allowed):\n"
                f"{preferred_lines}"
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
    if chunk.preserve_block_structure:
        retry_note = (
            "\nPreserve the exact source block sequence: "
            + ", ".join(markdown_block_structure(chunk.markdown))
            + ". Keep every paragraph separate; do not split a source paragraph. "
            "Preserve Markdown heading levels, lists, tables and code blocks.\n"
        )
    if quality_retry:
        if "missing mandatory glossary terms" in quality_retry:
            missing_terms = re.findall(r"([^\s,]+)\s*=>\s*([^,)]+)", quality_retry)
            terms_list = "\n".join(
                f"- {src_term}：must contain the literal Chinese target term `{tgt_term}`"
                for src_term, tgt_term in missing_terms[:6]
            ) if missing_terms else ""
            retry_note += (
                f"\nGlossary retry: the previous translation was rejected because the literal Chinese target terms were missing.\n"
                f"{terms_list}\n"
                "Rewrite your translation so each listed source term is rendered with its exact Chinese target verbatim. Do not paraphrase, do not use a synonym, do not omit the term.\n"
            )
        elif "paragraph/block structure" in quality_retry:
            retry_note += "\nStructure retry: restore the exact source block sequence. Do not add or remove paragraphs or heading markers.\n"
        elif "translator meta response" in quality_retry or "invented link targets" in quality_retry:
            retry_note += "\nFidelity retry: return only translated source content. Do not add translator notes or invent Markdown links for footnote numbers. Preserve source link destinations exactly.\n"
        else:
            retry_note += (
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
    digest = _chunk_input_hash(chunk)
    preferred = cache_dir / f"chunk-{chunk.index:06d}-{digest}.md"
    if preferred.exists():
        return preferred
    # An earlier chapter edit can shift global indices. Reuse only an identical
    # prompt/source/glossary fingerprint, still subject to the normal quality gate.
    matches = sorted(cache_dir.glob(f"chunk-*-{digest}.md"))
    return matches[0] if matches else preferred


def _read_chunk_cache(cache_dir: Path, chunk: TranslationChunk) -> str:
    """Return cached translation for a chunk, falling back to index-only filenames."""
    cache_path = _chunk_cache_path(cache_dir, chunk)
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8").strip()
    matches = sorted(cache_dir.glob(f"chunk-{chunk.index:06d}-*.md"))
    if len(matches) == 1:
        source_path = matches[0].with_suffix(".source.json")
        if source_path.exists():
            try:
                source_metadata = json.loads(source_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return ""
            if source_metadata.get("source_fingerprint") != _chunk_source_fingerprint(chunk.markdown):
                return ""
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
    if chunk.preserve_block_structure and markdown_block_structure(chunk.markdown) != markdown_block_structure(translated):
        raise ValueError(f"Translation for chunk {chunk.index} changed paragraph/block structure.")
    if target_language.lower().startswith("zh") and untranslated_prose_blocks(chunk.markdown, translated):
        raise ValueError(f"Translation for chunk {chunk.index} contains copied untranslated prose paragraphs.")
    if _looks_like_translator_meta_response(translated) and not _looks_like_translator_meta_response(chunk.markdown):
        raise ValueError(f"Translation for chunk {chunk.index} contains translator meta response.")
    source_footnotes = sorted(FOOTNOTE_REF_RE.findall(chunk.markdown))
    translated_footnotes = sorted(FOOTNOTE_REF_RE.findall(translated))
    if source_footnotes != translated_footnotes:
        raise ValueError(
            f"Translation for chunk {chunk.index} changed protected footnote markers."
        )
    # Parse rendered links, including reference-style and raw HTML anchors. A
    # numeric footnote marker is not a filename and must not become one.
    import markdown as markdown_renderer
    from bs4 import BeautifulSoup
    def link_targets(text: str) -> set[str]:
        soup = BeautifulSoup(markdown_renderer.markdown(text), "html.parser")
        return {str(a["href"]) for a in soup.find_all("a", href=True)}
    source_targets = link_targets(chunk.markdown) | {
        url.rstrip(".,;:!?)]") for url in re.findall(r"https?://[^\s<>]+", chunk.markdown)
    }
    if link_targets(translated) - source_targets:
        raise ValueError(f"Translation for chunk {chunk.index} contains invented link targets.")
    def semantic_html(text: str) -> list[str]:
        return sorted(
            tag
            for tag in HTML_TAG_RE.findall(text)
            if re.match(r"</?\s*[A-Za-z][\w:.-]*(?:\s|/?>)", tag)
        )

    if semantic_html(chunk.markdown) != semantic_html(translated):
        raise ValueError(
            f"Translation for chunk {chunk.index} changed protected HTML tags."
        )
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


def _apply_deterministic_glossary_repairs(
    *,
    source_text: str,
    translated_text: str,
    glossary_entries: list[dict] | None,
) -> str:
    """Apply deterministic glossary repairs without another model call.

    Connector variants (与/和/及) and leftover English source terms are patched
    in place. Remaining drift is accepted later rather than blocking the job.
    """
    if not glossary_entries:
        return translated_text
    repaired = _swap_connector_variants(translated_text, glossary_entries)
    return apply_glossary_source_substitutions(
        source_text,
        repaired,
        glossary_entries,
    )


def _is_hard_glossary_entry(entry: dict) -> bool:
    enforcement = str(entry.get("enforcement") or "").strip().lower()
    if enforcement == "hard":
        return True
    if not enforcement and entry.get("updated_by") != "user":
        return True
    return False


def _swap_connector_variants(text: str, glossary_entries: list[dict]) -> str:
    """Replace ``与/和/及`` connector variants with the canonical target."""
    for entry in glossary_entries:
        if not _is_hard_glossary_entry(entry):
            continue
        target = str(entry.get("target") or "").strip()
        if not target:
            continue
        variants: set[str] = set()
        if "与" in target:
            variants.update({target.replace("与", "和"), target.replace("与", "及")})
        if "和" in target:
            variants.update({target.replace("和", "与"), target.replace("和", "及")})
        if "及" in target:
            variants.update({target.replace("及", "与"), target.replace("及", "和")})
        for variant in sorted(variants, key=len, reverse=True):
            if variant and variant in text:
                text = text.replace(variant, target)
    return text


def _is_untranslated_quality_error(exc: Exception) -> bool:
    if not isinstance(exc, ValueError):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in (
        "looks untranslated", "looks incomplete", "copied untranslated prose paragraphs",
        "translator meta response", "invented link targets",
    ))


def _looks_untranslated_split_part(source: str, translated: str, target_language: str) -> bool:
    if not target_language.lower().startswith("zh"):
        return False
    source_ascii = _ascii_letter_count(source)
    if source_ascii < 120:
        return False
    translated_cjk = _cjk_count(translated)
    translated_ascii = _ascii_letter_count(translated)
    if translated_cjk >= max(20, int(source_ascii * 0.08)):
        return False
    return translated_ascii >= 80 and translated_ascii > translated_cjk * 3


def _looks_like_translator_meta_response(translated: str) -> bool:
    lowered = translated.lower()
    if re.search(r"(?im)^\s*[*_\[]*\s*(?:translation notes?|translator(?:'s)? notes?)\s*:", translated):
        return True
    meta_markers = (
        "this is a translation job",
        "please provide the actual markdown content",
        "i need the actual content",
        "i will translate it from english",
        "following all the rules you've specified",
    )
    return sum(1 for marker in meta_markers if marker in lowered) >= 2


def _should_try_fallback_translation(exc: Exception | None, *, had_sensitive_failure: bool) -> bool:
    if had_sensitive_failure:
        return True
    if exc is None:
        return False
    return _is_untranslated_quality_error(exc) or _is_glossary_quality_error(exc)


def _translation_fail_open_enabled() -> bool:
    value = os.getenv("TRANSLATION_FAIL_OPEN", "0").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _failed_chunk_placeholder(
    *,
    chunk: TranslationChunk,
    target_language: str,
    error: Exception | None,
) -> str:
    detail = str(error) if error is not None else "unknown translation quality failure"
    if target_language.lower().startswith("zh"):
        warning = (
            f"> ⚠️ BookWeaver：本段自动翻译未通过质量校验，已保留原文供审阅修订。"
            f"原因：{detail}"
        )
    else:
        warning = (
            f"> ⚠️ BookWeaver: automatic translation for this chunk did not pass "
            f"quality checks; source text is preserved for review. Reason: {detail}"
        )
    return (
        "<!-- BOOKWEAVER_TRANSLATION_FAIL_OPEN "
        f"chunk={chunk.index} reason={json.dumps(detail, ensure_ascii=False)} -->\n\n"
        f"{warning}\n\n"
        f"{chunk.markdown}"
    )


def _persist_chunk_translation(
    *,
    chunk: TranslationChunk,
    translated: str,
    cache_path: Path | None,
    observer: TranslationObserver | None,
    input_hash: str,
    allow_glossary_drift: bool = False,
    allow_failed_placeholder: bool = False,
) -> str:
    if cache_path is not None:
        _write_chunk_cache(
            cache_path,
            chunk=chunk,
            translated=translated,
            allow_glossary_drift=allow_glossary_drift,
            allow_failed_placeholder=allow_failed_placeholder,
        )
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
    allow_glossary_drift: bool = False,
    allow_failed_placeholder: bool = False,
) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_name(f'.{cache_path.name}.{uuid.uuid4().hex}.tmp')
    tmp_path.write_text(translated + "\n", encoding="utf-8")
    tmp_path.replace(cache_path)
    from pdf_translator.source_workspace import atomic_json
    atomic_json(cache_path.with_suffix('.source.json'),
            {
                "schema": "translation_cache_source_v1",
                "source_fingerprint": _chunk_source_fingerprint(chunk.markdown),
                "allow_glossary_drift": allow_glossary_drift,
                "allow_failed_placeholder": allow_failed_placeholder,
            },
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
    source_paragraphs = chunk.markdown.split("\n\n")
    translated_paragraphs = translated.split("\n\n")
    if len(source_paragraphs) == len(translated_paragraphs) and len(source_paragraphs) > 1:
        repaired_paragraphs = list(translated_paragraphs)
        repaired_any = False
        active_missing = [
            {**item, "status": "active"}
            for item in missing
        ]
        for index, (source_paragraph, translated_paragraph) in enumerate(
            zip(source_paragraphs, translated_paragraphs)
        ):
            paragraph_missing = glossary_terms_missing_in_translation(
                source_paragraph,
                translated_paragraph,
                active_missing,
            )
            if not paragraph_missing:
                continue
            lines = "\n".join(
                f"- {item['source']} => {item['target']}"
                for item in paragraph_missing
            )
            repair_chunk = TranslationChunk(
                index=chunk.index,
                markdown=source_paragraph,
                glossary_entries=paragraph_missing,
                prompt_instruction=(
                    "Glossary repair pass. Revise only this existing Chinese paragraph so "
                    "every listed mandatory term uses the exact Chinese wording. Preserve "
                    "all other meaning and formatting:\n"
                    f"{lines}\n\nCURRENT TRANSLATION TO REVISE:\n"
                    f"{translated_paragraph}"
                ),
            )
            repaired_paragraphs[index] = translator.translate_chunk(
                chunk=repair_chunk,
                source_language=source_language,
                target_language=target_language,
            ).strip()
            repaired_any = True
        if repaired_any:
            return "\n\n".join(repaired_paragraphs)

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
    if "new_sensitive" in message or "content_filter" in message:
        return False
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
    return any(marker in message for marker in (
        "token plan", "(2062)", "http 401", "http 403", "http 404",
        "invalid api key", "unauthorized", "insufficient_quota",
    ))


def _require_translation_not_paused(cache_dir: Path | None) -> None:
    if cache_dir is not None and (cache_dir.parent / 'translation-pause.json').exists():
        raise RuntimeError('翻译已按用户要求暂停；已完成片段保留，可修改输入或手动继续。')


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
    _require_translation_not_paused(cache_dir)
    input_hash = _chunk_input_hash(chunk)
    cache_path: Path | None = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = _chunk_cache_path(cache_dir, chunk)
        if cache_path.exists():
            cached = cache_path.read_text(encoding="utf-8").strip()
            if cached:
                cached = _strip_generated_english_chinese_glosses(chunk.markdown, cached, target_language)
                cached = _apply_deterministic_glossary_repairs(
                    source_text=chunk.markdown,
                    translated_text=cached,
                    glossary_entries=chunk.glossary_entries,
                )
            source_metadata: dict[str, Any] = {}
            try:
                source_metadata_path = cache_path.with_suffix(".source.json")
                source_metadata = (
                    json.loads(source_metadata_path.read_text(encoding="utf-8"))
                    if source_metadata_path.exists()
                    else {}
                )
                _assert_translation_quality(
                    chunk=chunk,
                    translated=cached,
                    target_language=target_language,
                    translator_name=translator.name,
                    require_glossary=not bool(source_metadata.get("allow_glossary_drift")),
                )
            except ValueError as exc:
                if _translation_fail_open_enabled() and (source_metadata.get("allow_failed_placeholder") or "BOOKWEAVER_TRANSLATION_FAIL_OPEN" in cached):
                    if observer is not None:
                        observer.cache_hit(chunk_index=chunk.index, input_hash=input_hash, cache_path=cache_path)
                    return cached
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
    last_error: Exception | None = None
    had_sensitive_failure = False
    last_glossary_candidate: str | None = None
    max_attempts = max(1, retry_count) + 4
    for attempt in range(max_attempts):
        _require_translation_not_paused(cache_dir)
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
            translated = _restore_single_prose_boundary(chunk, translated)
            translated = _apply_deterministic_glossary_repairs(
                source_text=chunk.markdown,
                translated_text=translated,
                glossary_entries=chunk.glossary_entries,
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
        except Exception as exc:
            last_error = exc
            if _is_glossary_quality_error(exc):
                substituted = _apply_deterministic_glossary_repairs(
                    source_text=chunk.markdown,
                    translated_text=translated,
                    glossary_entries=chunk.glossary_entries,
                )
                return _persist_chunk_translation(
                    chunk=chunk,
                    translated=substituted,
                    cache_path=cache_path,
                    observer=observer,
                    input_hash=input_hash,
                    allow_glossary_drift=True,
                )
            if "new_sensitive" in str(exc).lower():
                had_sensitive_failure = True
            permanent = _is_permanent_translation_error(exc)
            attempt_limit = max_attempts if _is_transient_translation_error(exc) else max(1, retry_count)
            retryable = attempt_no < attempt_limit and not permanent
            if allow_sensitive_split and "new_sensitive" in str(exc).lower():
                retryable = False
                try:
                    translated = _translate_sensitive_chunk_parts(
                        chunk=chunk,
                        source_language=source_language,
                        target_language=target_language,
                        translator=translator,
                        cache_dir=cache_dir,
                    )
                    _assert_translation_quality(
                        chunk=chunk,
                        translated=translated,
                        target_language=target_language,
                        translator_name=translator.name,
                        require_glossary=False,
                    )
                    return _persist_chunk_translation(
                        chunk=chunk,
                        translated=translated,
                        cache_path=cache_path,
                        observer=observer,
                        input_hash=input_hash,
                        allow_glossary_drift=bool(glossary_terms_missing_in_translation(chunk.markdown, translated, chunk.glossary_entries or [])),
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

    _require_translation_not_paused(cache_dir)
    # A model may merge paragraphs repeatedly despite the correction prompt.
    # Retry isolated source blocks, never infer missing boundaries from output.
    if last_error and not had_sensitive_failure and ("changed paragraph/block structure" in str(last_error)
                       or _is_untranslated_quality_error(last_error)):
        blocks = markdown_source_blocks(chunk.markdown)
        if len(blocks) > 1:
            # Keep the whole context while giving the model explicit ownership
            # boundaries. Page-fragment prose often cannot be translated alone.
            try:
                result = _translate_tagged_blocks(chunk=chunk, blocks=blocks,
                    source_language=source_language, target_language=target_language,
                    translator=translator, cache_dir=cache_dir)
                return _persist_chunk_translation(chunk=chunk, translated=result,
                    cache_path=cache_path, observer=observer, input_hash=input_hash,
                    allow_glossary_drift=bool(glossary_terms_missing_in_translation(chunk.markdown, result, chunk.glossary_entries or [])))
            except ValueError as exc:
                if _is_permanent_translation_error(exc) or _is_transient_translation_error(exc):
                    raise
            recovered = []
            for offset, block in enumerate(blocks):
                _require_translation_not_paused(cache_dir)
                part = replace(chunk, index=chunk.index * 1000 + offset,
                               markdown=block,
                               prompt_instruction=(chunk.prompt_instruction or "") +
                               "\nThe following neighboring source is context ONLY; do not translate or include it in your output. "
                               "Use it to understand incomplete sentences caused by page boundaries.\n"
                               + json.dumps({"previous": blocks[offset - 1][-1200:] if offset else "",
                                             "next": blocks[offset + 1][:1200] if offset + 1 < len(blocks) else ""}, ensure_ascii=False)
                               + "\nTranslate only the requested Markdown chunk, preserving its boundary.")
                try:
                    result = _translate_sensitive_part(
                        chunk=part, source_language=source_language,
                        target_language=target_language, translator=translator,
                        retry_count=max(1, retry_count), cache_dir=cache_dir,
                    )
                except ValueError as exc:
                    if _is_permanent_translation_error(exc) or _is_transient_translation_error(exc):
                        raise
                    last_error = exc
                    break
                _assert_translation_quality(chunk=part, translated=result,
                    target_language=target_language, translator_name=translator.name, require_glossary=False)
                recovered.append(result)
            if len(recovered) == len(blocks):
                result = "\n\n".join(recovered)
                _assert_translation_quality(chunk=chunk, translated=result,
                    target_language=target_language, translator_name=translator.name, require_glossary=False)
                return _persist_chunk_translation(chunk=chunk, translated=result,
                    cache_path=cache_path, observer=observer, input_hash=input_hash,
                    allow_glossary_drift=bool(glossary_terms_missing_in_translation(chunk.markdown, result, chunk.glossary_entries or [])))

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

    split_error: Exception | None = None
    if last_error and _is_untranslated_quality_error(last_error):
        try:
            split_translated = _translate_sensitive_chunk_parts(
                chunk=chunk, source_language=source_language,
                target_language=target_language, translator=translator,
                cache_dir=cache_dir,
            )
            split_translated = _strip_generated_english_chinese_glosses(
                chunk.markdown, split_translated, target_language,
            )
            split_translated = _apply_deterministic_glossary_repairs(
                source_text=chunk.markdown, translated_text=split_translated,
                glossary_entries=chunk.glossary_entries,
            )
            _assert_translation_quality(
                chunk=chunk, translated=split_translated,
                target_language=target_language, translator_name=translator.name,
                require_glossary=True,
            )
            return _persist_chunk_translation(
                chunk=chunk, translated=split_translated, cache_path=cache_path,
                observer=observer, input_hash=input_hash,
            )
        except Exception as exc:
            split_error = exc

    if last_error and _is_untranslated_quality_error(last_error) and _translation_fail_open_enabled():
        placeholder = _failed_chunk_placeholder(
            chunk=chunk,
            target_language=target_language,
            error=last_error,
        )
        if cache_path is not None:
            _write_chunk_cache(
                cache_path,
                chunk=chunk,
                translated=placeholder,
                allow_glossary_drift=True,
                allow_failed_placeholder=True,
            )
        if observer is not None:
            observer.attempt_success(
                chunk_index=chunk.index,
                input_hash=input_hash,
                cache_path=cache_path,
            )
        return placeholder

    if last_error and _is_glossary_quality_error(last_error) and last_glossary_candidate:
        substituted = _apply_deterministic_glossary_repairs(
            source_text=chunk.markdown,
            translated_text=last_glossary_candidate,
            glossary_entries=chunk.glossary_entries,
        )
        return _persist_chunk_translation(
            chunk=chunk,
            translated=substituted,
            cache_path=cache_path,
            observer=observer,
            input_hash=input_hash,
            allow_glossary_drift=True,
        )
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
            cache_dir=cache_path.parent if cache_path is not None else None,
        ),
    ):
        _require_translation_not_paused(cache_path.parent if cache_path is not None else None)
        try:
            translated = attempt().strip()
            if not translated:
                raise ValueError(f"Empty fallback translation returned for chunk {chunk.index}.")
            translated = _strip_generated_english_chinese_glosses(chunk.markdown, translated, target_language)
            translated = _apply_deterministic_glossary_repairs(
                source_text=chunk.markdown,
                translated_text=translated,
                glossary_entries=chunk.glossary_entries,
            )
            missing = glossary_terms_missing_in_translation(
                chunk.markdown,
                translated,
                chunk.glossary_entries or [],
            )
            if missing:
                try:
                    translated = sanitize_translation_output(
                        _repair_glossary_in_chunk(
                            chunk=chunk,
                            translated=translated,
                            missing=missing,
                            source_language=source_language,
                            target_language=target_language,
                            translator=primary_translator,
                        )
                    )
                except Exception:
                    pass
            remaining = glossary_terms_missing_in_translation(
                chunk.markdown,
                translated,
                chunk.glossary_entries or [],
            )
            for item in remaining:
                try:
                    fallback_variant = fallback.translate_chunk(
                        chunk=TranslationChunk(
                            index=chunk.index,
                            markdown=item["source"],
                        ),
                        source_language=source_language,
                        target_language=target_language,
                    ).strip()
                except Exception:
                    continue
                if fallback_variant and fallback_variant in translated:
                    translated = translated.replace(
                        fallback_variant,
                        item["target"],
                    )
            allow_glossary_drift = False
            translated = _apply_deterministic_glossary_repairs(
                source_text=chunk.markdown,
                translated_text=translated,
                glossary_entries=chunk.glossary_entries,
            )
            try:
                _assert_translation_quality(
                    chunk=chunk,
                    translated=translated,
                    target_language=target_language,
                    translator_name=fallback.name,
                    require_glossary=True,
                )
            except ValueError as exc:
                if not _is_glossary_quality_error(exc):
                    raise
                _assert_translation_quality(
                    chunk=chunk,
                    translated=translated,
                    target_language=target_language,
                    translator_name=fallback.name,
                    require_glossary=False,
                )
                allow_glossary_drift = True
            if cache_path is not None:
                _write_chunk_cache(
                    cache_path,
                    chunk=chunk,
                    translated=translated,
                    allow_glossary_drift=allow_glossary_drift,
                )
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
            if len(line) > max_part_chars:
                if block:
                    parts.append(block)
                    block = ""
                sentences = re.split(r"(?<=[.!?。！？])\s+", line)
                sentence_block = ""
                for sentence in sentences:
                    if len(sentence) > max_part_chars:
                        if sentence_block:
                            parts.append(sentence_block)
                            sentence_block = ""
                        words = sentence.split()
                        word_block = ""
                        for word in words:
                            word_candidate = (
                                f"{word_block} {word}".strip()
                                if word_block
                                else word
                            )
                            if len(word_candidate) <= max_part_chars:
                                word_block = word_candidate
                            else:
                                if word_block:
                                    parts.append(word_block)
                                word_block = word
                        if word_block:
                            sentence_block = word_block
                        continue
                    sentence_candidate = (
                        f"{sentence_block} {sentence}".strip()
                        if sentence_block
                        else sentence
                    )
                    if len(sentence_candidate) <= max_part_chars:
                        sentence_block = sentence_candidate
                    else:
                        if sentence_block:
                            parts.append(sentence_block)
                        sentence_block = sentence
                block = sentence_block
                continue
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


def _translate_tagged_blocks(*, chunk: TranslationChunk, blocks: list[str],
                            source_language: str | None, target_language: str,
                            translator: BaseTranslator, cache_dir: Path | None) -> str:
    markers = [f"<!-- BW_BLOCK_{i}_{hashlib.sha256(block.encode()).hexdigest()[:10]} -->"
               for i, block in enumerate(blocks)]
    marked = "\n\n".join(f"{marker}\n{block}" for marker, block in zip(markers, blocks))
    request = replace(chunk, markdown=marked, preserve_block_structure=False,
        prompt_instruction=(chunk.prompt_instruction or "") +
        "\nContext-preserving recovery: translate ALL prose, including incomplete fragments. "
        "Keep each BW_BLOCK comment exactly once in the same order. "
        "Each comment owns the following source block: do not move content across comments. "
        "Use neighboring blocks to understand broken sentences but preserve every boundary. "
        "Do not add commentary or links.")
    _require_translation_not_paused(cache_dir)
    output = translator.translate_chunk(request, source_language, target_language).strip()
    found = re.findall(r"<!-- BW_BLOCK_\d+_[0-9a-f]{10} -->", output)
    if found != markers or output.split(markers[0], 1)[0].strip():
        raise ValueError("Context recovery changed block ownership markers.")
    parts = re.split(r"<!-- BW_BLOCK_\d+_[0-9a-f]{10} -->", output)[1:]
    translated = []
    for block, part in zip(blocks, parts):
        owned = replace(chunk, markdown=block)
        if not part.strip():
            raise ValueError("Context recovery omitted a source block.")
        part = _restore_single_prose_boundary(owned, part.strip())
        part = _apply_deterministic_glossary_repairs(source_text=block, translated_text=part,
                                                   glossary_entries=chunk.glossary_entries)
        _assert_translation_quality(chunk=owned, translated=part, target_language=target_language,
                                    translator_name=translator.name, require_glossary=False)
        translated.append(part)
    result = "\n\n".join(translated)
    _assert_translation_quality(chunk=chunk, translated=result, target_language=target_language,
                                translator_name=translator.name, require_glossary=False)
    return result


def _translate_sensitive_chunk_parts(
    *,
    chunk: TranslationChunk,
    source_language: str | None,
    target_language: str,
    translator: BaseTranslator,
    cache_dir: Path | None = None,
) -> str:
    last_error: Exception | None = None
    split_sizes = (2800, 1400, 900, 500, 240)
    for max_part_chars in split_sizes:
        try:
            translated_parts: list[str] = []
            source_chunks = split_markdown_into_chunks(chunk.markdown, max_part_chars)
            if max_part_chars == split_sizes[-1]:
                source_chunks = [part for paragraph in chunk.markdown.split("\n\n")
                                 if paragraph.strip()
                                 for part in split_markdown_into_chunks(paragraph, max_part_chars)]
            source_parts = [part.markdown for part in source_chunks]
            for offset, part in enumerate(source_parts):
                try:
                    translated_part = _translate_sensitive_part(
                        chunk=TranslationChunk(
                            index=chunk.index * 1000 + offset,
                            markdown=part,
                            preserve_block_structure=chunk.preserve_block_structure,
                        ),
                        source_language=source_language,
                        target_language=target_language,
                        translator=translator,
                        retry_count=3,
                        cache_dir=cache_dir,
                        allow_fragment_recovery=False,
                    )
                except ValueError:
                    # Original-text preservation requires a recorded human decision.
                    raise
                translated_parts.append(translated_part)
            translated = join_chunk_texts(translated_parts, [part.separator_before for part in source_chunks])
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
    cache_dir: Path | None = None,
    allow_fragment_recovery: bool = True,
) -> str:
    chunk = replace(chunk, prompt_instruction=(chunk.prompt_instruction or '') +
        '\nTranslate all natural-language prose in this source fragment, including quoted prose. '
        'A fragment may start or end mid-sentence. Translate only the supplied text; '
        'do not invent missing context and do not return the English fragment unchanged. '
        'Keep genuine bibliographic identifiers and URLs intact.')
    recovery_path = cache_dir / 'recovery' / f'{_chunk_input_hash(chunk)}.md' if cache_dir is not None else None
    if recovery_path is not None and recovery_path.exists():
        cached = recovery_path.read_text(encoding='utf-8')
        try:
            _assert_translation_quality(chunk=chunk, translated=cached, target_language=target_language,
                                        translator_name=translator.name, require_glossary=False)
            return cached
        except ValueError:
            pass
    last_error: Exception | None = None
    for attempt in range(max(1, retry_count)):
        _require_translation_not_paused(cache_dir)
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
            translated = _strip_generated_english_chinese_glosses(
                chunk.markdown,
                translated,
                target_language,
            )
            translated = _restore_single_prose_boundary(chunk, translated)
            translated = _apply_deterministic_glossary_repairs(
                source_text=chunk.markdown, translated_text=translated,
                glossary_entries=chunk.glossary_entries,
            )
            if _looks_untranslated_split_part(chunk.markdown, translated, target_language):
                raise ValueError(
                    f"Split translation part {chunk.index} looks untranslated "
                    f"(ascii={_ascii_letter_count(translated)}, cjk={_cjk_count(translated)})."
                )
            if chunk.preserve_block_structure and markdown_block_structure(chunk.markdown) != markdown_block_structure(translated):
                raise ValueError(f"Split translation part {chunk.index} changed paragraph/block structure.")
            _assert_translation_quality(
                chunk=chunk, translated=translated,
                target_language=target_language, translator_name=translator.name, require_glossary=False,
            )
            if recovery_path is not None:
                _write_chunk_cache(recovery_path, chunk=chunk, translated=translated, allow_glossary_drift=True)
            return translated
        except Exception as exc:
            last_error = exc
            if _is_permanent_translation_error(exc) or _is_transient_translation_error(exc):
                _require_translation_not_paused(cache_dir)
                raise
            if "new_sensitive" in str(exc).lower():
                break
            if attempt + 1 >= max(1, retry_count):
                break
            time.sleep(min(2**attempt, 8))
    if (allow_fragment_recovery and last_error and _is_untranslated_quality_error(last_error)
            and markdown_block_structure(chunk.markdown) == ('p',) and len(chunk.markdown) > 500):
        fragments = _split_sensitive_source(chunk.markdown, max_part_chars=450)
        if 1 < len(fragments) <= 12:
            outputs = []
            for index, fragment in enumerate(fragments):
                outputs.append(_translate_sensitive_part(
                    chunk=replace(chunk, index=chunk.index * 100 + index, markdown=fragment),
                    source_language=source_language, target_language=target_language,
                    translator=translator, retry_count=2, cache_dir=cache_dir,
                    allow_fragment_recovery=False))
            translated = ' '.join(outputs)
            _assert_translation_quality(chunk=chunk, translated=translated, target_language=target_language,
                                        translator_name=translator.name, require_glossary=False)
            if recovery_path is not None:
                _write_chunk_cache(recovery_path, chunk=chunk, translated=translated, allow_glossary_drift=True)
            return translated
    raise ValueError(
        f"Sensitive split translation failed for chunk {chunk.index} "
        f"after {retry_count} attempts: {last_error}"
    ) from last_error


def _restore_single_prose_boundary(chunk: TranslationChunk, translated: str) -> str:
    """A one-paragraph request owns one output paragraph, independent of model wrapping.

    Only remove model-added paragraph breaks in plain prose. Never flatten
    headings, lists, tables, code or multiple source paragraphs.
    """
    if not chunk.preserve_block_structure or markdown_block_structure(chunk.markdown) != ("p",):
        return translated
    structure = markdown_block_structure(translated)
    if len(structure) > 1 and all(kind == "p" for kind in structure):
        return re.sub(r"\n\s*\n+", " ", translated.strip())
    return translated


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


def _minimax_subprocess_enabled() -> bool:
    value = os.getenv("MINIMAX_USE_SUBPROCESS_TIMEOUT", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _post_minimax_json(
    endpoint: str,
    *,
    payload: dict,
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict:
    if not _minimax_subprocess_enabled():
        response = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=(10, timeout_seconds),
        )
        data: dict | None = None
        if response.status_code < 400:
            data = response.json()
        return {
            "status_code": response.status_code,
            "headers": dict(getattr(response, "headers", {}) or {}),
            "text": str(getattr(response, "text", "") or ""),
            "json": data,
        }

    request = {
        "endpoint": endpoint,
        "payload": payload,
        "headers": headers,
        "timeout_seconds": timeout_seconds,
    }
    script = r"""
import json
import sys
import requests

request = json.loads(sys.stdin.read())
try:
    response = requests.post(
        request["endpoint"],
        json=request["payload"],
        headers=request["headers"],
        timeout=(10, float(request["timeout_seconds"])),
    )
    body = response.text
    data = None
    if response.status_code < 400:
        data = response.json()
    print(json.dumps({
        "ok": True,
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "text": body,
        "json": data,
    }, ensure_ascii=False))
except requests.RequestException as exc:
    print(json.dumps({
        "ok": False,
        "error_type": exc.__class__.__name__,
        "message": str(exc),
    }, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({
        "ok": False,
        "error_type": exc.__class__.__name__,
        "message": str(exc),
    }, ensure_ascii=False))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            input=json.dumps(request, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=max(1.0, float(timeout_seconds)) + 5.0,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise requests.Timeout(
            f"MiniMax request exceeded wall timeout {timeout_seconds:.0f}s"
        ) from exc
    if result.returncode != 0:
        raise requests.ConnectionError(
            f"MiniMax request subprocess failed: {result.stderr[:500]}"
        )
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise requests.ConnectionError(
            f"MiniMax request subprocess returned invalid JSON: {result.stdout[:500]}"
        ) from exc
    if not response.get("ok"):
        raise requests.RequestException(
            f"{response.get('error_type')}: {response.get('message')}"
        )
    return response


class MiniMaxAnthropicTranslator(BaseTranslator):
    name = "minimax"

    def __init__(self, settings: CompatibleAPISettings) -> None:
        self.api_key = settings.api_key
        self.endpoint = settings.base_url
        self.model = settings.model
        self.max_tokens = settings.max_tokens or DEFAULT_MINIMAX_MAX_TOKENS
        # Keep single-request stalls bounded; override with MINIMAX_HTTP_TIMEOUT_SECONDS for slower accounts.
        self.http_timeout = float(
            os.getenv("MINIMAX_HTTP_TIMEOUT_SECONDS", str(DEFAULT_MINIMAX_HTTP_TIMEOUT_SECONDS))
        )
        self.traffic = ProviderTrafficController(
            max_concurrency=settings.max_concurrency,
            rpm=settings.rpm,
            tpm=settings.tpm,
        )

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        return self.translate_prompt(
            _translation_prompt(chunk, source_language, target_language),
            chunk_index=chunk.index,
        )

    def translate_prompt(self, prompt: str, *, chunk_index: int) -> str:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        }

        response_data: dict = {}
        last_error: Exception | None = None
        for provider_attempt in range(4):
            try:
                estimated_tokens = max(1, len(prompt) // 3) + self.max_tokens
                self.traffic.wait_for_request(estimated_tokens=estimated_tokens)
                response = _post_minimax_json(
                    self.endpoint,
                    payload=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "anthropic-version": "2023-06-01",
                        "Connection": "close",
                    },
                    timeout_seconds=self.http_timeout,
                )
                status_code = int(response.get("status_code") or 0)
                if status_code >= 400:
                    error_body = str(response.get("text") or "")
                    if status_code not in {429, 529}:
                        self.traffic.record_failure()
                        raise ValueError(
                            f"MiniMax translation failed for chunk {chunk_index}: "
                            f"HTTP {status_code}: {error_body[:500]}"
                        )
                    retry_after = str((response.get("headers") or {}).get("Retry-After") or "")
                    self.traffic.record_overload(
                        retry_after=float(retry_after) if retry_after else 2 ** provider_attempt
                    )
                    continue
                response_data = response.get("json") or {}
                self.traffic.record_success()
                break
            except requests.RequestException as exc:
                last_error = exc
                self.traffic.record_overload(retry_after=2 ** provider_attempt)
        else:
            raise ValueError(
                f"MiniMax translation failed for chunk {chunk_index} after provider retries: {last_error}"
            ) from last_error

        text_parts: list[str] = []
        for item in response_data.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                text_parts.append(item)

        text = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
        if not text:
            raise ValueError(f"Empty MiniMax translation returned for chunk {chunk_index}.")
        if response_data.get("stop_reason") == "max_tokens":
            raise ValueError(
                f"MiniMax translation was truncated for chunk {chunk_index} "
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
        return translator.translate_prompt(prompt, chunk_index=chunk.index)

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
    retry_count: int = DEFAULT_TRANSLATION_RETRY_COUNT,
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
                separator_before=chunk.separator_before,
                preserve_block_structure=chunk.preserve_block_structure,
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
        translated_markdown=join_chunk_texts(translated_chunks, [chunk.separator_before for chunk in chunks]).strip() + "\n",
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


ORIGINAL_PAGE_FALLBACK_BLOCK_RE = re.compile(r"^!\[Original page \d+\]\([^)]+\)$")


def _is_original_page_fallback_block(block: str) -> bool:
    """Page-render fallback images are not figure/table media blocks."""

    return bool(ORIGINAL_PAGE_FALLBACK_BLOCK_RE.match(block.strip()))


def _chapter_markdown_for_translation(chapter: dict) -> str:
    title = str(chapter.get("title") or f"Chapter {chapter.get('index', '')}").strip()
    markdown = str(chapter.get("markdown") or "").strip()
    markdown = re.sub(
        r"(!\[[^\]]*\]\()([^)]+)(\))",
        lambda match: (
            f"{match.group(1)}book-images/"
            f"{match.group(2).split('/book-images/', 1)[1]}{match.group(3)}"
            if "/book-images/" in match.group(2)
            else match.group(0)
        ),
        markdown,
    )
    if not markdown and (chapter.get("preserve_original") or chapter.get("resource_only")):
        return ""
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
    if _is_original_page_fallback_block(block):
        return False
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


def estimate_chapter_segment_translation_chunk_count(
    book: dict,
    max_chunk_chars: int,
) -> int:
    return sum(
        1
        for segment in chapter_segments_for_translation(book, max_chars=max_chunk_chars)
        if bool(segment.get("translate", True))
    )


def _chapter_title_key(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "").casefold())


def _segment_is_chapter_title_heading(segment: dict, source_title: str) -> bool:
    if segment.get("is_chapter_title") is True:
        return True
    if str(segment.get("role") or "") != "heading":
        return False
    heading, _ = pop_leading_markdown_heading(str(segment.get("markdown") or ""))
    if not heading:
        return False
    heading_key = _chapter_title_key(heading)
    source_key = _chapter_title_key(source_title)
    if not heading_key or not source_key:
        return False
    return (
        heading_key == source_key
        or (min(len(heading_key), len(source_key)) >= 6 and heading_key in source_key)
        or (min(len(heading_key), len(source_key)) >= 6 and source_key in heading_key)
    )


def translate_book_chapters(
    *,
    book: dict,
    settings: RunSettings,
    translator: BaseTranslator,
    cache_dir: Path | None = None,
    retry_count: int = DEFAULT_TRANSLATION_RETRY_COUNT,
    concurrency: int = 1,
    observer: TranslationObserver | None = None,
) -> BookTranslationResult:
    translated_chapters: list[TranslatedChapter] = []
    translated_markdown_parts: list[str] = []
    chunk_index = 0
    glossary_entries = settings.glossary_entries or []
    run_dir = cache_dir.parent if cache_dir is not None else settings.output_dir
    _write_glossary_constraints(run_dir, [], reset=True)
    pages = book.get("pages") if isinstance(book, dict) else []
    pages = pages if isinstance(pages, list) else []
    segment_plan = chapter_segments_for_translation(book, max_chars=settings.max_chunk_chars)
    from pdf_translator.translation_failures import read_failures, put_failure, clear_failure
    pending_failures: list[str] = []
    active_failure_keys: set[str] = set()
    segments_by_chapter: dict[str, list[dict]] = {}
    processed_segment_ids: list[str] = []
    for segment in segment_plan:
        segments_by_chapter.setdefault(str(segment.get("chapter_id") or ""), []).append(segment)

    for fallback_index, chapter in enumerate(book.get("chapters", []), 1):
        if isinstance(chapter, dict) and not chapter.get("kind"):
            chapter["kind"] = classify_chapter(chapter, pages=pages)
        chapter_source_markdown = _chapter_markdown_for_translation(chapter)
        chapter_id = str(chapter.get("chapter_id") or chapter.get("id") or f"chapter-{fallback_index:03d}")
        source_title = str(chapter.get("title") or f"Chapter {fallback_index}")
        chapter_kind = str(chapter.get("kind") or classify_chapter(chapter, pages=pages))
        if not should_translate_chapter(chapter):
            translated_markdown = (
                "" if chapter.get("rebuild_toc") else chapter_source_markdown.strip()
            )
            if translated_markdown:
                translated_markdown += "\n"
                translated_markdown_parts.append(translated_markdown.strip())
            sip = chapter.get("source_internal_path")
            translated_chapters.append(
                TranslatedChapter(
                    index=int(chapter.get("index", len(translated_chapters) + 1)),
                    chapter_id=chapter_id,
                    title=source_title,
                    page_start=chapter.get("page_start"),
                    page_end=chapter.get("page_end"),
                    source_pages=[int(page_no) for page_no in chapter.get("source_pages", [])],
                    markdown=translated_markdown,
                    source_internal_path=sip if isinstance(sip, str) else None,
                    toc=bool(chapter.get("toc", True)),
                    source_title=source_title,
                    kind=chapter_kind,
                    rebuild_toc=bool(chapter.get("rebuild_toc")),
                )
            )
            continue

        translated_parts: list[str] = []
        chapter_jobs: list[tuple[int, TranslationChunk, dict, str]] = []
        planned_segments = segments_by_chapter.get(chapter_id or "")
        nonempty_planned_segments = [
            segment
            for segment in planned_segments or []
            if str(segment.get("markdown") or "").strip()
        ]
        for planned_segment in nonempty_planned_segments:
            segment_markdown = str(planned_segment.get("markdown") or "").strip()
            if not segment_markdown:
                continue
            segment_id = str(planned_segment.get("segment_id") or "").strip()
            if segment_id and bool(planned_segment.get("translate", True)):
                role = str(planned_segment.get("role") or "prose")
                if role not in {"figure", "table"}:
                    processed_segment_ids.append(segment_id)
            if not bool(planned_segment.get("translate", True)):
                translated_parts.append(segment_markdown)
                continue

            selected = (
                select_glossary_entries_for_text(
                    segment_markdown,
                    glossary_entries,
                    chapter_id=chapter_id,
                )
                if glossary_entries
                else []
            )
            global_chunks = [
                TranslationChunk(
                    index=chunk_index,
                    markdown=segment_markdown,
                    glossary_entries=selected or None,
                    preserve_block_structure=bool(planned_segment.get("preserve_block_structure", False)),
                )
            ]
            _write_glossary_constraints(run_dir, global_chunks, reset=False)
            failure_key = segment_id or str(chunk_index)
            active_failure_keys.add(failure_key)
            input_hash = _chunk_input_hash(global_chunks[0])
            previous_failure = read_failures(run_dir)["items"].get(failure_key, {})
            resolution = previous_failure.get("resolution", {})
            if previous_failure.get("input_hash") == input_hash and resolution.get("kind") in {"manual_translation", "preserve_source", "defer_to_review"}:
                translated_parts.append(resolution["text"])
                if observer is not None and hasattr(observer, 'human_resolution'):
                    observer.human_resolution(chunk_index=chunk_index, kind=resolution['kind'])
                chunk_index += len(global_chunks)
                continue
            chapter_jobs.append((len(translated_parts), global_chunks[0], planned_segment, failure_key))
            translated_parts.append('')
            chunk_index += len(global_chunks)

        provider_stopped = threading.Event()
        consecutive_network_failures = 0
        def translate_one(chunk: TranslationChunk) -> str:
            if provider_stopped.is_set():
                raise RuntimeError('Provider unavailable; queued requests stopped.')
            return _translate_chunks_ordered(
                    chunks=[chunk],
                    source_language=settings.source_language,
                    target_language=settings.target_language,
                    translator=translator,
                    cache_dir=cache_dir,
                    retry_count=retry_count,
                    concurrency=1,
                    observer=observer,
                )[0]

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
            futures = {}
            waiting_jobs = iter(chapter_jobs)
            def submit_next() -> None:
                job = next(waiting_jobs, None)
                if job is not None:
                    futures[executor.submit(translate_one, job[1])] = job
            for _ in range(max(1, concurrency)):
                submit_next()
            try:
                while futures:
                    future = next(as_completed(tuple(futures)))
                    position, chunk, segment, key = futures.pop(future)
                    try:
                        output = future.result()
                        if 'BOOKWEAVER_TRANSLATION_FAIL_OPEN' in output:
                            raise ValueError('Translation placeholder requires explicit human intervention.')
                        translated_parts[position] = output
                        clear_failure(run_dir, key)
                        consecutive_network_failures = 0
                    except Exception as exc:
                        _require_translation_not_paused(cache_dir)
                        if _is_permanent_translation_error(exc):
                            raise
                        if observer is not None:
                            observer.attempt_failure(chunk_index=chunk.index, input_hash=_chunk_input_hash(chunk),
                                attempt=max(1, retry_count), error_type=type(exc).__name__, message=str(exc), retryable=False)
                        from pdf_translator.translation_failures import is_provider_content_refusal
                        put_failure(run_dir, key, {
                            'segment_id': key, 'chunk_index': chunk.index,
                            'chapter_id': chapter_id, 'chapter_title': chapter.get('title'),
                            'source_pages': segment.get('source_pages', []),
                            'source': chunk.markdown, 'input_hash': _chunk_input_hash(chunk),
                            'error': str(exc), 'status': 'failed',
                            'failure_kind': (
                                'provider_content_refusal' if is_provider_content_refusal(str(exc))
                                else 'translation_failure'
                            ),
                        })
                        pending_failures.append(key)
                        if _is_transient_translation_error(exc):
                            consecutive_network_failures += 1
                            if consecutive_network_failures >= max(3, concurrency):
                                provider_stopped.set()
                                from pdf_translator.translation_failures import TranslationProviderUnavailable
                                raise TranslationProviderUnavailable('模型连接连续失败，已停止后续请求；成功缓存已保留。') from exc
                    submit_next()
            except BaseException:
                provider_stopped.set()
                for future in futures:
                    future.cancel()
                raise

        display_title = source_title
        translated_heading_evidence = False
        for position, planned_segment in enumerate(nonempty_planned_segments):
            if not _segment_is_chapter_title_heading(planned_segment, source_title):
                continue
            translated_title, remainder = pop_leading_markdown_heading(
                translated_parts[position]
            )
            if translated_title:
                display_title = translated_title
                translated_parts[position] = remainder
                translated_heading_evidence = True
            break

        translated_markdown = join_chunk_texts(
            translated_parts,
            [str(part.get("separator_before", "\n\n")) for part in nonempty_planned_segments],
        ).strip()
        if translated_heading_evidence:
            display_title, translated_markdown = resolve_translated_chapter_heading(
                translated_markdown,
                display_title,
                translated_heading_evidence=False,
            )
        if translated_markdown:
            translated_markdown += "\n"
            translated_markdown_parts.append(translated_markdown.strip())

        sip = chapter.get("source_internal_path")
        translated_chapters.append(
            TranslatedChapter(
                index=int(chapter.get("index", len(translated_chapters) + 1)),
                chapter_id=chapter_id,
                title=display_title,
                page_start=chapter.get("page_start"),
                page_end=chapter.get("page_end"),
                source_pages=[int(page_no) for page_no in chapter.get("source_pages", [])],
                markdown=translated_markdown,
                source_internal_path=sip if isinstance(sip, str) else None,
                toc=bool(chapter.get("toc", True)),
                source_title=source_title,
                kind=chapter_kind,
                rebuild_toc=bool(chapter.get("rebuild_toc")),
            )
        )

    def semantic_translate(**kwargs) -> list[str]:
        """Keep batching, but isolate failed notes and retain human resolutions."""
        results = []
        for chunk in kwargs["chunks"]:
            sources = chunk.markdown.split(SEMANTIC_SPAN_BOUNDARY)
            parts = [replace(chunk, index=chunk.index * 10000 + i,
                             markdown=source.strip(), prompt_instruction=SEMANTIC_TRANSLATION_POLICY)
                     for i, source in enumerate(sources)]
            keys = ["footnote:" + _chunk_input_hash(part) for part in parts]
            active_failure_keys.update(keys)
            ledger = read_failures(run_dir)["items"]
            if not any(key in ledger for key in keys):
                try:
                    output = _translate_chunks_ordered(**dict(kwargs, chunks=[chunk]))[0]
                    if "BOOKWEAVER_TRANSLATION_FAIL_OPEN" in output:
                        raise ValueError("Footnote translation requires human intervention.")
                    results.append(output)
                    continue
                except Exception as exc:
                    _require_translation_not_paused(cache_dir)
                    if _is_permanent_translation_error(exc):
                        raise
            outputs = []
            group_failed = False
            for part, key in zip(parts, keys):
                item = read_failures(run_dir)["items"].get(key, {})
                resolution = item.get("resolution", {})
                if resolution:
                    outputs.append(resolution["text"])
                    continue
                try:
                    output = _translate_chunks_ordered(**dict(kwargs, chunks=[part], observer=None))[0]
                    if "BOOKWEAVER_TRANSLATION_FAIL_OPEN" in output:
                        raise ValueError("Footnote translation requires human intervention.")
                    outputs.append(output)
                    clear_failure(run_dir, key)
                except Exception as exc:
                    _require_translation_not_paused(cache_dir)
                    if _is_permanent_translation_error(exc):
                        raise
                    put_failure(run_dir, key, {
                        "segment_id": key, "chunk_index": part.index,
                        "chapter_title": "脚注", "source_pages": [],
                        "source": part.markdown, "input_hash": _chunk_input_hash(part),
                        "error": str(exc), "status": "failed",
                    })
                    pending_failures.append(key)
                    group_failed = True
                    outputs.append("")
            if not group_failed and observer is not None:
                observer.attempt_success(chunk_index=chunk.index, input_hash=_chunk_input_hash(chunk), cache_path=None)
            results.append(SEMANTIC_SPAN_BOUNDARY.join(outputs))
        return results

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
            translated_spans = semantic_translate(
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
                    fallback_groups = (
                        [group[index : index + 12] for index in range(0, len(group), 12)]
                        if len(group) > 12
                        else [[span] for span in group]
                    )
                    parts = []
                    for fallback_group in fallback_groups:
                        fallback_chunk = TranslationChunk(
                            index=(
                                chunk_index
                                + len(semantic_chunks)
                                + fallback_chunk_count
                            ),
                            markdown=(
                                f"\n\n{SEMANTIC_SPAN_BOUNDARY}\n\n".join(
                                    str(span.get("source_text") or "")
                                    for span in fallback_group
                                )
                            ),
                            prompt_instruction=(
                                f"{SEMANTIC_TRANSLATION_POLICY}. Preserve every "
                                f"{SEMANTIC_SPAN_BOUNDARY} marker exactly."
                            ),
                        )
                        fallback_chunk_count += 1
                        fallback_text = semantic_translate(
                            chunks=[fallback_chunk],
                            source_language=settings.source_language,
                            target_language=settings.target_language,
                            translator=translator,
                            cache_dir=cache_dir,
                            retry_count=retry_count,
                            concurrency=concurrency,
                            observer=observer,
                        )[0]
                        fallback_parts = [
                            part.strip()
                            for part in fallback_text.split(
                                SEMANTIC_SPAN_BOUNDARY
                            )
                        ]
                        if len(fallback_parts) != len(fallback_group):
                            individual_chunks = [
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
                                for index, span in enumerate(fallback_group)
                            ]
                            fallback_parts = semantic_translate(
                                chunks=individual_chunks,
                                source_language=settings.source_language,
                                target_language=settings.target_language,
                                translator=translator,
                                cache_dir=cache_dir,
                                retry_count=retry_count,
                                concurrency=concurrency,
                                observer=observer,
                            )
                            fallback_chunk_count += len(individual_chunks)
                        parts.extend(fallback_parts)
                for span, part in zip(group, parts):
                    span["translated_text"] = part
            chunk_index += len(semantic_chunks) + fallback_chunk_count

    from pdf_translator.translation_failures import archive_obsolete_failures
    archive_obsolete_failures(run_dir, active_failure_keys)
    if pending_failures:
        from pdf_translator.translation_failures import TranslationInterventionRequired
        raise TranslationInterventionRequired(len(set(pending_failures)))
    delivery_chapters: list[TranslatedChapter] = []
    for chapter in translated_chapters:
        markdown = str(chapter.markdown or "").strip()
        if chapter.toc and (markdown or chapter.title):
            markdown = ensure_chapter_top_heading(markdown, chapter.title).strip()
        if markdown:
            markdown += "\n"
        delivery_chapters.append(replace(chapter, markdown=markdown))
    delivery_chapters = [
        TranslatedChapter(**payload)
        for payload in rebuild_delivery_toc_chapters(
            [asdict(chapter) for chapter in delivery_chapters],
            target_language=settings.target_language,
        )
    ]

    conservation_failures = verify_segment_processing_order(
        expected_ids=translatable_segment_ids(segment_plan),
        processed_ids=processed_segment_ids,
    )
    write_segment_conservation_report(run_dir, failures=conservation_failures)

    return BookTranslationResult(
        translated_markdown=join_chapter_delivery_markdown([asdict(chapter) for chapter in delivery_chapters]),
        translated_chapters=delivery_chapters,
        source_language=settings.source_language,
        target_language=settings.target_language,
        translator=translator.name,
        chunk_count=chunk_index,
        semantic_content=semantic_content if isinstance(semantic_content, dict) else None,
    )


def render_translation_quality_source(book: dict) -> str:
    """Render the frozen source in the same delivery shape as translated.raw.md.

    ``translation-input.md`` is a transport view: it omits preserved chapters,
    uses generated comments, and does not necessarily use delivery heading
    levels.  The raw translation is a complete delivery view.  Structural and
    protected-token checks must compare like with like while the transport
    file keeps its independent hash check.
    """

    segment_plan = chapter_segments_for_translation(book, max_chars=1)
    segments_by_chapter: dict[str, list[dict]] = {}
    for segment in segment_plan:
        segments_by_chapter.setdefault(str(segment.get("chapter_id") or ""), []).append(segment)

    delivery: list[dict] = []
    pages = book.get("pages") if isinstance(book, dict) else []
    pages = pages if isinstance(pages, list) else []
    for fallback_index, chapter in enumerate(book.get("chapters", []), 1):
        if not isinstance(chapter, dict):
            continue
        if not chapter.get("kind"):
            chapter["kind"] = classify_chapter(chapter, pages=pages)
        chapter_id = str(
            chapter.get("chapter_id") or chapter.get("id") or f"chapter-{fallback_index:03d}"
        )
        if should_translate_chapter(chapter):
            planned = [
                segment
                for segment in segments_by_chapter.get(chapter_id, [])
                if str(segment.get("markdown") or "").strip()
            ]
            markdown = join_chunk_texts(
                [str(segment.get("markdown") or "") for segment in planned],
                [str(segment.get("separator_before", "\n\n")) for segment in planned],
            ).strip()
        else:
            markdown = _chapter_markdown_for_translation(chapter).strip()
        delivery.append(
            {
                "title": str(chapter.get("title") or f"Chapter {fallback_index}"),
                "markdown": markdown,
                "toc": bool(chapter.get("toc", True)),
                "rebuild_toc": bool(chapter.get("rebuild_toc")),
            }
        )
    delivery = rebuild_delivery_toc_chapters(delivery, target_language="en")
    return join_chapter_delivery_markdown(delivery)
