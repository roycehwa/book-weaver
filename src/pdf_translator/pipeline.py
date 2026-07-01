from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
import re
from typing import Any

from pdf_translator.book_rebuild import build_book_reconstruction
from pdf_translator.book_views import render_book_markdown, render_translation_input_markdown
from pdf_translator.chunking import split_markdown_into_chunks
from pdf_translator.config import RunSettings
from pdf_translator.epub import render_epub_from_book, validate_epub_internal_hrefs
from pdf_translator.glossary import (
    extract_glossary_candidates,
    glossary_manifest_files,
    load_active_entries_for_translation,
    load_active_glossary_if_present,
)
from pdf_translator.guardrails import ingest_pdf_guarded
from pdf_translator.job_control import create_translation_job
from pdf_translator.jobs import resolve_text_operation
from pdf_translator.pdf_text_repair import (
    repair_book_dict,
    repair_pdf_markdown,
    write_ingest_quality_report,
)
from pdf_translator.models import (
    BookTranslationResult,
    PipelineArtifacts,
    TranslatedChapter,
    TranslationResult,
)
from pdf_translator.profile import build_document_profile
from pdf_translator.render import render_pdf_from_markdown
from pdf_translator.review import build_review_artifacts, write_review_artifacts
from pdf_translator.translate import (
    build_translator,
    estimate_translation_chunk_count,
    translate_book_chapters,
    translate_markdown,
    _chapter_markdown_for_translation,
)
from pdf_translator.workflow import (
    STAGE_AWAITING_GLOSSARY,
    STAGE_AWAITING_HUMAN_REVIEW,
    begin_translation,
    require_glossary_ready,
    write_workflow,
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


def should_skip_translation(source_language: str | None, target_language: str) -> bool:
    return _is_chinese_language(source_language) and _is_chinese_language(target_language)


def _is_chinese_language(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower().replace("_", "-")
    return normalized == "zh" or normalized.startswith("zh-") or normalized in {"chinese", "cn"}


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


def _fallback_book_from_markdown(source_path: Path, markdown: str) -> dict:
    return {
        "metadata": {"schema": "markdown_fallback", "schema_version": 1},
        "chapters": [
            {
                "index": 1,
                "chapter_id": "ch-001-document",
                "title": source_path.stem,
                "markdown": markdown,
                "source_pages": [],
                "toc": True,
            }
        ],
    }


def _translated_chapters_payload(
    *,
    source_path: Path,
    translated_markdown: str,
    translated_chapters: list[TranslatedChapter] | None,
) -> list[dict]:
    if translated_chapters is not None:
        return [asdict(chapter) if isinstance(chapter, TranslatedChapter) else dict(chapter) for chapter in translated_chapters]
    return [
        {
            "index": 1,
            "chapter_id": "ch-001-document",
            "title": source_path.stem,
            "markdown": translated_markdown,
            "source_pages": [],
            "toc": True,
        }
    ]


def _load_existing_run_context(
    settings: RunSettings,
) -> tuple[PipelineArtifacts, dict | None, str, dict[str, Any], dict[str, str], dict[str, Any]]:
    run_dir = settings.existing_run_dir
    if run_dir is None:
        raise ValueError("existing_run_dir is required.")
    run_dir = run_dir.expanduser().resolve()
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"Run directory missing manifest.json: {run_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_pdf = Path(str(manifest.get("source_pdf") or settings.source_pdf)).expanduser().resolve()
    artifacts = build_artifacts(run_dir, source_pdf, settings.target_language)
    book: dict | None = None
    extra_files: dict[str, str] = {}
    if artifacts.book_json_path.exists():
        book = json.loads(artifacts.book_json_path.read_text(encoding="utf-8"))
        book = repair_book_dict(book)
        artifacts.book_json_path.write_text(json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        extra_files["book_json"] = str(artifacts.book_json_path)
    if artifacts.book_markdown_path.exists():
        extra_files["book_markdown"] = str(artifacts.book_markdown_path)
    translation_input_markdown = ""
    if artifacts.translation_input_markdown_path.exists():
        translation_input_markdown = artifacts.translation_input_markdown_path.read_text(encoding="utf-8")
    if book is not None:
        repaired_input = render_translation_input_markdown(book)
        if repaired_input.strip():
            translation_input_markdown = repaired_input
            artifacts.translation_input_markdown_path.write_text(repaired_input, encoding="utf-8")
            write_ingest_quality_report(run_dir, source_markdown=repaired_input)
    profile: dict[str, Any] = {}
    if artifacts.profile_json_path.exists():
        profile = json.loads(artifacts.profile_json_path.read_text(encoding="utf-8"))
    preflight = dict(manifest.get("preflight") or {})
    if settings.source_language is None:
        settings.source_language = manifest.get("source_language") or profile.get("detected_language")
    return artifacts, book, translation_input_markdown, profile, extra_files, preflight


def _prepare_intake_artifacts(settings: RunSettings) -> tuple[
    PipelineArtifacts,
    object,
    object,
    dict,
    dict | None,
    str,
    dict[str, str],
]:
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
        repair_pdf_markdown(normalized.reconstructed_markdown),
        encoding="utf-8",
    )

    translation_input_markdown = repair_pdf_markdown(normalized.reconstructed_markdown)
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
        book = repair_book_dict(book)
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
    write_ingest_quality_report(artifacts.output_dir, source_markdown=translation_input_markdown)

    return artifacts, normalized, preflight, profile, book, translation_input_markdown, extra_files


def _estimated_translation_total(
    book: dict | None,
    translation_input_markdown: str,
    *,
    max_chunk_chars: int,
) -> int:
    if book is None:
        return len(split_markdown_into_chunks(translation_input_markdown, max_chunk_chars))
    total = 0
    for chapter in book.get("chapters", []):
        if not bool(chapter.get("translate", True)):
            continue
        chapter_markdown = _chapter_markdown_for_translation(chapter)
        total += estimate_translation_chunk_count(chapter_markdown, max_chunk_chars)
    return total


def _book_without_translation(book: dict, settings: RunSettings) -> BookTranslationResult:
    translated_chapters: list[TranslatedChapter] = []
    translated_markdown_parts: list[str] = []
    for chapter in book.get("chapters", []):
        markdown = str(chapter.get("markdown") or "").strip()
        if markdown:
            markdown += "\n"
            translated_markdown_parts.append(markdown.strip())
        sip = chapter.get("source_internal_path")
        translated_chapters.append(
            TranslatedChapter(
                index=int(chapter.get("index", len(translated_chapters) + 1)),
                chapter_id=str(chapter.get("chapter_id") or "") or None,
                title=str(chapter.get("title") or f"Chapter {len(translated_chapters) + 1}"),
                page_start=chapter.get("page_start"),
                page_end=chapter.get("page_end"),
                markdown=markdown,
                source_pages=[int(page_no) for page_no in chapter.get("source_pages", [])],
                source_internal_path=sip if isinstance(sip, str) else None,
                toc=bool(chapter.get("toc", True)),
            )
        )
    return BookTranslationResult(
        translated_markdown="\n\n".join(translated_markdown_parts).strip() + "\n",
        translated_chapters=translated_chapters,
        source_language=settings.source_language,
        target_language=settings.target_language,
        translator="skipped",
        chunk_count=0,
    )


def _markdown_without_translation(markdown: str, settings: RunSettings) -> TranslationResult:
    return TranslationResult(
        translated_markdown=markdown.strip() + "\n",
        source_language=settings.source_language,
        target_language=settings.target_language,
        translator="skipped",
        chunk_count=0,
    )


def run_intake_pipeline(settings: RunSettings) -> PipelineArtifacts:
    artifacts, normalized, preflight, profile, book, _translation_input_markdown, extra_files = _prepare_intake_artifacts(
        settings
    )

    manifest = {
        "mode": "intake",
        "source_pdf": str(settings.source_pdf),
        "output_dir": str(artifacts.output_dir),
        "translator": None,
        "source_language": settings.source_language,
        "target_language": None,
        "chunk_count": 0,
        "translation": {
            "mode": "not_requested",
            "cache_dir": None,
        },
        "preflight": preflight.as_dict(),
        "render": {
            "format": "none",
            "policy": (book or {}).get("render_policy", {}),
        },
        "files": {
            "normalized_markdown": str(artifacts.normalized_markdown_path),
            "normalized_json": str(artifacts.normalized_json_path),
            "profile_json": str(artifacts.profile_json_path),
            "reconstructed_markdown": str(artifacts.reconstructed_markdown_path),
            "translation_input_markdown": str(artifacts.translation_input_markdown_path),
            "images_dir": str(normalized.images_dir) if normalized.images_dir else None,
            **extra_files,
        },
    }
    artifacts.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if (artifacts.output_dir / "book.json").exists():
        extract_glossary_candidates(artifacts.output_dir)
        write_workflow(artifacts.output_dir, stage=STAGE_AWAITING_GLOSSARY)
        glossary_files = glossary_manifest_files(artifacts.output_dir)
        workflow_path = artifacts.output_dir / "workflow.json"
        if workflow_path.exists():
            glossary_files["workflow"] = str(workflow_path)
        manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
        manifest["files"] = {**manifest.get("files", {}), **glossary_files}
        artifacts.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return artifacts


def run_translation_pipeline(
    settings: RunSettings,
    on_stage: Callable[[str, dict[str, Any]], None] | None = None,
) -> PipelineArtifacts:
    def enter_stage(stage: str, **data: Any) -> None:
        if on_stage is not None:
            on_stage(stage, {"stage_percent": 0, **data})

    using_existing_run = settings.existing_run_dir is not None
    if using_existing_run:
        if settings.require_glossary_ready:
            require_glossary_ready(settings.existing_run_dir)
            begin_translation(settings.existing_run_dir)
        artifacts, book, translation_input_markdown, profile, extra_files, preflight = _load_existing_run_context(
            settings
        )
        normalized_images_dir = None
        if (artifacts.output_dir / "book-images").exists():
            normalized_images_dir = artifacts.output_dir / "book-images"
    else:
        enter_stage("ingesting")
        artifacts, normalized, preflight_obj, profile, book, translation_input_markdown, extra_files = _prepare_intake_artifacts(
            settings
        )
        preflight = preflight_obj.as_dict()
        normalized_images_dir = normalized.images_dir

    text_operation = resolve_text_operation(
        settings.processing_mode,
        settings.source_language,
        settings.target_language,
    )
    skip_translation = text_operation == "preserve"

    enter_stage("reconstructing", source_language=settings.source_language)

    translation_cache_dir = artifacts.output_dir / "translation-cache"
    translated_chapters = None
    translation_mode = "translated"
    job_files: dict[str, str] = {}
    enter_stage(
        "preserving" if skip_translation else "translating",
        source_language=settings.source_language,
        text_operation=text_operation,
    )
    if skip_translation:
        if settings.processing_mode == "preserve":
            translation_mode = "preserved"
        else:
            translation_mode = "skipped_same_language"
        if book is not None:
            translated = _book_without_translation(book, settings)
            translated_chapters = translated.translated_chapters
        else:
            translated = _markdown_without_translation(translation_input_markdown, settings)
    else:
        if not using_existing_run and book is not None and (artifacts.output_dir / "book.json").exists():
            extract_glossary_candidates(artifacts.output_dir)
        if settings.ignore_translation_cache and translation_cache_dir.exists():
            shutil.rmtree(translation_cache_dir)
        glossary_entries = load_active_entries_for_translation(artifacts.output_dir)
        translation_settings = (
            replace(settings, glossary_entries=glossary_entries)
            if glossary_entries
            else settings
        )
        translator = build_translator(settings.translator)
        total_chunks = _estimated_translation_total(
            book,
            translation_input_markdown,
            max_chunk_chars=settings.max_chunk_chars,
        )
        observer = create_translation_job(
            run_dir=artifacts.output_dir,
            translator=settings.translator,
            source_language=settings.source_language,
            target_language=settings.target_language,
            total_chunks=total_chunks,
            concurrency=settings.translation_concurrency,
            max_chunk_chars=settings.max_chunk_chars,
            resume=settings.resume_translation,
            live_progress=settings.show_translation_progress and sys.stderr.isatty(),
            progress_sink=settings.translation_progress_sink,
        )
        job_files = {
            "translation_job": str(artifacts.output_dir / "jobs" / "translation-job.json"),
            "translation_progress": str(artifacts.output_dir / "jobs" / "progress.json"),
            "translation_events": str(artifacts.output_dir / "jobs" / "translation-events.jsonl"),
        }
        try:
            if book is not None:
                translated = translate_book_chapters(
                    book=book,
                    settings=translation_settings,
                    translator=translator,
                    cache_dir=translation_cache_dir,
                    concurrency=settings.translation_concurrency,
                    observer=observer,
                )
                translated_chapters = translated.translated_chapters
            else:
                chunks = split_markdown_into_chunks(translation_input_markdown, settings.max_chunk_chars)
                translated = translate_markdown(
                    chunks=chunks,
                    settings=translation_settings,
                    translator=translator,
                    cache_dir=translation_cache_dir,
                    concurrency=settings.translation_concurrency,
                    observer=observer,
                )
        except Exception:
            observer.finish(status="failed")
            raise
        else:
            observer.finish(status="completed")

    enter_stage("validating")
    artifacts.translated_markdown_path.write_text(
        translated.translated_markdown,
        encoding="utf-8",
    )
    polished_markdown = translated.translated_markdown
    if (
        not skip_translation
        and text_operation == "translate"
        and settings.target_language.lower().startswith("zh")
        and book is not None
    ):
        try:
            from pdf_translator.polish import run_polish, scan_polish_candidates

            if scan_polish_candidates(translated.translated_markdown):
                polish_result = run_polish(
                    run_dir=artifacts.output_dir,
                    target_language=settings.target_language,
                    translator_name=settings.translator,
                )
                polished_path = artifacts.output_dir / "translated.polished.md"
                if polished_path.exists():
                    polished_markdown = polished_path.read_text(encoding="utf-8")
                    artifacts.translated_markdown_path.write_text(polished_markdown, encoding="utf-8")
                    extra_files["translated_polished_markdown"] = str(polished_path)
                    extra_files["polish_report"] = str(artifacts.output_dir / "polish-report.json")
        except Exception:
            polished_markdown = translated.translated_markdown
    translated = replace(translated, translated_markdown=polished_markdown)
    translated_chapters_payload = _translated_chapters_payload(
        source_path=settings.source_pdf,
        translated_markdown=translated.translated_markdown,
        translated_chapters=translated_chapters,
    )
    translated_chapters_path = artifacts.output_dir / "translated-chapters.json"
    translated_chapters_path.write_text(
        json.dumps(translated_chapters_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    extra_files["translated_chapters"] = str(translated_chapters_path)
    enter_stage("pre_review")
    review_source_book = book or _fallback_book_from_markdown(settings.source_pdf, translation_input_markdown)
    review_artifacts = build_review_artifacts(
        source_path=settings.source_pdf,
        target_language=translated.target_language,
        text_operation=text_operation,
        book=review_source_book,
        translated_chapters=translated_chapters_payload,
        cache_dir=translation_cache_dir if translation_cache_dir.exists() else None,
        max_chunk_chars=settings.max_chunk_chars,
        run_dir=artifacts.output_dir,
    )
    extra_files.update(write_review_artifacts(artifacts.output_dir, review_artifacts))
    rendered_files: dict[str, str] = {}
    if settings.output_format in {"pdf", "both"}:
        render_pdf_from_markdown(
            title=f"{settings.source_pdf.stem} ({translated.target_language})",
            markdown_text=translated.translated_markdown,
            output_path=artifacts.translated_pdf_path,
            images_dir=normalized_images_dir,
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

    extra_files.update(glossary_manifest_files(artifacts.output_dir))
    workflow_path = artifacts.output_dir / "workflow.json"
    if workflow_path.exists():
        extra_files["workflow"] = str(workflow_path)
    write_workflow(artifacts.output_dir, stage=STAGE_AWAITING_HUMAN_REVIEW)

    manifest = {
        "mode": "translate",
        "source_pdf": str(settings.source_pdf),
        "output_dir": str(artifacts.output_dir),
        "translator": translated.translator,
        "processing_mode": settings.processing_mode,
        "text_operation": text_operation,
        "source_language": translated.source_language,
        "target_language": translated.target_language,
        "chunk_count": translated.chunk_count,
        "translation": {
            "mode": translation_mode,
            "concurrency": settings.translation_concurrency,
            "max_chunk_chars": settings.max_chunk_chars,
            "cache_dir": str(translation_cache_dir) if translation_mode == "translated" else None,
            "resume": settings.resume_translation,
            "ignore_cache": settings.ignore_translation_cache,
        },
        "preflight": preflight if isinstance(preflight, dict) else preflight.as_dict(),
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
            "translation_cache_dir": str(translation_cache_dir) if translation_mode != "skipped_same_language" else None,
            "images_dir": str(normalized_images_dir) if normalized_images_dir else None,
            **rendered_files,
            **extra_files,
            **job_files,
        },
    }
    artifacts.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifacts
