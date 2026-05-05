from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from pdf_translator.guardrails import (
    DEFAULT_INGEST_TIMEOUT_SECONDS,
    IngestGuardrailError,
    ingest_pdf_guarded,
)
from pdf_translator.profile import build_document_profile


def load_validation_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        cases = payload
    elif isinstance(payload, dict) and isinstance(payload.get("cases"), list):
        cases = payload["cases"]
    else:
        raise ValueError("Validation manifest must be a list or an object with a 'cases' list.")

    normalized_cases: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each validation case must be an object.")
        if "source_pdf" not in case or "mode" not in case:
            raise ValueError("Each validation case must include 'source_pdf' and 'mode'.")
        mode = case["mode"]
        if mode == "articles":
            raise ValueError(
                "Validation mode 'articles' is no longer supported; this project validates book/magazine "
                "profiles only. Use mode 'profile'."
            )
        if mode != "profile":
            raise ValueError(f"Unsupported validation mode: {mode!r}. Supported: 'profile'.")
        normalized_cases.append(case)
    return normalized_cases


def _profile_artifact_path(source_pdf: Path, output_dir: Path) -> Path:
    return output_dir / source_pdf.stem / "profile.json"


def _profile_metrics(result: dict[str, Any]) -> dict[str, Any]:
    total_pages = result["total_pages"]
    actions = result["actions"]
    usable_pages = actions["accept"] + actions["assist"]
    usable_pct = usable_pages / total_pages if total_pages else 0.0
    reject_pct = actions["reject_structure"] / total_pages if total_pages else 0.0
    return {
        "total_pages": total_pages,
        "document_action": result["document_action"],
        "accept": actions["accept"],
        "assist": actions["assist"],
        "skip_content": actions["skip_content"],
        "reject_structure": actions["reject_structure"],
        "usable_pct": round(usable_pct, 3),
        "reject_pct": round(reject_pct, 3),
    }


def _run_profile_case(
    *,
    source_pdf: Path,
    output_dir: Path,
    profile_name: str,
    reuse_existing: bool,
    timeout_seconds: int | None,
    max_file_size_mb: float | None,
    max_page_count: int | None,
) -> tuple[dict[str, Any], Path]:
    artifact_path = _profile_artifact_path(source_pdf, output_dir)
    if reuse_existing and artifact_path.exists():
        return json.loads(artifact_path.read_text(encoding="utf-8")), artifact_path

    normalized, preflight = ingest_pdf_guarded(
        source_pdf,
        profile_name=profile_name,
        timeout_seconds=timeout_seconds,
        max_file_size_mb=max_file_size_mb,
        max_page_count=max_page_count,
    )
    result = build_document_profile(source_pdf, normalized.structured, profile_name=profile_name)
    result["preflight"] = preflight.as_dict()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, artifact_path


def run_validation_case(
    case: dict[str, Any],
    output_dir: Path,
    reuse_existing: bool = True,
    *,
    ingest_timeout_seconds: int | None = DEFAULT_INGEST_TIMEOUT_SECONDS,
    max_file_size_mb: float | None = None,
    max_page_count: int | None = None,
) -> dict[str, Any]:
    source_pdf = Path(case["source_pdf"]).expanduser().resolve()
    mode = case["mode"]
    name = case.get("name", source_pdf.stem)
    profile_name = case.get("profile", "auto")
    timeout_seconds = case.get("ingest_timeout_seconds", ingest_timeout_seconds)
    case_max_file_size_mb = case.get("max_file_size_mb", max_file_size_mb)
    case_max_page_count = case.get("max_page_count", max_page_count)

    try:
        if mode == "profile":
            result, artifact_path = _run_profile_case(
                source_pdf=source_pdf,
                output_dir=output_dir,
                profile_name=profile_name,
                reuse_existing=reuse_existing,
                timeout_seconds=timeout_seconds,
                max_file_size_mb=case_max_file_size_mb,
                max_page_count=case_max_page_count,
            )
            metrics = _profile_metrics(result)
            passed = metrics["document_action"] != "reject"
            summary: dict[str, Any] = {
                "name": name,
                "mode": mode,
                "profile": result["profile"],
                "source_pdf": str(source_pdf),
                "artifact_path": str(artifact_path),
                "passed": passed,
                "metrics": metrics,
            }
            if "preflight" in result:
                summary["preflight"] = result["preflight"]
            return summary

        raise ValueError(f"Unsupported validation mode: {mode}")
    except IngestGuardrailError as exc:
        summary = {
            "name": name,
            "mode": mode,
            "profile": profile_name,
            "source_pdf": str(source_pdf),
            "artifact_path": str(_profile_artifact_path(source_pdf, output_dir)),
            "passed": False,
            "failure": {
                "type": exc.failure_type,
                "message": str(exc),
            },
        }
        if exc.preflight is not None:
            summary["preflight"] = exc.preflight.as_dict()
        return summary
    except Exception as exc:
        return {
            "name": name,
            "mode": mode,
            "profile": profile_name,
            "source_pdf": str(source_pdf),
            "artifact_path": str(_profile_artifact_path(source_pdf, output_dir)),
            "passed": False,
            "failure": {
                "type": "unexpected_error",
                "message": str(exc),
            },
        }


def run_validation_manifest(
    manifest_path: Path,
    output_dir: Path,
    reuse_existing: bool = True,
    *,
    ingest_timeout_seconds: int | None = DEFAULT_INGEST_TIMEOUT_SECONDS,
    max_file_size_mb: float | None = None,
    max_page_count: int | None = None,
) -> dict[str, Any]:
    cases = load_validation_manifest(manifest_path)
    case_reports = run_validation_cases(
        cases,
        output_dir=output_dir,
        reuse_existing=reuse_existing,
        ingest_timeout_seconds=ingest_timeout_seconds,
        max_file_size_mb=max_file_size_mb,
        max_page_count=max_page_count,
    )
    passed_cases = sum(1 for report in case_reports if report["passed"])
    mode_counter = Counter(report["mode"] for report in case_reports)
    profile_counter = Counter(report["profile"] for report in case_reports)
    failure_counter = Counter(
        report["failure"]["type"] for report in case_reports if not report["passed"] and "failure" in report
    )
    pass_rate = passed_cases / len(case_reports) if case_reports else 0.0

    return {
        "manifest_path": str(manifest_path),
        "total_cases": len(case_reports),
        "passed_cases": passed_cases,
        "failed_cases": len(case_reports) - passed_cases,
        "pass_rate": round(pass_rate, 3),
        "by_mode": dict(mode_counter),
        "by_profile": dict(profile_counter),
        "failure_types": dict(failure_counter),
        "cases": case_reports,
    }


def run_validation_cases(
    cases: list[dict[str, Any]],
    *,
    output_dir: Path,
    reuse_existing: bool = True,
    ingest_timeout_seconds: int | None = DEFAULT_INGEST_TIMEOUT_SECONDS,
    max_file_size_mb: float | None = None,
    max_page_count: int | None = None,
) -> list[dict[str, Any]]:
    return [
        run_validation_case(
            case,
            output_dir=output_dir,
            reuse_existing=reuse_existing,
            ingest_timeout_seconds=ingest_timeout_seconds,
            max_file_size_mb=max_file_size_mb,
            max_page_count=max_page_count,
        )
        for case in cases
    ]


def write_validation_report(report: dict[str, Any], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
