from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pdf_translator.glossary_extraction import (
    _classify_term,
    _count_occurrences,
    _metadata_exclusions,
    extract_candidate_phrases,
    extract_connector_phrases,
    extract_index_phrases,
    score_glossary_candidate,
)
from pdf_translator.glossary_profiles import (
    GLOSSARY_PROFILE_LABELS,
    detect_glossary_profile,
    profile_policy,
    profile_resolution_from_artifacts,
)


GLOSSARY_SCHEMA = "phase_a_glossary_v1"
EXTRACTION_POLICY_SCHEMA = "phase_a_glossary_extraction_v2"
LEGACY_EXTRACTION_POLICY_SCHEMA = "phase_a_glossary_extraction_v1"
DEFAULT_MAX_CANDIDATES = 40


def _glossary_dir(run_dir: Path) -> Path:
    return run_dir / "glossary"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _book_text_corpus(book: dict[str, Any]) -> tuple[str, dict[str, str]]:
    chapter_text: dict[str, str] = {}
    parts: list[str] = []
    for chapter in book.get("chapters", []):
        chapter_id = str(chapter.get("chapter_id") or chapter.get("id") or "unknown")
        markdown = str(chapter.get("markdown") or chapter.get("title") or "")
        chapter_text[chapter_id] = markdown
        parts.append(markdown)
    return "\n\n".join(parts), chapter_text


def _is_index_chapter(chapter: dict[str, Any]) -> bool:
    title = str(chapter.get("title") or chapter.get("chapter_id") or "").lower()
    return any(token in title for token in ("index", "glossary", "notes", "bibliography"))


def _load_extraction_policy(run_dir: Path) -> dict[str, Any] | None:
    path = _glossary_dir(run_dir) / "extraction-policy.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def detect_glossary_profile_for_run(run_dir: Path) -> dict[str, Any]:
    book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))
    corpus, _ = _book_text_corpus(book)
    return detect_glossary_profile(book, corpus=corpus)


