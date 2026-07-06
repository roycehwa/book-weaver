from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from pdf_translator.pdf_text_repair import repair_book_dict
from pdf_translator.glossary_extraction import (
    _classify_term,
    _count_occurrences,
    _metadata_exclusions,
    extract_candidate_phrases,
    extract_connector_phrases,
    extract_domain_single_words,
    extract_index_phrases,
    extract_quoted_terms,
    score_glossary_candidate,
    canonical_source_key,
    canonical_source_term,
    candidate_integrity_rejection,
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
CANDIDATE_FLOOR = 60
CANDIDATE_CEILING = 200


def compute_max_candidates(book: dict[str, Any]) -> int:
    """Scale glossary surface limit with book size instead of a fixed cap."""
    corpus, _ = _book_text_corpus(book)
    chars = len(corpus)
    chapters = len(book.get("chapters") or [])
    return min(CANDIDATE_CEILING, max(CANDIDATE_FLOOR, chars // 6000 + chapters * 4))


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
    max_candidates: int | None = None,
    profile: str | None = None,
    profile_source: str | None = None,
) -> dict[str, Any]:
    book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))
    book = repair_book_dict(book)
    corpus, chapter_text = _book_text_corpus(book)
    exclusions = _metadata_exclusions(book)
    existing_policy = _load_extraction_policy(run_dir)
    limit = max_candidates if max_candidates is not None else compute_max_candidates(book)

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
        if profile_source == "user":
            overridden = True
            resolved_source = "user"
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
        if active_policy.allow_single_word_domain:
            phrase_sources.extend(
                extract_domain_single_words(markdown, active_policy.single_word_markers)
            )
            phrase_sources.extend(extract_quoted_terms(markdown))
        if active_policy.enable_index_parse and in_index:
            phrase_sources.extend(extract_index_phrases(markdown))
        for phrase in phrase_sources:
            canonical_term = canonical_source_term(phrase)
            canonical_key = canonical_source_key(phrase)
            if not canonical_key:
                continue
            entry = stats.setdefault(
                canonical_key,
                {
                    "source": canonical_term,
                    "variants": set(),
                    "occurrences": 0,
                    "chapters": set(),
                    "body_chapters": set(),
                    "in_index": False,
                },
            )
            entry["variants"].add(phrase)
            entry["occurrences"] += _count_occurrences(markdown, phrase)
            entry["chapters"].add(chapter_id)
            if not in_index:
                entry["body_chapters"].add(chapter_id)
            entry["in_index"] = entry["in_index"] or in_index

    # Re-count across full corpus for accuracy (chapter-local counts can undercount).
    for entry in stats.values():
        entry["occurrences"] = sum(
            _count_occurrences(corpus, variant)
            for variant in entry["variants"]
        )
        canonical_term = str(entry["source"])
        for chapter in book.get("chapters", []):
            if _is_index_chapter(chapter):
                continue
            chapter_id = str(chapter.get("chapter_id") or chapter.get("id") or "unknown")
            markdown = chapter_text.get(chapter_id, "")
            if _count_occurrences(markdown, canonical_term):
                entry["body_chapters"].add(chapter_id)

    ranked: list[dict[str, Any]] = []
    rejected_count = 0
    reference_only_rejected = 0
    integrity_rejected = 0
    for entry in stats.values():
        phrase = str(entry["source"])
        integrity_reason = candidate_integrity_rejection(phrase)
        if integrity_reason is not None:
            rejected_count += 1
            integrity_rejected += 1
            continue
        if not entry["body_chapters"]:
            rejected_count += 1
            reference_only_rejected += 1
            continue
        score, reasons, rejected = score_glossary_candidate(
            phrase,
            occurrences=int(entry["occurrences"]),
            chapter_count=len(entry["chapters"]),
            body_chapter_count=len(entry.get("body_chapters") or set()),
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
                "body_chapter_count": len(entry["body_chapters"]),
                "reasons": reasons,
                "evidence": sorted(entry["chapters"]),
                "updated_by": "machine",
            }
        )

    ranked.sort(key=lambda item: (-float(item["score"]), -int(item["occurrences"]), item["source"]))
    ranked, overlap_suppressed = _suppress_overlapping_candidates(ranked)
    eligible_before_cutoff = len(ranked)
    ranked, quality_cutoff, below_threshold_rejected = _apply_dynamic_quality_cutoff(
        ranked,
        minimum_score=float(active_policy.min_accept_score),
    )
    candidates = ranked[:limit]

    policy = {
        "schema": EXTRACTION_POLICY_SCHEMA,
        "generated_at": _now(),
        "max_candidates": limit,
        "profile_policy_version": 2,
        "principles": list(active_policy.principles),
        "stats": {
            "raw_phrases_seen": len(stats),
            "eligible": len(ranked),
            "eligible_before_cutoff": eligible_before_cutoff,
            "rejected": rejected_count,
            "integrity_rejected": integrity_rejected,
            "reference_only_rejected": reference_only_rejected,
            "overlap_suppressed": overlap_suppressed,
            "below_threshold_rejected": below_threshold_rejected,
            "quality_cutoff": quality_cutoff,
            "surfaced": len(candidates),
            "max_candidates_limit": limit,
            "hard_ceiling_reached": len(ranked) > limit,
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


def _is_contiguous_word_subset(shorter: str, longer: str) -> bool:
    short_words = canonical_source_key(shorter).split()
    long_words = canonical_source_key(longer).split()
    if len(short_words) >= len(long_words):
        return False
    width = len(short_words)
    return any(long_words[index : index + width] == short_words for index in range(len(long_words) - width + 1))


def _suppress_overlapping_candidates(
    ranked: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    suppressed = 0
    for candidate in ranked:
        source = str(candidate["source"])
        occurrences = int(candidate["occurrences"])
        chapters = int(candidate["body_chapter_count"])
        represented = any(
            _is_contiguous_word_subset(source, str(existing["source"]))
            and int(existing["occurrences"]) * 5 >= occurrences * 3
            and int(existing["body_chapter_count"]) >= chapters
            for existing in kept
        )
        if represented:
            suppressed += 1
            continue
        kept.append(candidate)
    return kept, suppressed


def _apply_dynamic_quality_cutoff(
    ranked: list[dict[str, Any]],
    *,
    minimum_score: float,
) -> tuple[list[dict[str, Any]], float, int]:
    if not ranked:
        return [], minimum_score, 0
    if len(ranked) <= CANDIDATE_FLOOR:
        cutoff = minimum_score
    else:
        top_score = float(ranked[0]["score"])
        cutoff = max(minimum_score, 8.0, top_score - 7.0)
    surfaced = [item for item in ranked if float(item["score"]) >= cutoff]
    return surfaced, cutoff, len(ranked) - len(surfaced)


def load_candidates(run_dir: Path) -> list[dict[str, Any]]:
    path = _glossary_dir(run_dir) / "candidates.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("candidates", []))


def candidate_for_source(run_dir: Path, source: str) -> dict[str, Any] | None:
    source_key = canonical_source_key(source)
    for candidate in load_candidates(run_dir):
        if canonical_source_key(str(candidate.get("source") or "")) == source_key:
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
        {
            **entry,
            "source": canonical_source_term(str(entry.get("source") or "")),
        }
        for entry in active.get("entries", [])
        if entry.get("status") == "active" and str(entry.get("target") or "").strip()
    ]


def migrate_glossary_variants(run_dir: Path) -> dict[str, int]:
    glossary_dir = _glossary_dir(run_dir)
    active_path = glossary_dir / "active.json"
    merged_count = 0
    if active_path.exists():
        active = json.loads(active_path.read_text(encoding="utf-8"))
        by_key: dict[str, dict[str, Any]] = {}
        for raw_entry in active.get("entries", []):
            entry = dict(raw_entry)
            key = canonical_source_key(str(entry.get("source") or ""))
            if not key:
                continue
            entry["source"] = canonical_source_term(str(entry.get("source") or ""))
            current = by_key.get(key)
            if current is None:
                by_key[key] = entry
                continue
            merged_count += 1
            current_is_user = current.get("updated_by") == "user"
            entry_is_user = entry.get("updated_by") == "user"
            if entry_is_user and not current_is_user:
                by_key[key] = entry
        active["entries"] = sorted(by_key.values(), key=lambda item: str(item["source"]).casefold())
        active["updated_at"] = _now()
        _write_json(active_path, active)

    candidates_path = glossary_dir / "candidates.json"
    candidate_merged_count = 0
    if candidates_path.exists():
        payload = json.loads(candidates_path.read_text(encoding="utf-8"))
        candidates_by_key: dict[str, dict[str, Any]] = {}
        for raw_candidate in payload.get("candidates", []):
            candidate = dict(raw_candidate)
            key = canonical_source_key(str(candidate.get("source") or ""))
            if not key:
                continue
            candidate["source"] = canonical_source_term(str(candidate.get("source") or ""))
            current = candidates_by_key.get(key)
            if current is None:
                candidates_by_key[key] = candidate
                continue
            candidate_merged_count += 1
            preferred = max(
                (current, candidate),
                key=lambda item: (
                    bool(item.get("target_suggestion")),
                    float(item.get("score") or 0),
                ),
            )
            preferred["evidence"] = sorted(
                set(current.get("evidence") or []) | set(candidate.get("evidence") or [])
            )
            preferred["chapter_count"] = len(preferred["evidence"])
            preferred["occurrences"] = max(
                int(current.get("occurrences") or 0),
                int(candidate.get("occurrences") or 0),
            )
            candidates_by_key[key] = preferred
        payload["candidates"] = sorted(
            candidates_by_key.values(),
            key=lambda item: (-float(item.get("score") or 0), str(item["source"]).casefold()),
        )
        _write_json(candidates_path, payload)

    return {
        "merged_count": merged_count,
        "candidate_merged_count": candidate_merged_count,
    }


POLICY_ROUND_ANNOTATION_KEYS = (
    "sensitive_content_risk",
    "sensitive_content_score",
    "sensitive_content_signals",
    "glossary_suggest_strategy",
    "glossary_suggest_strategy_label",
)


def clear_glossary_policy_round_annotations(run_dir: Path) -> dict[str, Any] | None:
    """Remove conclusions from prior test rounds; keep extraction profile/settings."""
    policy_path = _glossary_dir(run_dir) / "extraction-policy.json"
    if not policy_path.is_file():
        return None
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        return None
    stripped = {key: policy.pop(key) for key in POLICY_ROUND_ANNOTATION_KEYS if key in policy}
    if stripped:
        policy["round_annotations_cleared_at"] = _now()
        _write_json(policy_path, policy)
    return stripped or None


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
    source = canonical_source_term(source)
    source_key = canonical_source_key(source)
    entries = [
        entry
        for entry in active.get("entries", [])
        if canonical_source_key(str(entry.get("source") or "")) != source_key
    ]
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
    text = re.sub(
        r"!?\[([^\]]*)\]\((?:\\.|[^)])*\)",
        lambda match: match.group(1),
        text,
    )
    selected: list[tuple[int, dict[str, Any]]] = []
    occupied: list[tuple[int, int]] = []
    for entry in sorted(
        entries,
        key=lambda item: len(str(item.get("source") or "")),
        reverse=True,
    ):
        if entry.get("status") != "active":
            continue
        source = str(entry.get("source") or "").strip()
        if not source:
            continue
        first_word = source.split(maxsplit=1)[0].casefold()
        if first_word in {
            "after",
            "before",
            "during",
            "since",
            "until",
            "when",
            "while",
        }:
            continue
        matches = list(re.finditer(re.escape(source), text, flags=re.IGNORECASE))
        available = next(
            (
                match.span()
                for match in matches
                if _is_complete_source_term_match(text, match.start(), match.end())
                if not any(
                    match.start() < end and match.end() > start
                    for start, end in occupied
                )
            ),
            None,
        )
        if available is None:
            continue
        occupied.append(available)
        selected.append((available[0], entry))
    selected.sort(key=lambda item: item[0])
    return [entry for _, entry in selected[:limit]]


def _is_complete_source_term_match(text: str, start: int, end: int) -> bool:
    if start > 0 and text[start - 1].isalnum():
        return False
    if end < len(text) and text[end].isalnum():
        return False
    before = text[max(0, start - 40):start]
    after = text[end:end + 40]
    if re.search(r"[A-Z][A-Za-z'’-]*[–—-]\s*$", before):
        return False
    if re.match(r"^\s*(?:[–—-]\s*)?(?:[A-Z][A-Za-z'’-]*|[IVX]+)\b", after):
        return False
    return True


def glossary_terms_missing_in_translation(
    source_text: str,
    translated_text: str,
    entries: list[dict[str, Any]],
    *,
    chapter_id: str | None = None,
) -> list[dict[str, str]]:
    """Return active glossary targets required by source_text but absent from translated_text."""
    missing: list[dict[str, str]] = []
    for entry in select_glossary_entries_for_text(
        source_text,
        entries,
        chapter_id=chapter_id,
    ):
        source_term = str(entry.get("source") or "").strip()
        target_term = str(entry.get("target") or "").strip()
        if not source_term or not target_term:
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


def locked_glossary_sources(run_dir: Path) -> set[str]:
    """Terms that must not receive new machine suggestions."""
    locked: set[str] = set()
    for source, entry in _active_entry_by_source(run_dir).items():
        status = str(entry.get("status") or "")
        if status == "rejected":
            locked.add(source)
            continue
        if status == "active" and str(entry.get("target") or "").strip():
            locked.add(source)
    latest = _latest_decisions_by_source(run_dir)
    for source, decision in latest.items():
        if decision.get("status") == "rejected":
            locked.add(source)
    return locked


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
