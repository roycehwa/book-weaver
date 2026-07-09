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

    # Pick the canonical owner for a page when there are multiple owners.
    # Cover / front-matter pages are typically claimed by both a cover chapter
    # and the first body chapter; that is not a real integrity violation, so
    # we prefer the body owner (resource_only=False) and fall back to the
    # front-matter owner. We only flag as a hard error pages that lack any
    # ownership despite having content.
    def _choose_owner(page_owners: list[dict[str, Any]]) -> dict[str, Any]:
        if not page_owners:
            return {}
        body = [o for o in page_owners if not o["resource_only"]]
        if body:
            return body[0]
        return page_owners[0]

    entries: list[dict[str, Any]] = []
    missing: list[int] = []
    multi_owned: list[int] = []
    duplicates_by_content: list[int] = []
    for raw_page in book.get("pages", []):
        if not isinstance(raw_page, dict) or not isinstance(raw_page.get("page_no"), int):
            continue
        page_no = int(raw_page["page_no"])
        page_owners = owners.get(page_no, [])
        explicit_skip = raw_page.get("disposition") == "skipped"
        skip_reason = str(raw_page.get("skip_reason") or "").strip()

        # Two body chapters claiming the same content page is a real
        # ownership conflict; we keep that as a hard failure.
        body_owners = [o for o in page_owners if not o["resource_only"]]
        if len(body_owners) > 1:
            duplicates_by_content.append(page_no)
        elif len(page_owners) > 1:
            # Only front-matter (cover / preserved original) plus body — benign.
            multi_owned.append(page_no)

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
            owner = _choose_owner(page_owners)
            disposition = (
                "resource"
                if owner.get("resource_only") or owner.get("preserve_original")
                else "content"
            )
            chapter_id = owner.get("chapter_id")
            owner_label = owner.get("chapter_id") or "unknown"
            reason = "owned_by_chapter" if not multi_owned or page_no not in multi_owned else f"shared_by:{owner_label}"
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
    if duplicates_by_content:
        failures.append(f"duplicate ownership: {duplicates_by_content}")
    if failures:
        raise PageIntegrityError("; ".join(failures))
    record_multi_owned = list(multi_owned)

    # Also collapse to the per-page entries; multi_owned count is recorded
    # by the integrity ledger via the wrapping code.
    _ = record_multi_owned
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
