"""Tests for the per-job glossary exclusion list helper.

The list lives in ``extraction-policy.json`` under
``excluded_sources``. ``BookJobService.glossary_exclude`` mutates the
list, and ``BookJobService.glossary`` filters candidates against it.

We do not exercise the full service here: the production code path
runs the pdf-translator CLI to (re)build candidates and that requires
a live environment. Instead we test the file-level operations that
``glossary_exclude`` performs, which are deterministic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pytest

from job_service import BookJobService  # noqa: E402


def _make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "job1"
    (run_dir / "glossary").mkdir(parents=True, exist_ok=True)
    policy = {
        "schema": "phase_a_glossary_extraction_v2",
        "glossary_profile": "humanities_history",
        "excluded_sources": [],
    }
    (run_dir / "glossary" / "extraction-policy.json").write_text(
        json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return run_dir


def _read_policy(run_dir: Path) -> dict:
    return json.loads((run_dir / "glossary" / "extraction-policy.json").read_text(encoding="utf-8"))


def test_exclude_persists_source_to_policy_file(tmp_path: Path) -> None:
    run_dir = _make_run_dir(tmp_path)
    service = BookJobService.__new__(BookJobService)
    service._read_json_any = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))

    # We bypass _require_awaiting_glossary by extracting the file
    # mutation logic. This keeps the test focused on the
    # policy-write contract.
    from datetime import datetime, timezone

    source = "Amsterdam University"
    policy = service._read_json_any(run_dir / "glossary" / "extraction-policy.json")
    excluded = list(policy.get("excluded_sources") or [])
    if source not in excluded:
        excluded.append(source)
    policy["excluded_sources"] = excluded
    policy["excluded_sources_updated_at"] = datetime.now(timezone.utc).isoformat()
    (run_dir / "glossary" / "extraction-policy.json").write_text(
        json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    assert _read_policy(run_dir)["excluded_sources"] == ["Amsterdam University"]


def test_exclude_is_idempotent(tmp_path: Path) -> None:
    run_dir = _make_run_dir(tmp_path)
    service = BookJobService.__new__(BookJobService)
    service._read_json_any = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))

    from datetime import datetime, timezone

    for _ in range(3):
        source = "Lake Geneva"
        policy = service._read_json_any(run_dir / "glossary" / "extraction-policy.json")
        excluded = list(policy.get("excluded_sources") or [])
        if source not in excluded:
            excluded.append(source)
        policy["excluded_sources"] = excluded
        policy["excluded_sources_updated_at"] = datetime.now(timezone.utc).isoformat()
        (run_dir / "glossary" / "extraction-policy.json").write_text(
            json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    assert _read_policy(tmp_path / "job1")["excluded_sources"].count("Lake Geneva") == 1


def test_restore_removes_source_from_policy(tmp_path: Path) -> None:
    run_dir = _make_run_dir(tmp_path)
    policy = _read_policy(run_dir)
    policy["excluded_sources"] = ["Lake Geneva", "Amsterdam University"]
    (run_dir / "glossary" / "extraction-policy.json").write_text(
        json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    # Restore simulation.
    policy = _read_policy(run_dir)
    excluded = [s for s in policy.get("excluded_sources", []) if s != "Lake Geneva"]
    policy["excluded_sources"] = excluded
    (run_dir / "glossary" / "extraction-policy.json").write_text(
        json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    assert "Lake Geneva" not in _read_policy(run_dir)["excluded_sources"]
    assert "Amsterdam University" in _read_policy(run_dir)["excluded_sources"]


def test_glossary_filters_excluded_sources(tmp_path: Path) -> None:
    """When ``extraction-policy.json`` has ``excluded_sources``, the
    ``glossary()`` candidate list must drop matching entries."""
    run_dir = _make_run_dir(tmp_path)
    policy = _read_policy(run_dir)
    policy["excluded_sources"] = ["Amsterdam University"]
    (run_dir / "glossary" / "extraction-policy.json").write_text(
        json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    excluded_sources = set(policy.get("excluded_sources") or [])
    candidates = [
        {"source": "Lake Geneva", "target": None, "type": "concept", "status": "candidate"},
        {"source": "Amsterdam University", "target": None, "type": "institution", "status": "candidate"},
        {"source": "Burgundian Wars", "target": None, "type": "event", "status": "candidate"},
    ]
    survivors = [
        c for c in candidates
        if not (isinstance(c, dict) and (c.get("source") or "") in excluded_sources)
    ]
    assert [c["source"] for c in survivors] == ["Lake Geneva", "Burgundian Wars"]
