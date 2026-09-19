"""Notes/endnotes content-policy dependency analysis (format-neutral).

Uses explicit link and DOM provenance from the reconstructed book only.
Does not infer dependencies from numbered-note heuristics.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "bookweaver_content_policy_dependencies_v1"

_NOTES_ENDNOTES_TITLE = re.compile(
    r"^(?:notes?|end\s?notes?|notes and references|bibliographical notes|biographical notes|"
    r"注释|註釋|尾注)$",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_chapter_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", str(title or "")).strip()
    text = re.sub(r"^(?:\d+|[IVXLCDM]+)[.、:：\s]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[.:：。]+$", "", text).strip()
    return text


def is_notes_endnotes_chapter(chapter: dict[str, Any]) -> bool:
    return bool(_NOTES_ENDNOTES_TITLE.match(normalize_chapter_title(str(chapter.get("title") or ""))))


def _chapter_id(chapter: dict[str, Any], fallback_index: int) -> str:
    value = str(chapter.get("chapter_id") or chapter.get("id") or "").strip()
    return value or f"chapter-{fallback_index:03d}"


def _normalize_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    if not value:
        return ""
    normalized = posixpath.normpath(value)
    return normalized.removeprefix("./")


def _split_link_target(target: str) -> tuple[str, str | None]:
    normalized = _normalize_path(target)
    if "#" not in normalized:
        return normalized, None
    path_part, fragment = normalized.split("#", 1)
    return path_part, fragment or None


def _chapter_resource_paths(chapter: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    internal = _normalize_path(str(chapter.get("source_internal_path") or ""))
    if internal:
        paths.add(internal)
    for unit in chapter.get("dom_units") or []:
        if not isinstance(unit, dict):
            continue
        resource = _normalize_path(str(unit.get("resource_path") or ""))
        if resource:
            paths.add(resource)
    return paths


def _chapter_element_ids(chapter: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for unit in chapter.get("dom_units") or []:
        if not isinstance(unit, dict):
            continue
        element_id = str(unit.get("element_id") or "").strip()
        if element_id:
            ids.add(element_id)
    return ids


def _reading_unit_links_by_chapter(
    book: dict[str, Any],
) -> dict[str, list[tuple[str, str | None, str | None]]]:
    try:
        from pdf_translator.reading_units import build_reading_units

        source_path = Path("book.epub")
        metadata = book.get("metadata") if isinstance(book.get("metadata"), dict) else {}
        chapter_source = str(metadata.get("chapter_source") or "")
        if "pdf" in chapter_source:
            source_path = Path("book.pdf")
        payload = build_reading_units(book, source_path=source_path, translation_authority=False)
    except Exception:
        return {}

    grouped: dict[str, list[tuple[str, str | None, str | None]]] = {}
    for unit in payload.get("units") or []:
        if not isinstance(unit, dict):
            continue
        chapter_key = str(unit.get("chapter_id") or "")
        if not chapter_key:
            continue
        unit_id = str(unit.get("unit_id") or "").strip() or None
        for span in unit.get("provenance") or []:
            if not isinstance(span, dict):
                continue
            resource_path = _normalize_path(str(span.get("resource_path") or "")) or None
            for target in span.get("link_targets") or []:
                grouped.setdefault(chapter_key, []).append((str(target), unit_id, resource_path))
    return grouped


def _iter_link_targets_from_chapter(
    chapter: dict[str, Any],
    book: dict[str, Any],
    *,
    reading_unit_links: dict[str, list[tuple[str, str | None, str | None]]],
) -> Iterable[tuple[str, str | None, str | None]]:
    chapter_pages = {
        int(page)
        for page in chapter.get("source_pages") or []
        if isinstance(page, int) or (isinstance(page, str) and str(page).isdigit())
    }
    seen: set[tuple[str, str | None, str | None]] = set()
    chapter_key = _chapter_id(chapter, 1)

    def emit(
        target: str,
        location: str | None,
        source_resource_path: str | None,
    ) -> Iterable[tuple[str, str | None, str | None]]:
        cleaned = str(target or "").strip()
        if not cleaned:
            return
        scheme = cleaned.split(":", 1)[0].lower() if ":" in cleaned else ""
        if scheme in {"http", "https", "mailto", "ftp", "tel", "javascript"}:
            return
        source_resource = _normalize_path(source_resource_path or "") or None
        key = (_normalize_path(cleaned), location, source_resource)
        if key in seen:
            return
        seen.add(key)
        yield cleaned, location, source_resource

    for unit in chapter.get("dom_units") or []:
        if not isinstance(unit, dict):
            continue
        location = str(unit.get("dom_path") or unit.get("unit_id") or "").strip() or None
        resource_path = _normalize_path(str(unit.get("resource_path") or "")) or None
        for target in unit.get("link_targets") or []:
            yield from emit(str(target), location, resource_path)

    for continuation in book.get("logical_continuations") or []:
        if not isinstance(continuation, dict):
            continue
        from_page = int(continuation.get("from_page") or 0)
        to_page = int(continuation.get("to_page") or 0)
        if from_page not in chapter_pages and to_page not in chapter_pages:
            continue
        for field, resource_field in (
            ("left_link_targets", "left_resource_path"),
            ("right_link_targets", "right_resource_path"),
        ):
            for target in continuation.get(field) or []:
                yield from emit(str(target), field, str(continuation.get(resource_field) or ""))

    for target, location, resource_path in reading_unit_links.get(chapter_key, []):
        yield from emit(target, location, resource_path)


def _resolve_notes_target(
    link_target: str,
    *,
    source_resource_path: str | None,
    notes_by_id: dict[str, dict[str, Any]],
    resource_to_chapters: dict[str, set[str]],
) -> str | None:
    path_part, fragment = _split_link_target(link_target)
    source_resource = _normalize_path(source_resource_path or "")
    candidate_paths: list[str] = []
    if path_part:
        direct = _normalize_path(path_part)
        if direct:
            candidate_paths.append(direct)
        if source_resource:
            relative = _normalize_path(posixpath.join(posixpath.dirname(source_resource), path_part))
            if relative and relative not in candidate_paths:
                candidate_paths.append(relative)
    elif source_resource:
        candidate_paths.append(source_resource)
    else:
        return None
    matches: list[str] = []
    for candidate_path in candidate_paths:
        for chapter_id in sorted(resource_to_chapters.get(candidate_path, set())):
            if chapter_id not in notes_by_id:
                continue
            if fragment:
                element_ids = _chapter_element_ids(notes_by_id[chapter_id])
                if element_ids and fragment not in element_ids:
                    continue
            matches.append(chapter_id)
    unique = sorted(set(matches))
    return unique[0] if len(unique) == 1 else None


def collect_content_policy_dependency_evidence(book: dict[str, Any]) -> list[dict[str, Any]]:
    chapters = [ch for ch in book.get("chapters") or [] if isinstance(ch, dict)]
    notes_by_id: dict[str, dict[str, Any]] = {}
    resource_to_chapters: dict[str, set[str]] = {}
    for index, chapter in enumerate(chapters, start=1):
        chapter_id = _chapter_id(chapter, index)
        if is_notes_endnotes_chapter(chapter):
            notes_by_id[chapter_id] = chapter
        for resource in _chapter_resource_paths(chapter):
            resource_to_chapters.setdefault(resource, set()).add(chapter_id)

    reading_unit_links = _reading_unit_links_by_chapter(book)
    evidence: list[dict[str, Any]] = []
    seen_dependency_ids: set[str] = set()
    for index, chapter in enumerate(chapters, start=1):
        source_id = _chapter_id(chapter, index)
        for link_target, location, source_resource_path in _iter_link_targets_from_chapter(
            chapter,
            book,
            reading_unit_links=reading_unit_links,
        ):
            target_id = _resolve_notes_target(
                link_target,
                source_resource_path=source_resource_path,
                notes_by_id=notes_by_id,
                resource_to_chapters=resource_to_chapters,
            )
            if not target_id or target_id == source_id:
                continue
            dependency_id = _stable_dependency_id(source_id, target_id, link_target)
            if dependency_id in seen_dependency_ids:
                continue
            seen_dependency_ids.add(dependency_id)
            evidence.append(
                {
                    "source_chapter_id": source_id,
                    "source_location": location,
                    "source_resource_path": source_resource_path,
                    "target_chapter_id": target_id,
                    "link_evidence": link_target,
                    "dependency_id": dependency_id,
                }
            )
    return evidence


def _stable_dependency_id(source_chapter_id: str, target_chapter_id: str, link_evidence: str) -> str:
    digest = hashlib.sha256(
        f"{source_chapter_id}\0{target_chapter_id}\0{link_evidence}".encode("utf-8")
    ).hexdigest()[:16]
    return f"cpd-{digest}"


def _resolved_policy(chapter: dict[str, Any]) -> str:
    policy = str(chapter.get("content_policy") or "auto").strip()
    if policy not in {"auto", "translate", "preserve", "exclude"}:
        return "translate"
    return policy


def _chapter_title_by_id(chapters: list[dict[str, Any]]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for index, chapter in enumerate(chapters, start=1):
        if not isinstance(chapter, dict):
            continue
        titles[_chapter_id(chapter, index)] = str(chapter.get("title") or "")
    return titles


def findings_from_evidence(
    evidence: list[dict[str, Any]],
    canonical_chapters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    policies = {_chapter_id(chapter, index): _resolved_policy(chapter) for index, chapter in enumerate(canonical_chapters, 1) if isinstance(chapter, dict)}
    titles = _chapter_title_by_id(canonical_chapters)
    findings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in evidence:
        source_id = str(item.get("source_chapter_id") or "")
        target_id = str(item.get("target_chapter_id") or "")
        link_evidence = str(item.get("link_evidence") or "")
        if not source_id or not target_id or not link_evidence:
            continue
        if policies.get(source_id) == "exclude":
            continue
        if policies.get(target_id) != "exclude":
            continue
        dependency_id = str(item.get("dependency_id") or _stable_dependency_id(source_id, target_id, link_evidence))
        if dependency_id in seen_ids:
            continue
        seen_ids.add(dependency_id)
        target_title = titles.get(target_id, target_id)
        source_title = titles.get(source_id, source_id)
        findings.append(
            {
                "dependency_id": dependency_id,
                "source_chapter_id": source_id,
                "source_chapter_title": source_title,
                "source_location": item.get("source_location"),
                "target_chapter_id": target_id,
                "target_chapter_title": target_title,
                "link_evidence": link_evidence,
                "message": (
                    f"正文章节「{source_title}」通过链接 {link_evidence} 引用尾注章节「{target_title}」。"
                    "略过该尾注会使引用断裂，建议保留原文。"
                ),
                "recommended_policy": "preserve",
            }
        )
    return findings


def analyze_content_policy_dependencies(
    book: dict[str, Any],
    canonical_chapters: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence = collect_content_policy_dependency_evidence(book)
    findings = findings_from_evidence(evidence, canonical_chapters)
    return {
        "schema": SCHEMA,
        "evidence": evidence,
        "findings": findings,
    }


def validate_acknowledged_dependencies(
    findings: list[dict[str, Any]],
    acknowledged_dependency_ids: list[str] | None,
) -> list[dict[str, Any]]:
    required = {str(item["dependency_id"]) for item in findings if item.get("dependency_id")}
    provided = {str(value) for value in (acknowledged_dependency_ids or []) if str(value).strip()}
    if provided - required:
        raise ValueError("unknown_dependency_acknowledgement")
    missing = [item for item in findings if str(item.get("dependency_id")) not in provided]
    return missing


def write_content_policy_dependencies_artifact(
    path: Path,
    *,
    findings: list[dict[str, Any]],
    acknowledged_dependency_ids: list[str],
) -> dict[str, Any]:
    payload = {
        "schema": SCHEMA,
        "created_at": _utc_now(),
        "acknowledged_dependency_ids": sorted(acknowledged_dependency_ids),
        "findings": findings,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return payload
