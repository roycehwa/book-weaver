from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA = "segment_order_v1"
REPORT_SCHEMA = "segment_conservation_report_v1"


def translatable_segment_ids(segment_plan: list[dict[str, Any]]) -> list[str]:
    ordered: list[str] = []
    for segment in segment_plan:
        if not isinstance(segment, dict):
            continue
        if not bool(segment.get("translate", True)):
            continue
        role = str(segment.get("role") or "prose")
        if role in {"figure", "table"}:
            continue
        segment_id = str(segment.get("segment_id") or "").strip()
        if segment_id:
            ordered.append(segment_id)
    return ordered


def write_segment_order_ledger(run_dir: Path, segment_plan: dict[str, Any]) -> Path:
    segments = segment_plan.get("segments") if isinstance(segment_plan, dict) else []
    segments = segments if isinstance(segments, list) else []
    payload = {
        "schema": SCHEMA,
        "segment_count": len(segments),
        "translatable_segment_count": len(translatable_segment_ids(segments)),
        "segment_ids": [
            str(segment.get("segment_id") or "")
            for segment in segments
            if isinstance(segment, dict) and str(segment.get("segment_id") or "").strip()
        ],
        "translatable_segment_ids": translatable_segment_ids(segments),
    }
    path = run_dir / "segment-order.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_segment_order_ledger(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "segment-order.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def verify_segment_processing_order(
    *,
    expected_ids: list[str],
    processed_ids: list[str],
) -> list[str]:
    failures: list[str] = []
    if len(expected_ids) != len(processed_ids):
        failures.append(
            f"segment_count_mismatch expected={len(expected_ids)} actual={len(processed_ids)}"
        )
    for index, expected_id in enumerate(expected_ids):
        if index >= len(processed_ids):
            failures.append(f"missing_segment_at_{index}: {expected_id}")
            continue
        actual_id = processed_ids[index]
        if actual_id != expected_id:
            failures.append(
                f"order_mismatch_at_{index}: expected={expected_id} actual={actual_id}"
            )
    for extra_id in processed_ids[len(expected_ids) :]:
        failures.append(f"unexpected_segment: {extra_id}")
    return failures


def write_segment_conservation_report(run_dir: Path, *, failures: list[str]) -> Path:
    payload = {
        "schema": REPORT_SCHEMA,
        "ok": not failures,
        "failures": failures,
    }
    path = run_dir / "segment-conservation.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