def extract_glossary_candidates(
    run_dir: Path,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    profile: str | None = None,
    profile_source: str | None = None,
) -> dict[str, Any]:
    book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))
    corpus, chapter_text = _book_text_corpus(book)
    exclusions = _metadata_exclusions(book)
    existing_policy = _load_extraction_policy(run_dir)

    if profile is None and existing_policy and existing_policy.get("glossary_profile_overridden"):
        profile_id = str(existing_policy["glossary_profile"])
        resolved_source = str(existing_policy.get("glossary_profile_source") or "user")
        overridden = True
        detection = {
            "glossary_profile": profile_id,
            "glossary_profile_label": existing_policy.get("glossary_profile_label")
            or GLOSSARY_PROFILE_LABELS.get(profile_id, profile_id),
            "glossary_profile_source": resolved_source,
            "glossary_profile_confidence": existing_policy.get("glossary_profile_confidence"),
            "glossary_profile_scores": existing_policy.get("glossary_profile_scores", {}),
            "glossary_profile_overridden": True,
            "humanities_subhints": existing_policy.get("humanities_subhints", []),
        }
    elif profile is not None:
        profile_id, resolved_source, overridden = profile_resolution_from_artifacts(
            existing_policy,
            explicit_profile=profile,
        )
        if profile_source:
            resolved_source = profile_source
        auto_detection = detect_glossary_profile(book, corpus=corpus)
        detection = {
            **auto_detection,
            "glossary_profile": profile_id,
            "glossary_profile_label": GLOSSARY_PROFILE_LABELS[profile_id],
            "glossary_profile_source": resolved_source,
            "glossary_profile_overridden": overridden,
        }
        if profile_id == "humanities_history":
            detection["humanities_subhints"] = auto_detection.get("humanities_subhints", [])
        else:
            detection["humanities_subhints"] = []
    else:
        detection = detect_glossary_profile(book, corpus=corpus)
        profile_id = str(detection["glossary_profile"])
        resolved_source = "auto"
        overridden = False

    active_policy = profile_policy(profile_id)

    stats: dict[str, dict[str, Any]] = {}
    for chapter in book.get("chapters", []):
        chapter_id = str(chapter.get("chapter_id") or chapter.get("id") or "unknown")
        markdown = chapter_text.get(chapter_id, "")
        in_index = _is_index_chapter(chapter)
        phrase_sources = list(extract_candidate_phrases(markdown))
        if active_policy.enable_connector_phrases:
            phrase_sources.extend(extract_connector_phrases(markdown))
        if active_policy.enable_index_parse and in_index:
            phrase_sources.extend(extract_index_phrases(markdown))
        for phrase in phrase_sources:
            entry = stats.setdefault(
                phrase,
                {
                    "occurrences": 0,
                    "chapters": set(),
                    "in_index": False,
                },
            )
            entry["occurrences"] += _count_occurrences(markdown, phrase)
            entry["chapters"].add(chapter_id)
            entry["in_index"] = entry["in_index"] or in_index

    # Re-count across full corpus for accuracy (chapter-local counts can undercount).
    for phrase, entry in stats.items():
        entry["occurrences"] = _count_occurrences(corpus, phrase)

    ranked: list[dict[str, Any]] = []
    rejected_count = 0
    for phrase, entry in stats.items():
        score, reasons, rejected = score_glossary_candidate(
            phrase,
            occurrences=int(entry["occurrences"]),
            chapter_count=len(entry["chapters"]),
            exclusions=exclusions,
            in_index=bool(entry["in_index"]),
            policy=active_policy,
        )
        if rejected:
            rejected_count += 1
            continue
        confidence = min(0.98, max(0.35, score / 14.0))
        ranked.append(
            {
                "source": phrase,
                "target": None,
                "type": _classify_term(phrase),
                "status": "candidate",
                "confidence": round(confidence, 3),
                "score": round(score, 2),
                "occurrences": int(entry["occurrences"]),
                "chapter_count": len(entry["chapters"]),
                "reasons": reasons,
                "evidence": sorted(entry["chapters"]),
                "updated_by": "machine",
            }
        )

    ranked.sort(key=lambda item: (-float(item["score"]), -int(item["occurrences"]), item["source"]))
    candidates = ranked[: max(1, max_candidates)]

    policy = {
        "schema": EXTRACTION_POLICY_SCHEMA,
        "generated_at": _now(),
        "max_candidates": max_candidates,
        "profile_policy_version": 1,
        "principles": list(active_policy.principles),
        "stats": {
            "raw_phrases_seen": len(stats),
            "rejected": rejected_count,
            "surfaced": len(candidates),
        },
        "metadata_exclusions": sorted(exclusions),
        **detection,
    }
    if overridden and existing_policy and existing_policy.get("glossary_profile") != profile_id:
        policy["glossary_profile_previous"] = existing_policy.get("glossary_profile")

    payload = {
        "schema": GLOSSARY_SCHEMA,
        "generated_at": _now(),
        "candidates": candidates,
        "policy": policy,
    }
    glossary_dir = _glossary_dir(run_dir)
    _write_json(glossary_dir / "candidates.json", payload)
    _write_json(glossary_dir / "extraction-policy.json", policy)
    active_path = glossary_dir / "active.json"
    if not active_path.exists():
        _write_json(active_path, {"schema": GLOSSARY_SCHEMA, "updated_at": _now(), "entries": []})
    (glossary_dir / "decisions.jsonl").touch(exist_ok=True)

    from pdf_translator.workflow import STAGE_AWAITING_GLOSSARY, load_workflow, write_workflow

    workflow = load_workflow(run_dir)
    stage = (workflow or {}).get("stage") or STAGE_AWAITING_GLOSSARY
    write_workflow(
        run_dir,
        stage=stage,
        glossary_profile=profile_id,
        glossary_profile_label=policy["glossary_profile_label"],
        glossary_profile_source=resolved_source,
        glossary_profile_confidence=policy.get("glossary_profile_confidence"),
        glossary_profile_overridden=overridden,
        humanities_subhints=policy.get("humanities_subhints", []),
    )
    return payload


def load_candidates(run_dir: Path) -> list[dict[str, Any]]:
    path = _glossary_dir(run_dir) / "candidates.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("candidates", []))


def candidate_for_source(run_dir: Path, source: str) -> dict[str, Any] | None:
    for candidate in load_candidates(run_dir):
        if candidate.get("source") == source:
            return candidate
    return None


def load_active_glossary(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "glossary" / "active.json").read_text(encoding="utf-8"))


def load_active_glossary_if_present(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "glossary" / "active.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_active_entries_for_translation(run_dir: Path) -> list[dict[str, Any]]:
    active = load_active_glossary_if_present(run_dir)
    if not active:
        return []
    return [
        entry
        for entry in active.get("entries", [])
        if entry.get("status") == "active" and str(entry.get("target") or "").strip()
    ]


def glossary_manifest_files(run_dir: Path) -> dict[str, str]:
    glossary_dir = _glossary_dir(run_dir)
    files: dict[str, str] = {}
    mapping = {
        "glossary_active": "active.json",
        "glossary_candidates": "candidates.json",
        "glossary_decisions": "decisions.jsonl",
        "glossary_extraction_policy": "extraction-policy.json",
    }
    for key, name in mapping.items():
        path = glossary_dir / name
        if path.exists():
            files[key] = str(path)
    workflow = run_dir / "workflow.json"
    if workflow.exists():
        files["workflow"] = str(workflow)
    return files


def apply_glossary_decision(
    run_dir: Path,
    *,
    source: str,
    target: str | None,
    term_type: str,
    status: str,
    decided_by: str,
) -> dict[str, Any]:
    glossary_dir = _glossary_dir(run_dir)
    active_path = glossary_dir / "active.json"
    active = (
        json.loads(active_path.read_text(encoding="utf-8"))
        if active_path.exists()
        else {"schema": GLOSSARY_SCHEMA, "entries": []}
    )
    candidate = candidate_for_source(run_dir, source)
    entries = [entry for entry in active.get("entries", []) if entry.get("source") != source]
    entry = {
        "source": source,
        "target": target,
        "type": term_type or (candidate or {}).get("type") or "concept",
        "status": status,
        "confidence": 1.0 if decided_by == "user" else float((candidate or {}).get("confidence") or 0.7),
        "score": (candidate or {}).get("score"),
        "occurrences": (candidate or {}).get("occurrences"),
        "chapter_count": (candidate or {}).get("chapter_count"),
        "reasons": (candidate or {}).get("reasons", []),
        "evidence": (candidate or {}).get("evidence", []),
        "updated_by": decided_by,
    }
    if status == "active":
        entries.append(entry)
    active["entries"] = sorted(entries, key=lambda item: item["source"])
    active["updated_at"] = _now()
    _write_json(active_path, active)
    decision = {"event": "glossary_decision", "timestamp": _now(), **entry}
    with (glossary_dir / "decisions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(decision, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def select_glossary_entries_for_text(
    text: str,
    entries: list[dict[str, Any]],
    *,
    chapter_id: str | None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    selected = []
    lowered = text.lower()
    for entry in entries:
        if entry.get("status") != "active":
            continue
        source = str(entry.get("source") or "")
        evidence = set(entry.get("evidence") or [])
        if source.lower() in lowered or (chapter_id is not None and chapter_id in evidence):
            selected.append(entry)
    return selected[:limit]


def glossary_terms_missing_in_translation(
    source_text: str,
    translated_text: str,
    entries: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Return active glossary targets required by source_text but absent from translated_text."""
    missing: list[dict[str, str]] = []
    lowered_source = source_text.lower()
    for entry in entries:
        if entry.get("status") != "active":
            continue
        source_term = str(entry.get("source") or "").strip()
        target_term = str(entry.get("target") or "").strip()
        if not source_term or not target_term:
            continue
        if source_term.lower() not in lowered_source:
            continue
        if target_term not in translated_text:
            missing.append({"source": source_term, "target": target_term})
    return missing


def _active_entry_by_source(run_dir: Path) -> dict[str, dict[str, Any]]:
    active = load_active_glossary_if_present(run_dir)
    if not active:
        return {}
    return {
        str(entry.get("source") or ""): entry
        for entry in active.get("entries", [])
        if entry.get("source")
    }


def _latest_decisions_by_source(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = _glossary_dir(run_dir) / "decisions.jsonl"
    if not path.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        decision = json.loads(line)
        source = str(decision.get("source") or "").strip()
        if source:
            latest[source] = decision
    return latest


def finalize_pending_glossary_entries(run_dir: Path) -> dict[str, Any]:
    """Adopt unresolved glossary candidates when translation starts.

    Terms explicitly rejected stay rejected. Everything else with a Chinese
    suggestion is written to active.json as if the user had confirmed it.
    """
    candidates = load_candidates(run_dir)
    active_by_source = _active_entry_by_source(run_dir)
    latest_decisions = _latest_decisions_by_source(run_dir)
    confirmed: list[str] = []
    skipped: list[str] = []
    for candidate in candidates:
        source = str(candidate.get("source") or "").strip()
        if not source:
            continue
        latest = latest_decisions.get(source)
        if latest and latest.get("status") == "rejected":
            continue
        existing = active_by_source.get(source)
        if existing:
            status = str(existing.get("status") or "")
            if status == "rejected":
                continue
            if status == "active" and str(existing.get("target") or "").strip():
                continue
        target = str(candidate.get("target_suggestion") or "").strip()
        if not target:
            skipped.append(source)
            continue
        apply_glossary_decision(
            run_dir,
            source=source,
            target=target,
            term_type=str(candidate.get("type") or "concept"),
            status="active",
            decided_by="translation_start",
        )
        confirmed.append(source)
        active_by_source[source] = {"source": source, "status": "active", "target": target}
    return {
        "confirmed": confirmed,
        "confirmed_count": len(confirmed),
        "skipped_without_suggestion": skipped,
    }


def glossary_status(run_dir: Path) -> dict[str, Any]:
    candidates_path = _glossary_dir(run_dir) / "candidates.json"
    active = load_active_glossary_if_present(run_dir)
    candidate_count = 0
    if candidates_path.exists():
        candidate_count = len(json.loads(candidates_path.read_text(encoding="utf-8")).get("candidates", []))
    active_entries = active.get("entries", []) if active else []
    active_count = sum(1 for entry in active_entries if entry.get("status") == "active")
    policy = _load_extraction_policy(run_dir) or {}
    return {
        "candidate_count": candidate_count,
        "active_count": active_count,
        "entry_count": len(active_entries),
        "glossary_profile": policy.get("glossary_profile"),
        "glossary_profile_label": policy.get("glossary_profile_label"),
        "glossary_profile_confidence": policy.get("glossary_profile_confidence"),
        "glossary_profile_overridden": policy.get("glossary_profile_overridden", False),
    }
