from __future__ import annotations

import json
from pathlib import Path
import re

from pdf_translator.book_rebuild import build_book_reconstruction
from pdf_translator.book_views import render_book_markdown, render_translation_input_markdown
from pdf_translator.chunking import split_markdown_into_chunks
from pdf_translator.config import RunSettings
from pdf_translator.epub import render_epub_from_book, validate_epub_internal_hrefs
from pdf_translator.guardrails import ingest_pdf_guarded
from pdf_translator.models import PipelineArtifacts
from pdf_translator.profile import build_document_profile
from pdf_translator.render import render_pdf_from_markdown
from pdf_translator.translate import (
    build_translator,
    estimate_translation_chunk_count,
    translate_book_chapters,
    translate_markdown,
)


def build_output_dir(base_output_dir: Path, source_pdf: Path) -> Path:
    return base_output_dir / source_pdf.stem


def safe_delivery_file_stem(source_path: Path, target_language: str) -> str:
    source_stem = source_path.stem.strip() or "book"
    source_stem = re.sub(r"[\\/:*?\"<>|]+", " ", source_stem)
    source_stem = re.sub(r"\s+", " ", source_stem).strip(" .")
    if len(source_stem) > 140:
        source_stem = source_stem[:140].rstrip(" .")
    target = re.sub(r"[\\/:*?\"<>|]+", " ", target_language.strip() or "translated")
    target = re.sub(r"\s+", " ", target).strip(" .")
    return f"{source_stem} ({target})"


def build_artifacts(output_dir: Path, source_pdf: Path, target_language: str) -> PipelineArtifacts:
    delivery_stem = safe_delivery_file_stem(source_pdf, target_language)
    return PipelineArtifacts(
        output_dir=output_dir,
        normalized_markdown_path=output_dir / "normalized.md",
        normalized_json_path=output_dir / "normalized.json",
        profile_json_path=output_dir / "profile.json",
        reconstructed_markdown_path=output_dir / "reconstructed.md",
        translation_input_markdown_path=output_dir / "translation-input.md",
        translated_markdown_path=output_dir / "translated.md",
        translated_pdf_path=output_dir / f"{delivery_stem}.pdf",
        translated_epub_path=output_dir / f"{delivery_stem}.epub",
        manifest_path=output_dir / "manifest.json",
        book_json_path=output_dir / "book.json",
        book_markdown_path=output_dir / "book.md",
        book_trace_markdown_path=output_dir / "book-trace.md",
    )


def _build_chapter_report(book: dict, *, max_chunk_chars: int) -> dict:
    chapters = []
    for chapter in book.get("chapters", []):
        markdown = str(chapter.get("markdown") or "")
        chunks = split_markdown_into_chunks(markdown, max_chunk_chars)
        translation_chunks = estimate_translation_chunk_count(markdown, max_chunk_chars)
        source_pages = [int(page_no) for page_no in chapter.get("source_pages", [])]
        chapters.append(
            {
                "index": chapter.get("index"),
                "chapter_id": chapter.get("chapter_id"),
                "title": chapter.get("title"),
                "page_start": chapter.get("page_start"),
                "page_end": chapter.get("page_end"),
                "source_page_count": len(source_pages),
                "source_pages": source_pages,
                "char_count": len(markdown),
                "estimated_chunk_count": len(chunks),
                "estimated_translation_chunk_count": translation_chunks,
                "placeholder_title": str(chapter.get("title") or "").startswith("Untitled Section"),
                "translate": bool(chapter.get("translate", True)),
                "preserve_original": bool(chapter.get("preserve_original", False)),
            }
        )
    return {
        "chapter_source": book.get("metadata", {}).get("chapter_source", "unknown"),
        "outline_entry_count": book.get("metadata", {}).get("outline_entry_count", 0),
        "chapter_count": len(chapters),
        "placeholder_title_count": sum(1 for chapter in chapters if chapter["placeholder_title"]),
        "preserved_original_count": sum(1 for chapter in chapters if chapter["preserve_original"]),
        "estimated_chunk_count": sum(
            chapter["estimated_translation_chunk_count"] for chapter in chapters if chapter["translate"]
        ),
        "chapters": chapters,
    }


