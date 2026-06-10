from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_or_name(path: Path | None, run_dir: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(run_dir))
    except ValueError:
        return str(path)


def _resolve_artifact_path(value: object, run_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if not path.is_absolute():
        path = run_dir / path
    return path.resolve()


def _approved_review_versions(run_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    versions: list[tuple[Path, dict[str, Any]]] = []
    for manifest_path in (run_dir / "versions").glob("*/version-manifest.json"):
        manifest = _read_json(manifest_path)
        review = manifest.get("review") if isinstance(manifest.get("review"), dict) else {}
        status = str(review.get("status") or manifest.get("approval_status") or "").strip().lower()
        if status != "approved":
            continue
        markdown_path = _resolve_artifact_path(
            (manifest.get("files") or {}).get("translated_markdown"),
            run_dir,
        )
        if markdown_path is None or not markdown_path.exists():
            continue
        versions.append((manifest_path, manifest))
    return sorted(
        versions,
        key=lambda item: (
            str((item[1].get("review") or {}).get("approved_at") or item[1].get("created_at") or ""),
            item[0].stat().st_mtime,
        ),
    )


def resolve_phase_a_handoff(run_dir: Path) -> dict[str, Any]:
    """Resolve the stable Phase A -> Phase B content handoff.

    Source-only books remain valid in any language. A translated reading layer is
    added only when a translated or reviewed artifact actually exists.
    """
    run_dir = run_dir.expanduser().resolve()
    manifest = _read_json(run_dir / "manifest.json")
    source_markdown = run_dir / "book.md"
    source_language = manifest.get("source_language")
    target_language = manifest.get("target_language")

    approved_versions = _approved_review_versions(run_dir)
    if approved_versions:
        version_manifest_path, version_manifest = approved_versions[-1]
        files = version_manifest.get("files") or {}
        translation_markdown = _resolve_artifact_path(files.get("translated_markdown"), run_dir)
        translation_epub = _resolve_artifact_path(files.get("translated_epub"), run_dir)
        review = version_manifest.get("review") or {}
        return {
            "mode": "source_plus_translation",
            "content_source": "reviewed_translation",
            "source_language": source_language,
            "reading_language": version_manifest.get("target_language") or target_language,
            "source_markdown": source_markdown if source_markdown.exists() else None,
            "translation_markdown": translation_markdown,
            "reading_markdown": translation_markdown,
            "reading_epub": translation_epub,
            "review_status": "approved",
            "review_version": version_manifest.get("version"),
            "review_manifest": version_manifest_path,
            "approved_at": review.get("approved_at"),
        }

    if (run_dir / "review_state.json").exists():
        return {
            "mode": "source_only",
            "content_source": "source_book_pending_translation_review",
            "source_language": source_language,
            "reading_language": source_language,
            "source_markdown": source_markdown if source_markdown.exists() else None,
            "translation_markdown": None,
            "reading_markdown": source_markdown if source_markdown.exists() else None,
            "reading_epub": None,
            "review_status": "pending",
            "review_version": None,
            "review_manifest": None,
            "approved_at": None,
        }

    polished_markdown = run_dir / "translated.polished.md"
    translated_markdown = run_dir / "translated.md"
    if polished_markdown.exists() or translated_markdown.exists():
        selected = polished_markdown if polished_markdown.exists() else translated_markdown
        return {
            "mode": "source_plus_translation",
            "content_source": "polished_translation" if selected == polished_markdown else "machine_translation",
            "source_language": source_language,
            "reading_language": target_language,
            "source_markdown": source_markdown if source_markdown.exists() else None,
            "translation_markdown": selected,
            "reading_markdown": selected,
            "reading_epub": None,
            "review_status": "not_reviewed",
            "review_version": None,
            "review_manifest": None,
            "approved_at": None,
        }

    return {
        "mode": "source_only",
        "content_source": "source_book",
        "source_language": source_language,
        "reading_language": source_language,
        "source_markdown": source_markdown if source_markdown.exists() else None,
        "translation_markdown": None,
        "reading_markdown": source_markdown if source_markdown.exists() else None,
        "reading_epub": None,
        "review_status": "not_applicable",
        "review_version": None,
        "review_manifest": None,
        "approved_at": None,
    }


def _select_final_epub(run_dir: Path, manifest: dict[str, Any]) -> Path | None:
    polish_report = _read_json(run_dir / "polish-report.json")
    polished = polish_report.get("outputs", {}).get("translated_polished_epub")
    if isinstance(polished, str) and Path(polished).exists():
        return Path(polished)

    translated_epub = manifest.get("files", {}).get("translated_epub")
    if isinstance(translated_epub, str) and Path(translated_epub).exists():
        return Path(translated_epub)

    epubs = sorted(
        path
        for path in run_dir.glob("*.epub")
        if not path.name.startswith(".") and path.name != run_dir.with_suffix(".epub").name
    )
    return epubs[-1] if epubs else None


def _chapter_id_coverage(book: dict[str, Any]) -> float:
    chapters = book.get("chapters") or []
    if not chapters:
        return 0.0
    covered = sum(1 for chapter in chapters if chapter.get("chapter_id"))
    return round(covered / len(chapters), 5)


def finalize_run(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    manifest = _read_json(run_dir / "manifest.json")
    book = _read_json(run_dir / "book.json")
    polish_report = _read_json(run_dir / "polish-report.json")

    handoff = resolve_phase_a_handoff(run_dir)
    final_markdown = handoff["reading_markdown"]
    final_epub = _select_final_epub(run_dir, manifest)
    if handoff.get("reading_epub") is not None:
        final_epub = handoff["reading_epub"]
    href_validation = manifest.get("files", {}).get("epub_href_validation")
    href_ratio = href_validation.get("resolved_ratio") if isinstance(href_validation, dict) else None
    chapter_count = len(book.get("chapters") or [])
    required_missing = [
        name
        for name, path in [
            ("manifest.json", run_dir / "manifest.json"),
            ("book.json", run_dir / "book.json"),
            ("book.md", run_dir / "book.md"),
            ("chapter-report.json", run_dir / "chapter-report.json"),
        ]
        if not path.exists()
    ]

    translation_mode = manifest.get("translation", {}).get("mode") or (
        "translated" if (run_dir / "translated.md").exists() else "not_requested"
    )
    remaining_polish_candidates = None
    if polish_report:
        remaining_polish_candidates = int(polish_report.get("rejected_count", 0)) + int(
            polish_report.get("unchanged_count", 0)
        )

    status = {
        "schema": "phase_a_status_v2",
        "status": "accepted" if not required_missing and final_markdown is not None else "needs_review",
        "source_path": manifest.get("source_pdf"),
        "run_dir": str(run_dir),
        "source_language": manifest.get("source_language"),
        "target_language": manifest.get("target_language"),
        "translation_mode": translation_mode,
        "book_json": _relative_or_name(run_dir / "book.json", run_dir),
        "source_markdown": _relative_or_name(run_dir / "book.md", run_dir),
        "final_markdown": _relative_or_name(final_markdown, run_dir),
        "final_epub": _relative_or_name(final_epub, run_dir),
        "phase_b_input": {
            "mode": handoff["mode"],
            "content_source": handoff["content_source"],
            "source_language": handoff["source_language"],
            "reading_language": handoff["reading_language"],
            "source_markdown": _relative_or_name(handoff["source_markdown"], run_dir),
            "translation_markdown": _relative_or_name(handoff["translation_markdown"], run_dir),
            "reading_markdown": _relative_or_name(handoff["reading_markdown"], run_dir),
            "review_status": handoff["review_status"],
            "review_version": handoff["review_version"],
            "review_manifest": _relative_or_name(handoff["review_manifest"], run_dir),
        },
        "chapter_count": chapter_count,
        "chapter_id_coverage": _chapter_id_coverage(book),
        "href_resolved_ratio": href_ratio,
        "remaining_polish_candidates": remaining_polish_candidates,
        "missing_required": required_missing,
        "ready_for_phase_b": not required_missing and handoff["reading_markdown"] is not None,
    }
    status_path = run_dir / "phase_a_status.json"
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status_path": str(status_path), "status": status}


def cleanup_run(run_dir: Path, *, dry_run: bool = True, include_caches: bool = True) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    candidates: list[Path] = [
        run_dir / "normalized.md",
        run_dir / "normalized.json",
        run_dir / "reconstructed.md",
    ]
    candidates.extend(run_dir.glob(".DS_Store"))
    candidates.extend((run_dir / "chapters").glob("*.md") if (run_dir / "chapters").exists() else [])
    if (run_dir / "book-images").exists():
        candidates.append(run_dir / "images")
    if include_caches:
        candidates.extend([run_dir / "translation-cache", run_dir / "polish-cache"])

    removed: list[str] = []
    would_remove: list[str] = []
    for path in candidates:
        if not path.exists():
            continue
        rel = _relative_or_name(path, run_dir) or str(path)
        if dry_run:
            would_remove.append(rel)
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(rel)

    empty_chapters_dir = run_dir / "chapters"
    if not dry_run and empty_chapters_dir.exists() and not any(empty_chapters_dir.iterdir()):
        empty_chapters_dir.rmdir()

    report = {
        "schema": "cleanup_report_v1",
        "run_dir": str(run_dir),
        "dry_run": dry_run,
        "include_caches": include_caches,
        "would_remove": would_remove,
        "removed": removed,
    }
    report_path = run_dir / ("cleanup-dry-run.json" if dry_run else "cleanup-report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"report_path": str(report_path), "report": report}
