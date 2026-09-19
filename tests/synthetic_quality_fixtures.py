"""Synthetic run-dir helpers for translation quality gate tests."""

from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.chapter_segments import build_chapter_segments_from_reading_units
from pdf_translator.reading_units import build_reading_units
from pdf_translator.translation_quality import write_translation_quality_bundle


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
    if text_operation != "translate":
        write_translation_quality_bundle(
            run_dir,
            text_operation=str(text_operation),
            source_markdown=(run_dir / "translation-input.md").read_text(encoding="utf-8")
            if (run_dir / "translation-input.md").exists()
            else (run_dir / "book.md").read_text(encoding="utf-8")
            if (run_dir / "book.md").exists()
            else "",
            review_items=[],
        )
        return
    source_path = run_dir / "translation-input.md"
    if not source_path.exists():
        source_path = run_dir / "book.md"
    raw_path = run_dir / "translated.raw.md"
    if not raw_path.exists() and (run_dir / "translated.md").exists():
        raw_path.write_text((run_dir / "translated.md").read_text(encoding="utf-8"), encoding="utf-8")
    if not (run_dir / "translated.cleaned.md").exists() and raw_path.exists():
        (run_dir / "translated.cleaned.md").write_text(raw_path.read_text(encoding="utf-8"), encoding="utf-8")
    if raw_path.exists():
        source = raw_path.read_text(encoding="utf-8")
    elif source_path.exists():
        source = source_path.read_text(encoding="utf-8")
    else:
        source = "Synthetic source.\n"
    write_translation_quality_bundle(
        run_dir,
        text_operation="translate",
        source_markdown=source,
        review_items=[],
    )


def write_synthetic_reading_units(run_dir: Path) -> None:
    from tests.test_translation_quality import _write_reading_units

    _write_reading_units(run_dir)