def run_translation_pipeline(settings: RunSettings) -> PipelineArtifacts:
    output_dir = build_output_dir(settings.output_dir, settings.source_pdf)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = build_artifacts(output_dir, settings.source_pdf, settings.target_language)

    normalized, preflight = ingest_pdf_guarded(
        settings.source_pdf,
        profile_name=settings.profile_name,
        timeout_seconds=settings.ingest_timeout_seconds,
        max_file_size_mb=settings.max_file_size_mb,
        max_page_count=settings.max_page_count,
        output_dir=output_dir,
    )
    if settings.source_language is None:
        settings.source_language = normalized.detected_language

    artifacts.normalized_markdown_path.write_text(normalized.raw_markdown, encoding="utf-8")
    artifacts.normalized_json_path.write_text(
        json.dumps(normalized.structured, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    profile = build_document_profile(settings.source_pdf, normalized.structured, profile_name=settings.profile_name)
    profile["preflight"] = preflight.as_dict()
    artifacts.profile_json_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    artifacts.reconstructed_markdown_path.write_text(
        normalized.reconstructed_markdown,
        encoding="utf-8",
    )

    translation_input_markdown = normalized.reconstructed_markdown
    book: dict | None = None
    extra_files: dict[str, str] = {
        "profile_json": str(artifacts.profile_json_path),
    }
    if profile["profile"] == "book":
        book_images_dir = output_dir / "book-images"
        book = build_book_reconstruction(
            normalized.structured,
            source_pdf=settings.source_pdf,
            images_dir=book_images_dir,
        )
        if render_translation_input_markdown(book).strip():
            translation_input_markdown = render_translation_input_markdown(book)
        artifacts.book_json_path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.book_markdown_path.write_text(render_book_markdown(book), encoding="utf-8")
        artifacts.book_trace_markdown_path.write_text(render_book_markdown(book, include_trace=True), encoding="utf-8")
        chapter_report_path = output_dir / "chapter-report.json"
        chapter_report_path.write_text(
            json.dumps(_build_chapter_report(book, max_chunk_chars=settings.max_chunk_chars), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        chapters_dir = output_dir / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        for stale in chapters_dir.glob("*.md"):
            stale.unlink()
        for chapter in book["chapters"]:
            chapter_path = chapters_dir / f"{chapter['index']:03d}.md"
            chapter_path.write_text(chapter["markdown"], encoding="utf-8")
        extra_files["book_json"] = str(artifacts.book_json_path)
        extra_files["book_markdown"] = str(artifacts.book_markdown_path)
        extra_files["book_trace_markdown"] = str(artifacts.book_trace_markdown_path)
        extra_files["chapter_report"] = str(chapter_report_path)
        extra_files["book_images_dir"] = str(book_images_dir)
        extra_files["chapters_dir"] = str(chapters_dir)

    artifacts.translation_input_markdown_path.write_text(
        translation_input_markdown,
        encoding="utf-8",
    )

    translator = build_translator(settings.translator)
    translation_cache_dir = output_dir / "translation-cache"
    translated_chapters = None
    if book is not None:
        translated = translate_book_chapters(
            book=book,
            settings=settings,
            translator=translator,
            cache_dir=translation_cache_dir,
            concurrency=settings.translation_concurrency,
        )
        translated_chapters = translated.translated_chapters
    else:
        chunks = split_markdown_into_chunks(translation_input_markdown, settings.max_chunk_chars)
        translated = translate_markdown(
            chunks=chunks,
            settings=settings,
            translator=translator,
            cache_dir=translation_cache_dir,
            concurrency=settings.translation_concurrency,
        )

    artifacts.translated_markdown_path.write_text(
        translated.translated_markdown,
        encoding="utf-8",
    )
    rendered_files: dict[str, str] = {}
    if settings.output_format in {"pdf", "both"}:
        render_pdf_from_markdown(
            title=f"{settings.source_pdf.stem} ({translated.target_language})",
            markdown_text=translated.translated_markdown,
            output_path=artifacts.translated_pdf_path,
            images_dir=normalized.images_dir,
        )
        rendered_files["translated_pdf"] = str(artifacts.translated_pdf_path)
    if settings.output_format in {"epub", "both"}:
        epub_book = book or {
            "metadata": {"schema": "markdown_fallback", "schema_version": 1},
            "chapters": [
                {
                    "index": 1,
                    "title": settings.source_pdf.stem,
                    "markdown": translated.translated_markdown,
                    "source_pages": [],
                }
            ],
        }
        epub_chapters = translated_chapters or [
            {
                "index": 1,
                "title": settings.source_pdf.stem,
                "markdown": translated.translated_markdown,
                "source_pages": [],
            }
        ]
        render_epub_from_book(
            book=epub_book,
            translated_chapters=epub_chapters,
            output_path=artifacts.translated_epub_path,
            title=f"{settings.source_pdf.stem} ({translated.target_language})",
            language=translated.target_language,
        )
        rendered_files["translated_epub"] = str(artifacts.translated_epub_path)
        rendered_files["epub_href_validation"] = validate_epub_internal_hrefs(artifacts.translated_epub_path)

    manifest = {
        "source_pdf": str(settings.source_pdf),
        "output_dir": str(output_dir),
        "translator": translated.translator,
        "source_language": translated.source_language,
        "target_language": translated.target_language,
        "chunk_count": translated.chunk_count,
        "translation": {
            "concurrency": settings.translation_concurrency,
            "max_chunk_chars": settings.max_chunk_chars,
            "cache_dir": str(translation_cache_dir),
        },
        "preflight": preflight.as_dict(),
        "render": {
            "format": settings.output_format,
            "policy": (book or {}).get("render_policy", {}),
        },
        "files": {
            "normalized_markdown": str(artifacts.normalized_markdown_path),
            "normalized_json": str(artifacts.normalized_json_path),
            "profile_json": str(artifacts.profile_json_path),
            "reconstructed_markdown": str(artifacts.reconstructed_markdown_path),
            "translation_input_markdown": str(artifacts.translation_input_markdown_path),
            "translated_markdown": str(artifacts.translated_markdown_path),
            "translation_cache_dir": str(translation_cache_dir),
            "images_dir": str(normalized.images_dir) if normalized.images_dir else None,
            **rendered_files,
            **extra_files,
        },
    }
    artifacts.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifacts
