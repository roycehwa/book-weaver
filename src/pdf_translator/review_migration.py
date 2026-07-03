from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pdf_translator.book_rebuild import (
    apply_canonical_chapter_plan,
    build_book_reconstruction,
)
from pdf_translator.page_integrity import build_page_ledger
from pdf_translator.integrity import build_integrity_ledger
from pdf_translator.pdf_text_repair import repair_book_dict


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate_legacy_review_run(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    normalized_path = Path(
        (manifest.get("files") or {}).get("normalized_json")
        or run_dir / "normalized.json"
    ).expanduser().resolve()
    source_path = Path(str(manifest["source_pdf"])).expanduser().resolve()
    canonical_path = run_dir.parent / "canonical-chapters.json"
    review_state_path = run_dir / "review_state.json"

    review_state_hash = _sha256(review_state_path)
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir = run_dir / "migration-backups" / backup_id
    backup_dir.mkdir(parents=True, exist_ok=False)
    for name in ("book.json", "page-ledger.json", "integrity-ledger.json"):
        source = run_dir / name
        if source.exists():
            shutil.copy2(source, backup_dir / name)
    _atomic_write_json(
        backup_dir / "migration.json",
        {
            "schema": "review_migration_backup_v1",
            "review_state_sha256": review_state_hash,
            "source_pdf": str(source_path),
            "normalized_json": str(normalized_path),
            "canonical_chapters": str(canonical_path) if canonical_path.exists() else None,
        },
    )

    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    book = repair_book_dict(
        build_book_reconstruction(
            normalized,
            source_pdf=source_path,
            images_dir=run_dir / "book-images",
        )
    )
    if canonical_path.exists():
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        book = apply_canonical_chapter_plan(book, canonical)
    ledger = build_page_ledger(book)
    integrity_ledger = build_integrity_ledger(book)

    if _sha256(review_state_path) != review_state_hash:
        raise RuntimeError("review_state.json changed while migration was rebuilding artifacts")
    _atomic_write_json(run_dir / "book.json", book)
    _atomic_write_json(run_dir / "page-ledger.json", ledger)
    _atomic_write_json(run_dir / "integrity-ledger.json", integrity_ledger)
    if _sha256(review_state_path) != review_state_hash:
        raise RuntimeError("review_state.json changed during migration")

    return {
        "migrated": True,
        "backup_id": backup_id,
        "page_ledger": ledger,
        "integrity_ledger": integrity_ledger,
        "review_state_sha256": review_state_hash,
    }
