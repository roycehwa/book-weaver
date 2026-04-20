from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.chunking import split_markdown_into_chunks
from pdf_translator.config import RunSettings
from pdf_translator.guardrails import ingest_pdf_guarded
from pdf_translator.models import PipelineArtifacts
from pdf_translator.render import render_pdf_from_markdown
from pdf_translator.translate import build_translator, translate_markdown


def build_output_dir(base_output_dir: Path, source_pdf: Path) -> Path:
    return base_output_dir / source_pdf.stem


def build_artifacts(output_dir: Path) -> PipelineArtifacts:
    return PipelineArtifacts(
        output_dir=output_dir,
        normalized_markdown_path=output_dir / "normalized.md",
        normalized_json_path=output_dir / "normalized.json",
        reconstructed_markdown_path=output_dir / "reconstructed.md",
        translated_markdown_path=output_dir / "translated.md",
        translated_pdf_path=output_dir / "translated.pdf",
        manifest_path=output_dir / "manifest.json",
    )


def run_translation_pipeline(settings: RunSettings) -> PipelineArtifacts:
    output_dir = build_output_dir(settings.output_dir, settings.source_pdf)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = build_artifacts(output_dir)

    normalized, preflight = ingest_pdf_guarded(
        settings.source_pdf,
        profile_name=settings.profile_name,
        timeout_seconds=settings.ingest_timeout_seconds,
        max_file_size_mb=settings.max_file_size_mb,
        max_page_count=settings.max_page_count,
    )
    if settings.source_language is None:
        settings.source_language = normalized.detected_language

    artifacts.normalized_markdown_path.write_text(normalized.raw_markdown, encoding="utf-8")
    artifacts.normalized_json_path.write_text(
        json.dumps(normalized.structured, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    artifacts.reconstructed_markdown_path.write_text(
        normalized.reconstructed_markdown,
        encoding="utf-8",
    )

    chunks = split_markdown_into_chunks(normalized.reconstructed_markdown, settings.max_chunk_chars)
    translator = build_translator(settings.translator)
    translated = translate_markdown(chunks=chunks, settings=settings, translator=translator)

    artifacts.translated_markdown_path.write_text(
        translated.translated_markdown,
        encoding="utf-8",
    )
    render_pdf_from_markdown(
        title=f"{settings.source_pdf.stem} ({translated.target_language})",
        markdown_text=translated.translated_markdown,
        output_path=artifacts.translated_pdf_path,
    )

    manifest = {
        "source_pdf": str(settings.source_pdf),
        "output_dir": str(output_dir),
        "translator": translated.translator,
        "source_language": translated.source_language,
        "target_language": translated.target_language,
        "chunk_count": translated.chunk_count,
        "preflight": preflight.as_dict(),
        "files": {
            "normalized_markdown": str(artifacts.normalized_markdown_path),
            "normalized_json": str(artifacts.normalized_json_path),
            "reconstructed_markdown": str(artifacts.reconstructed_markdown_path),
            "translated_markdown": str(artifacts.translated_markdown_path),
            "translated_pdf": str(artifacts.translated_pdf_path),
        },
    }
    artifacts.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifacts
