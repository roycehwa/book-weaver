from __future__ import annotations

from collections import defaultdict
from typing import Any


class PageIntegrityError(ValueError):
    """Raised when required source pages do not have exact chapter ownership."""


def build_page_ledger(book: dict[str, Any]) -> dict[str, Any]:
    owners: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, raw_chapter in enumerate(book.get("chapters", []), 1):
        if not isinstance(raw_chapter, dict):
            continue
        owner = {
            "chapter_id": str(raw_chapter.get("chapter_id") or f"chapter-{index:03d}"),
            "resource_only": bool(raw_chapter.get("resource_only")),
            "preserve_original": bool(raw_chapter.get("preserve_original")),
        }
        for raw_page_no in raw_chapter.get("source_pages", []):
            if isinstance(raw_page_no, int):
                owners[raw_page_no].append(owner)

    entries: list[dict[str, Any]] = []
    missing: list[int] = []
    duplicate: list[int] = []
    for raw_page in book.get("pages", []):
        if not isinstance(raw_page, dict) or not isinstance(raw_page.get("page_no"), int):
            continue
        page_no = int(raw_page["page_no"])
        page_owners = owners.get(page_no, [])
        explicit_skip = raw_page.get("disposition") == "skipped"
        skip_reason = str(raw_page.get("skip_reason") or "").strip()

        if len(page_owners) > 1:
            duplicate.append(page_no)
        if (
            raw_page.get("has_content") is True
            and not page_owners
            and not (explicit_skip and skip_reason)
        ):
            missing.append(page_no)

        if explicit_skip and skip_reason:
            disposition = "skipped"
            chapter_id = None
            reason = skip_reason
        elif page_owners:
            owner = page_owners[0]
            disposition = (
                "resource"
                if owner["resource_only"] or owner["preserve_original"]
                else "content"
            )
            chapter_id = owner["chapter_id"]
            reason = "owned_by_chapter"
        else:
            disposition = "blank"
            chapter_id = None
            reason = "no_extracted_content"
        entries.append(
            {
                "page_no": page_no,
                "disposition": disposition,
                "chapter_id": chapter_id,
                "reason": reason,
            }
        )

    failures: list[str] = []
    if missing:
        failures.append(f"missing ownership: {missing}")
    if duplicate:
        failures.append(f"duplicate ownership: {duplicate}")
    if failures:
        raise PageIntegrityError("; ".join(failures))

    return {
        "schema": "page_ledger_v1",
        "pages": entries,
        "summary": {
            "total_pages": len(entries),
            "content_pages": sum(
                entry["disposition"] == "content" for entry in entries
            ),
            "resource_pages": sum(
                entry["disposition"] == "resource" for entry in entries
            ),
            "blank_pages": sum(entry["disposition"] == "blank" for entry in entries),
            "skipped_pages": sum(
                entry["disposition"] == "skipped" for entry in entries
            ),
            "required_pages": sum(
                entry["disposition"] in {"content", "resource"} for entry in entries
            ),
            "required_coverage_ratio": 1.0,
        },
    }
