from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pdf_translator.glossary import (
    glossary_status,
    load_active_glossary_if_present,
)


WORKFLOW_SCHEMA = "phase_a_workflow_v1"
STAGE_AWAITING_GLOSSARY = "awaiting_glossary"
STAGE_GLOSSARY_READY = "glossary_ready"
STAGE_TRANSLATING = "translating"
STAGE_PRE_REVIEW = "pre_review"
STAGE_AWAITING_HUMAN_REVIEW = "awaiting_human_review"
STAGE_COMPLETED = "completed"


class GlossaryNotReadyError(ValueError):
    """Raised when translation is requested before glossary is finalized."""


def _workflow_path(run_dir: Path) -> Path:
    return run_dir / "workflow.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_workflow(run_dir: Path) -> dict[str, Any] | None:
    path = _workflow_path(run_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_workflow(run_dir: Path, *, stage: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "schema": WORKFLOW_SCHEMA,
        "stage": stage,
        "updated_at": _now(),
        **extra,
    }
    existing = load_workflow(run_dir)
    if existing is not None:
        payload.setdefault("created_at", existing.get("created_at", _now()))
    else:
        payload["created_at"] = _now()
    path = _workflow_path(run_dir)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def active_glossary_entries(run_dir: Path) -> list[dict[str, Any]]:
    active = load_active_glossary_if_present(run_dir)
    if not active:
        return []
    return [
        entry
        for entry in active.get("entries", [])
        if entry.get("status") == "active" and str(entry.get("target") or "").strip()
    ]


TRANSLATION_ALLOWED_STAGES = frozenset(
    {
        STAGE_GLOSSARY_READY,
        STAGE_TRANSLATING,
        STAGE_PRE_REVIEW,
        STAGE_AWAITING_HUMAN_REVIEW,
        STAGE_COMPLETED,
    }
)


def glossary_ready_summary(run_dir: Path) -> dict[str, Any]:
    status = glossary_status(run_dir)
    active_entries = active_glossary_entries(run_dir)
    workflow = load_workflow(run_dir)
    stage = (workflow or {}).get("stage")
    return {
        "workflow_stage": stage,
        "candidate_count": status["candidate_count"],
        "active_count": status["active_count"],
        "ready_entries": len(active_entries),
        "is_ready": bool(active_entries)
        and stage in {STAGE_GLOSSARY_READY, *TRANSLATION_ALLOWED_STAGES - {STAGE_GLOSSARY_READY}},
    }


def begin_translation(run_dir: Path) -> dict[str, Any]:
    return write_workflow(run_dir, stage=STAGE_TRANSLATING)


_SUGGESTION_FIELD_KEYS = (
    "target_suggestion",
    "suggestion_confidence",
    "suggestion_note",
    "suggestion_source",
)


def _strip_suggestions_from_candidates(run_dir: Path) -> int:
    from pdf_translator.glossary import GLOSSARY_SCHEMA, _glossary_dir, _now, _write_json

    glossary_dir = _glossary_dir(run_dir)
    candidates_path = glossary_dir / "candidates.json"
    if not candidates_path.is_file():
        return 0
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    cleared_count = 0
    cleared: list[dict[str, Any]] = []
    for candidate in payload.get("candidates", []):
        item = dict(candidate)
        if any(key in item for key in _SUGGESTION_FIELD_KEYS):
            cleared_count += 1
        for key in _SUGGESTION_FIELD_KEYS:
            item.pop(key, None)
        cleared.append(item)
    payload["candidates"] = cleared
    payload["updated_at"] = _now()
    _write_json(candidates_path, payload)
    return cleared_count


def clear_glossary_suggestions(run_dir: Path) -> dict[str, Any]:
    """Clear machine suggestions only; keep adopted/rejected decisions."""
    from pdf_translator.glossary import _glossary_dir

    glossary_dir = _glossary_dir(run_dir)
    cleared_count = _strip_suggestions_from_candidates(run_dir)
    for name in ("suggest-running.json", "suggestions.json"):
        path = glossary_dir / name
        if path.exists():
            path.unlink()
    status = glossary_status(run_dir)
    return {
        "cleared_suggestion_count": cleared_count,
        "candidate_count": status["candidate_count"],
        "active_count": status["active_count"],
        "kept_adoptions": True,
    }


def reset_glossary_review(
    run_dir: Path,
    *,
    clear_suggestions: bool = True,
    clear_policy_annotations: bool = True,
    decided_by: str = "system",
) -> dict[str, Any]:
    """Clear adoption decisions and return workflow to awaiting_glossary."""
    from pdf_translator.glossary import (
        GLOSSARY_SCHEMA,
        _glossary_dir,
        _write_json,
        clear_glossary_policy_round_annotations,
        glossary_status,
    )

    glossary_dir = _glossary_dir(run_dir)
    _write_json(
        glossary_dir / "active.json",
        {"schema": GLOSSARY_SCHEMA, "entries": [], "updated_at": _now()},
    )
    decisions_path = glossary_dir / "decisions.jsonl"
    if decisions_path.exists():
        decisions_path.write_text("", encoding="utf-8")
    for name in ("suggest-running.json", "suggestions.json"):
        path = glossary_dir / name
        if path.exists():
            path.unlink()
    if clear_suggestions:
        _strip_suggestions_from_candidates(run_dir)
    cleared_policy = None
    if clear_policy_annotations:
        cleared_policy = clear_glossary_policy_round_annotations(run_dir)
    write_workflow(
        run_dir,
        stage=STAGE_AWAITING_GLOSSARY,
        glossary_review_reset_at=_now(),
        glossary_review_reset_by=decided_by,
    )
    status = glossary_status(run_dir)
    return {
        "workflow_stage": STAGE_AWAITING_GLOSSARY,
        "candidate_count": status["candidate_count"],
        "active_count": status["active_count"],
        "cleared_suggestions": clear_suggestions,
        "cleared_policy_annotations": bool(cleared_policy),
    }


def mark_glossary_ready(run_dir: Path, *, decided_by: str = "user") -> dict[str, Any]:
    active_entries = active_glossary_entries(run_dir)
    if not active_entries:
        raise GlossaryNotReadyError(
            "Glossary is not ready: add at least one active term with a target translation "
            "via `book-weaver glossary apply` before running translate."
        )
    return write_workflow(
        run_dir,
        stage=STAGE_GLOSSARY_READY,
        active_term_count=len(active_entries),
        decided_by=decided_by,
        glossary_finalized_by_user=True,
    )


def require_glossary_ready(run_dir: Path) -> None:
    summary = glossary_ready_summary(run_dir)
    if summary["ready_entries"] and summary["workflow_stage"] in TRANSLATION_ALLOWED_STAGES:
        return
    if not summary["ready_entries"]:
        raise GlossaryNotReadyError(
            "Translation blocked: no active glossary terms with targets. "
            "Run `book-weaver glossary apply` then `book-weaver glossary ready RUN_DIR`."
        )
    raise GlossaryNotReadyError(
        "Translation blocked: finalize glossary first. "
        "Run `book-weaver glossary ready RUN_DIR` after applying terminology decisions."
    )
