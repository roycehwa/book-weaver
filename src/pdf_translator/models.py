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
class PipelineArtifacts:
    output_dir: Path
    normalized_markdown_path: Path
    normalized_json_path: Path
    reconstructed_markdown_path: Path
    translated_markdown_path: Path
    translated_pdf_path: Path
    manifest_path: Path
