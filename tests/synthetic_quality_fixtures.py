"""Synthetic run-dir helpers for translation quality gate tests."""

from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.chapter_segments import build_chapter_segments_from_reading_units
from pdf_translator.reading_units import build_reading_units
from pdf_translator.translation_quality import write_translation_quality_bundle


def write_synthetic_reading_units(run_dir: Path) -> None:
    book = {
        "chapters": [
            {
                "index": 1,
                "chapter_id": "ch-1",
                "title": "Chapter",
                "markdown": "# Chapter\n\nHello world.\n",
                "source_pages": [1],
                "translate": True,
            }
        ]
    }
    payload = build_reading_units(book, source_path=Path("synthetic.pdf"), translation_authority=True)
    (run_dir / "reading-units.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = "# Chapter\n\nHello world.\n"
    unit_id = payload["units"][0]["unit_id"] if payload.get("units") else "unit-1"
    segments = {
        "schema": "bookweaver_chapter_segments_v1",
        "reading_units_fingerprint": payload["document_fingerprint"],
        "segments": [
            {
                "segment_id": "seg-1",
                "chapter_id": "ch-1",
                "block_index": 0,
                "markdown": markdown,
                "unit_ids": [unit_id],
                "source_location": {"line": 3, "resource_path": "synthetic.pdf"},
            }
        ],
    }
    (run_dir / "chapter-segments.json").write_text(json.dumps(segments, indent=2), encoding="utf-8")
    (run_dir / "translation-input.md").write_text(markdown, encoding="utf-8")


def ensure_confirmed_reading_units_for_translation(run_dir: Path, *, max_chars: int = 9000) -> None:
    run_dir = run_dir.expanduser().resolve()
    reading_units_path = run_dir / "reading-units.json"
    if not (run_dir / "book.json").exists():
        return
    if reading_units_path.exists():
        payload = json.loads(reading_units_path.read_text(encoding="utf-8"))
        if payload.get("generation", {}).get("translation_authority") is True:
            return
    book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))
    source_pdf = Path("synthetic.epub")
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_pdf = Path(str(manifest.get("source_pdf") or source_pdf))
    confirmed = build_reading_units(
        book,
        source_path=source_pdf,
        translation_authority=True,
    )
    reading_units_path.write_text(json.dumps(confirmed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    segment_plan = build_chapter_segments_from_reading_units(confirmed, max_chars=max_chars)
    (run_dir / "chapter-segments.json").write_text(
        json.dumps(segment_plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _source_markdown_for_quality(run_dir: Path) -> str:
    translation_input = run_dir / "translation-input.md"
    if translation_input.exists():
        return translation_input.read_text(encoding="utf-8")
    book_md = run_dir / "book.md"
    if book_md.exists():
        return book_md.read_text(encoding="utf-8")
    return "Synthetic source.\n"


def write_minimal_translation_quality_artifacts(run_dir: Path) -> None:
    """Write passing quality reports for a translate-mode manifest if outputs exist."""
    run_dir = run_dir.expanduser().resolve()
    if not (run_dir / "reading-units.json").exists() and (run_dir / "book.json").exists():
        ensure_confirmed_reading_units_for_translation(run_dir)
    translation_input = run_dir / "translation-input.md"
    if not translation_input.exists() and (run_dir / "book.md").exists():
        translation_input.write_text((run_dir / "book.md").read_text(encoding="utf-8"), encoding="utf-8")
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    text_operation = manifest.get("text_operation") or "translate"
    source_markdown = _source_markdown_for_quality(run_dir)
    if text_operation != "translate":
        write_translation_quality_bundle(
            run_dir,
            text_operation=str(text_operation),
            source_markdown=source_markdown,
            review_items=[],
        )
        return
    raw_path = run_dir / "translated.raw.md"
    if not raw_path.exists() and (run_dir / "translated.md").exists():
        raw_path.write_text(source_markdown, encoding="utf-8")
    if not (run_dir / "translated.cleaned.md").exists() and raw_path.exists():
        (run_dir / "translated.cleaned.md").write_text(raw_path.read_text(encoding="utf-8"), encoding="utf-8")
    if not (run_dir / "translated.md").exists() and raw_path.exists():
        (run_dir / "translated.md").write_text(raw_path.read_text(encoding="utf-8"), encoding="utf-8")
    write_translation_quality_bundle(
        run_dir,
        text_operation="translate",
        source_markdown=source_markdown,
        review_items=[],
    )
