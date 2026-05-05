from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class NormalizedDocument:
    source_pdf: Path
    raw_markdown: str
    reconstructed_markdown: str
    structured: dict[str, Any]
    detected_language: str | None = None
    images_dir: Path | None = None


@dataclass(slots=True)
class TranslationChunk:
    index: int
    markdown: str


@dataclass(slots=True)
class TranslationResult:
    translated_markdown: str
    source_language: str | None
    target_language: str
    translator: str
    chunk_count: int
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TranslatedChapter:
    index: int
    title: str
    page_start: int | None
    page_end: int | None
    markdown: str
    source_pages: list[int] = field(default_factory=list)


@dataclass(slots=True)
class BookTranslationResult:
    translated_markdown: str
    translated_chapters: list[TranslatedChapter]
    source_language: str | None
    target_language: str
    translator: str
    chunk_count: int
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PipelineArtifacts:
    output_dir: Path
    normalized_markdown_path: Path
    normalized_json_path: Path
    profile_json_path: Path
    reconstructed_markdown_path: Path
    translation_input_markdown_path: Path
    translated_markdown_path: Path
    translated_pdf_path: Path
    translated_epub_path: Path
    manifest_path: Path
    book_json_path: Path | None = None
    book_markdown_path: Path | None = None
    book_trace_markdown_path: Path | None = None
