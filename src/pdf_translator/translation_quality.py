from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import markdown as markdown_renderer
from bs4 import BeautifulSoup

from pdf_translator.chunking import markdown_block_structure
from pdf_translator.pdf_text_repair import scan_ingest_quality
from pdf_translator.polish import POLISH_PROMPT_VERSION
from pdf_translator.reading_units import validate_reading_units
from pdf_translator.source_workspace import atomic_json, glossary_fingerprint
from pdf_translator.translate import TRANSLATION_PROMPT_VERSION
from pdf_translator.zh_markdown_cleanup import RULES_VERSION, TRANSLATION_CLEANUP_REPORT_FILENAME

REPORT_SCHEMA = "bookweaver_translation_quality_report_v1"
INDEX_SCHEMA = "bookweaver_translation_quality_index_v1"

SOURCE_QUALITY_REPORT = "source-quality-report.json"
RAW_TRANSLATION_QUALITY_REPORT = "raw-translation-quality-report.json"
POLISHED_OUTPUT_QUALITY_REPORT = "polished-output-quality-report.json"
TRANSLATION_QUALITY_INDEX = "translation-quality-index.json"

QualityStage = Literal["source", "raw_translation", "polished_output"]
QualitySeverity = Literal["blocking", "review"]
QualityStatus = Literal["passed", "review", "blocked", "not_applicable"]

_REVIEW_ISSUE_TO_CODE: dict[str, str] = {
    "missing_translation": "missing_translation",
    "missing_content": "missing_content",
    "untranslated": "untranslated",
    "mixed_english": "mixed_english",
    "possibly_incomplete": "possibly_incomplete",
    "translation_failed_open": "translation_failed_open",
    "glossary_drift": "glossary_drift",
    "polish_unavailable": "polish_unavailable",
    "suspect_ocr": "suspect_ocr",
}

_NUMERIC_LITERAL_RE = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w.])"
)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd\u00ad]")


class TranslationQualityBlockedError(ValueError):
    """Raised when a blocking translation-quality finding prevents downstream work."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def stable_finding_id(*, stage: str, code: str, evidence: dict[str, Any]) -> str:
    digest = json.dumps(
        {"stage": stage, "code": code, "evidence": evidence},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()[:24]


def _finding(
    *,
    stage: QualityStage,
    code: str,
    severity: QualitySeverity,
    message: str,
    evidence: dict[str, Any] | None = None,
    chapter_id: str | None = None,
    unit_ids: list[str] | None = None,
    segment_ids: list[str] | None = None,
    source_location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = dict(evidence or {})
    return {
        "finding_id": stable_finding_id(stage=stage, code=code, evidence=evidence),
        "code": code,
        "severity": severity,
        "stage": stage,
        "message": message,
        "evidence": evidence,
        "chapter_id": chapter_id,
        "unit_ids": unit_ids or [],
        "segment_ids": segment_ids or [],
        "source_location": source_location or {},
    }


def _status_from_findings(findings: list[dict[str, Any]]) -> QualityStatus:
    if any(item["severity"] == "blocking" for item in findings):
        return "blocked"
    if findings:
        return "review"
    return "passed"


def _report_payload(
    *,
    stage: QualityStage,
    findings: list[dict[str, Any]],
    input_sha256: str | None,
    output_sha256: str | None,
    rules_version: str | None,
    prompt_version: str | None,
    context: dict[str, Any],
    status: QualityStatus | None = None,
) -> dict[str, Any]:
    resolved_status = status or _status_from_findings(findings)
    blocking_count = sum(1 for item in findings if item["severity"] == "blocking")
    review_count = sum(1 for item in findings if item["severity"] == "review")
    acceptable = blocking_count == 0
    return {
        "schema": REPORT_SCHEMA,
        "stage": stage,
        "status": resolved_status,
        "acceptable": acceptable,
        "blocking_count": blocking_count,
        "review_count": review_count,
        "findings": findings,
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
        "rules_version": rules_version,
        "prompt_version": prompt_version,
        "reading_units_document_fingerprint": context.get("reading_units_document_fingerprint"),
        "chapter_segments_fingerprint": context.get("chapter_segments_fingerprint"),
        "glossary_fingerprint": context.get("glossary_fingerprint"),
        "provenance_summary": context.get("provenance_summary", {}),
        "generated_at": utc_now(),
    }


def write_quality_report(path: Path, payload: dict[str, Any]) -> None:
    atomic_json(path, payload)


def _load_reading_units(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "reading-units.json"
    if not path.exists():
        raise TranslationQualityBlockedError(
            "Translation blocked: reading-units.json is required. Create a new task."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TranslationQualityBlockedError("Translation blocked: reading-units.json is invalid.")
    return payload


def _load_chapter_segments(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "chapter-segments.json"
    if not path.exists():
        raise TranslationQualityBlockedError(
            "Translation blocked: chapter-segments.json is required. Create a new task."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TranslationQualityBlockedError("Translation blocked: chapter-segments.json is invalid.")
    return payload


def _provenance_summary(reading_units: dict[str, Any]) -> dict[str, Any]:
    generation = reading_units.get("generation") if isinstance(reading_units.get("generation"), dict) else {}
    units = reading_units.get("units") if isinstance(reading_units.get("units"), list) else []
    return {
        "source_format": reading_units.get("source_format"),
        "unit_count": len(units),
        "translation_authority": generation.get("translation_authority"),
        "provenance_precision": generation.get("provenance_precision"),
    }


def build_quality_context(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    reading_units_path = run_dir / "reading-units.json"
    chapter_segments_path = run_dir / "chapter-segments.json"
    reading_units = _load_reading_units(run_dir) if reading_units_path.exists() else {}
    chapter_segments = _load_chapter_segments(run_dir) if chapter_segments_path.exists() else {}
    translation_input_path = run_dir / "translation-input.md"
    raw_path = run_dir / "translated.raw.md"
    cleaned_path = run_dir / "translated.cleaned.md"
    final_path = run_dir / "translated.md"
    review_state_path = run_dir / "review-state.json"
    return {
        "run_dir": str(run_dir),
        "reading_units_document_fingerprint": reading_units.get("document_fingerprint"),
        "chapter_segments_fingerprint": sha256_file(chapter_segments_path)
        if chapter_segments_path.exists()
        else None,
        "glossary_fingerprint": glossary_fingerprint(run_dir),
        "provenance_summary": _provenance_summary(reading_units) if reading_units else {},
        "translation_input_sha256": sha256_file(translation_input_path)
        if translation_input_path.exists()
        else None,
        "reading_units_fingerprint": reading_units.get("document_fingerprint"),
        "chapter_segments_sha256": sha256_file(chapter_segments_path)
        if chapter_segments_path.exists()
        else None,
        "raw_sha256": sha256_file(raw_path) if raw_path.exists() else None,
        "cleaned_sha256": sha256_file(cleaned_path) if cleaned_path.exists() else None,
        "final_sha256": sha256_file(final_path) if final_path.exists() else None,
        "translation_prompt_version": TRANSLATION_PROMPT_VERSION,
        "zh_cleanup_rules_version": RULES_VERSION,
        "polish_prompt_version": POLISH_PROMPT_VERSION,
        "manual_decision_state_sha256": sha256_file(review_state_path)
        if review_state_path.exists()
        else None,
    }


def build_quality_signature(run_dir: Path, *, text_operation: str) -> dict[str, Any]:
    context = build_quality_context(run_dir)
    context["text_operation"] = text_operation
    return {
        "translation_input_sha256": context.get("translation_input_sha256"),
        "reading_units_fingerprint": context.get("reading_units_fingerprint"),
        "chapter_segments_sha256": context.get("chapter_segments_sha256"),
        "raw_sha256": context.get("raw_sha256"),
        "cleaned_sha256": context.get("cleaned_sha256"),
        "final_sha256": context.get("final_sha256"),
        "glossary_fingerprint": context.get("glossary_fingerprint"),
        "translation_prompt_version": context.get("translation_prompt_version"),
        "zh_cleanup_rules_version": context.get("zh_cleanup_rules_version"),
        "polish_prompt_version": context.get("polish_prompt_version"),
        "manual_decision_state_sha256": context.get("manual_decision_state_sha256"),
        "text_operation": text_operation,
    }


def _markdown_link_targets(text: str) -> set[str]:
    soup = BeautifulSoup(markdown_renderer.markdown(text), "html.parser")
    targets = {str(item["href"]) for item in soup.find_all("a", href=True)}
    targets.update(url.rstrip(".,;:!?)]") for url in re.findall(r"https?://[^\s<>]+", text))
    return targets


def _markdown_link_specs(text: str) -> list[tuple[bool, str]]:
    specs: list[tuple[bool, str]] = []
    index = 0
    while index < len(text):
        is_image = text.startswith("![", index)
        if is_image or text[index] == "[":
            start = index + 2 if is_image else index + 1
            close = text.find("]", start)
            if close != -1 and close + 1 < len(text) and text[close + 1] == "(":
                depth = 0
                end = -1
                for pos in range(close + 1, len(text)):
                    char = text[pos]
                    if char == "(":
                        depth += 1
                    elif char == ")":
                        depth -= 1
                        if depth == 0:
                            end = pos
                            break
                if end != -1:
                    specs.append((is_image, text[close + 2 : end]))
                    index = end + 1
                    continue
        index += 1
    return specs


def _numeric_literals(text: str) -> list[str]:
    return [match.group(0) for match in _NUMERIC_LITERAL_RE.finditer(text)]


def build_source_quality_report(
    run_dir: Path,
    *,
    text_operation: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    context = context or build_quality_context(run_dir)
    findings: list[dict[str, Any]] = []
    if text_operation != "translate":
        return _report_payload(
            stage="source",
            findings=findings,
            input_sha256=context.get("translation_input_sha256"),
            output_sha256=context.get("translation_input_sha256"),
            rules_version="ingest_quality_report_v1",
            prompt_version=None,
            context=context,
            status="not_applicable",
        )

    translation_input_path = run_dir / "translation-input.md"
    if not translation_input_path.exists():
        findings.append(
            _finding(
                stage="source",
                code="missing_translation_input",
                severity="blocking",
                message="translation-input.md is missing.",
                evidence={"path": str(translation_input_path)},
            )
        )
    else:
        source_text = translation_input_path.read_text(encoding="utf-8")
        ingest = scan_ingest_quality(source_text)
        for issue in ingest.blocking_issues:
            findings.append(
                _finding(
                    stage="source",
                    code=str(issue.get("code") or "blocking_ingest"),
                    severity="blocking",
                    message="Blocking ingest pattern detected before translation.",
                    evidence=dict(issue),
                )
            )
        for issue in ingest.warning_issues:
            findings.append(
                _finding(
                    stage="source",
                    code=str(issue.get("code") or "ingest_warning"),
                    severity="review",
                    message="Ingest warning detected before translation.",
                    evidence=dict(issue),
                )
            )

    try:
        reading_units = _load_reading_units(run_dir)
        validate_reading_units(reading_units)
        generation = reading_units.get("generation") or {}
        if generation.get("translation_authority") is not True:
            findings.append(
                _finding(
                    stage="source",
                    code="reading_units_not_authoritative",
                    severity="blocking",
                    message="Reading units are not translation-authoritative.",
                    evidence={"translation_authority": generation.get("translation_authority")},
                )
            )
        chapter_segments = _load_chapter_segments(run_dir)
        expected = reading_units.get("document_fingerprint")
        actual = chapter_segments.get("reading_units_fingerprint")
        if expected and actual and expected != actual:
            findings.append(
                _finding(
                    stage="source",
                    code="chapter_segments_stale",
                    severity="blocking",
                    message="chapter-segments.json does not match current reading-units fingerprint.",
                    evidence={"expected": expected, "actual": actual},
                )
            )
    except TranslationQualityBlockedError as exc:
        findings.append(
            _finding(
                stage="source",
                code="reading_units_invalid",
                severity="blocking",
                message=str(exc),
                evidence={},
            )
        )
    except ValueError as exc:
        findings.append(
            _finding(
                stage="source",
                code="reading_units_validation_failed",
                severity="blocking",
                message=str(exc),
                evidence={},
            )
        )

    input_sha = context.get("translation_input_sha256")
    return _report_payload(
        stage="source",
        findings=findings,
        input_sha256=input_sha,
        output_sha256=input_sha,
        rules_version="ingest_quality_report_v1",
        prompt_version=TRANSLATION_PROMPT_VERSION,
        context=context,
    )


def run_source_quality_gate_before_translation(
    run_dir: Path,
    *,
    text_operation: str,
) -> dict[str, str]:
    run_dir = run_dir.expanduser().resolve()
    context = build_quality_context(run_dir)
    report = build_source_quality_report(run_dir, text_operation=text_operation, context=context)
    source_path = run_dir / SOURCE_QUALITY_REPORT
    write_quality_report(source_path, report)
    index = build_translation_quality_index(
        run_dir,
        reports={
            "source": report,
            "raw_translation": None,
            "polished_output": None,
        },
        context=context,
        text_operation=text_operation,
    )
    index_path = run_dir / TRANSLATION_QUALITY_INDEX
    write_quality_report(index_path, index)
    if report["status"] == "blocked":
        raise TranslationQualityBlockedError(
            "Source translation quality gate blocked model translation. "
            f"See {source_path} and {index_path}."
        )
    return {
        "source_quality_report": str(source_path),
        "translation_quality_index": str(index_path),
    }


def _review_items_to_findings(
    review_items: list[dict[str, Any]],
    *,
    stage: QualityStage,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in review_items:
        issue_type = str(item.get("issue_type") or "unknown")
        code = _REVIEW_ISSUE_TO_CODE.get(issue_type, issue_type)
        severity: QualitySeverity = "review"
        if issue_type in {
            "missing_translation",
            "translation_failed_open",
            "missing_content",
            "untranslated",
            "possibly_incomplete",
        }:
            severity = "review"
        findings.append(
            _finding(
                stage=stage,
                code=code,
                severity=severity,
                message=f"Review item {issue_type} detected in raw translation output.",
                evidence={"issue_type": issue_type, "item_id": item.get("item_id"), "evidence": item.get("evidence", {})},
                chapter_id=str(item.get("chapter_id") or "") or None,
                segment_ids=[str(item.get("segment_id") or "")] if item.get("segment_id") else [],
                source_location=item.get("source_location") if isinstance(item.get("source_location"), dict) else {},
            )
        )
    return findings


def build_raw_translation_quality_report(
    run_dir: Path,
    *,
    text_operation: str,
    source_markdown: str,
    review_items: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    context = context or build_quality_context(run_dir)
    if text_operation != "translate":
        return _report_payload(
            stage="raw_translation",
            findings=[],
            input_sha256=context.get("translation_input_sha256"),
            output_sha256=None,
            rules_version=RULES_VERSION,
            prompt_version=TRANSLATION_PROMPT_VERSION,
            context=context,
            status="not_applicable",
        )

    raw_path = run_dir / "translated.raw.md"
    findings: list[dict[str, Any]] = []
    if not raw_path.exists():
        findings.append(
            _finding(
                stage="raw_translation",
                code="missing_raw_translation",
                severity="blocking",
                message="translated.raw.md is required for translation quality audit.",
                evidence={"path": str(raw_path)},
            )
        )
        raw_text = ""
    else:
        raw_text = raw_path.read_text(encoding="utf-8")
        if not raw_text.strip():
            findings.append(
                _finding(
                    stage="raw_translation",
                    code="empty_raw_translation",
                    severity="blocking",
                    message="Raw translation output is empty.",
                    evidence={},
                )
            )
        if _CONTROL_CHAR_RE.search(raw_text):
            findings.append(
                _finding(
                    stage="raw_translation",
                    code="control_or_replacement_character",
                    severity="blocking",
                    message="Raw translation contains control or replacement characters.",
                    evidence={},
                )
            )
        if markdown_block_structure(source_markdown) != markdown_block_structure(raw_text):
            findings.append(
                _finding(
                    stage="raw_translation",
                    code="markdown_block_structure_loss",
                    severity="blocking",
                    message="Raw translation changed Markdown block structure relative to source input.",
                    evidence={
                        "source_structure": list(markdown_block_structure(source_markdown)),
                        "raw_structure": list(markdown_block_structure(raw_text)),
                    },
                )
            )
        invented = _markdown_link_targets(raw_text) - _markdown_link_targets(source_markdown)
        if invented:
            findings.append(
                _finding(
                    stage="raw_translation",
                    code="invented_link_targets",
                    severity="blocking",
                    message="Raw translation introduced link targets absent from source.",
                    evidence={"targets": sorted(invented)[:20]},
                )
            )

    binding_path = run_dir / "translation-source-revision.json"
    if binding_path.exists():
        binding = _read_json(binding_path)
        if binding.get("glossary_fingerprint") and binding["glossary_fingerprint"] != context.get("glossary_fingerprint"):
            findings.append(
                _finding(
                    stage="raw_translation",
                    code="stale_glossary_fingerprint",
                    severity="blocking",
                    message="Raw translation provenance does not match current glossary fingerprint.",
                    evidence={
                        "expected": context.get("glossary_fingerprint"),
                        "actual": binding.get("glossary_fingerprint"),
                    },
                )
            )

    if review_items:
        findings.extend(_review_items_to_findings(review_items, stage="raw_translation"))

    output_sha = sha256_text(raw_text) if raw_text else None
    return _report_payload(
        stage="raw_translation",
        findings=findings,
        input_sha256=context.get("translation_input_sha256"),
        output_sha256=output_sha,
        rules_version=RULES_VERSION,
        prompt_version=TRANSLATION_PROMPT_VERSION,
        context=context,
    )


def build_polished_output_quality_report(
    run_dir: Path,
    *,
    text_operation: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    context = context or build_quality_context(run_dir)
    if text_operation != "translate":
        return _report_payload(
            stage="polished_output",
            findings=[],
            input_sha256=context.get("cleaned_sha256"),
            output_sha256=context.get("final_sha256"),
            rules_version=POLISH_PROMPT_VERSION,
            prompt_version=POLISH_PROMPT_VERSION,
            context=context,
            status="not_applicable",
        )

    cleaned_path = run_dir / "translated.cleaned.md"
    final_path = run_dir / "translated.md"
    polished_path = run_dir / "translated.polished.md"
    cleaned_text = cleaned_path.read_text(encoding="utf-8") if cleaned_path.exists() else ""
    final_text = final_path.read_text(encoding="utf-8") if final_path.exists() else ""
    findings: list[dict[str, Any]] = []

    if not cleaned_text.strip():
        findings.append(
            _finding(
                stage="polished_output",
                code="missing_cleaned_translation",
                severity="blocking",
                message="translated.cleaned.md is required before polished-output quality audit.",
                evidence={"path": str(cleaned_path)},
            )
        )
    if not final_text.strip():
        findings.append(
            _finding(
                stage="polished_output",
                code="missing_final_translation",
                severity="blocking",
                message="translated.md is required before polished-output quality audit.",
                evidence={"path": str(final_path)},
            )
        )

    if cleaned_text and final_text:
        if len(cleaned_text.splitlines()) != len(final_text.splitlines()):
            findings.append(
                _finding(
                    stage="polished_output",
                    code="line_count_regression",
                    severity="blocking",
                    message="Final translation changed line count relative to cleaned translation.",
                    evidence={
                        "cleaned_lines": len(cleaned_text.splitlines()),
                        "final_lines": len(final_text.splitlines()),
                    },
                )
            )
        if markdown_block_structure(cleaned_text) != markdown_block_structure(final_text):
            findings.append(
                _finding(
                    stage="polished_output",
                    code="markdown_block_structure_regression",
                    severity="blocking",
                    message="Final translation changed Markdown block structure relative to cleaned translation.",
                    evidence={
                        "cleaned_structure": list(markdown_block_structure(cleaned_text)),
                        "final_structure": list(markdown_block_structure(final_text)),
                    },
                )
            )
        if _markdown_link_specs(cleaned_text) != _markdown_link_specs(final_text):
            findings.append(
                _finding(
                    stage="polished_output",
                    code="link_structure_regression",
                    severity="blocking",
                    message="Final translation changed Markdown link structure.",
                    evidence={},
                )
            )
        if cleaned_text.count("![") != final_text.count("!["):
            findings.append(
                _finding(
                    stage="polished_output",
                    code="image_marker_regression",
                    severity="blocking",
                    message="Final translation changed image marker count.",
                    evidence={},
                )
            )
        if cleaned_text.count("[^") != final_text.count("[^"):
            findings.append(
                _finding(
                    stage="polished_output",
                    code="footnote_marker_regression",
                    severity="blocking",
                    message="Final translation changed footnote marker count.",
                    evidence={},
                )
            )
        if _numeric_literals(cleaned_text) != _numeric_literals(final_text):
            findings.append(
                _finding(
                    stage="polished_output",
                    code="numeric_literal_regression",
                    severity="blocking",
                    message="Final translation changed numeric literals.",
                    evidence={},
                )
            )

    polish_report = _read_json(run_dir / "polish-report.json")
    warning = _read_json(run_dir / "polish-warning.json")
    if warning:
        findings.append(
            _finding(
                stage="polished_output",
                code="polish_unavailable",
                severity="review",
                message="Polish step failed and left a polish-warning artifact.",
                evidence=warning,
            )
        )
    for bucket, code in (
        ("rejected", "polish_rejected"),
        ("unchanged", "polish_unchanged"),
        ("unresolved", "polish_unresolved"),
    ):
        for entry in polish_report.get(bucket, []) if isinstance(polish_report.get(bucket), list) else []:
            if not isinstance(entry, dict):
                continue
            findings.append(
                _finding(
                    stage="polished_output",
                    code=code,
                    severity="review",
                    message=f"Polish decision requires review ({code}).",
                    evidence={"decision": entry.get("decision"), "line": entry.get("line"), "before": entry.get("before")},
                )
            )
    outcome = str(polish_report.get("outcome") or "")
    if outcome in {"no_candidates", "accepted"} and not findings:
        pass

    if polished_path.exists() and not final_text and polished_path.read_text(encoding="utf-8").strip():
        findings.append(
            _finding(
                stage="polished_output",
                code="polished_markdown_not_published",
                severity="blocking",
                message="Polished markdown exists but was not published to translated.md.",
                evidence={"path": str(polished_path)},
            )
        )

    return _report_payload(
        stage="polished_output",
        findings=findings,
        input_sha256=context.get("cleaned_sha256"),
        output_sha256=context.get("final_sha256"),
        rules_version=POLISH_PROMPT_VERSION,
        prompt_version=POLISH_PROMPT_VERSION,
        context=context,
    )


def build_translation_quality_index(
    run_dir: Path,
    *,
    reports: dict[str, dict[str, Any] | None],
    context: dict[str, Any],
    text_operation: str,
    invalidated_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    report_paths: dict[str, str | None] = {
        "source": str(run_dir / SOURCE_QUALITY_REPORT),
        "raw_translation": str(run_dir / RAW_TRANSLATION_QUALITY_REPORT),
        "polished_output": str(run_dir / POLISHED_OUTPUT_QUALITY_REPORT),
    }
    report_hashes: dict[str, str | None] = {}
    for key, path_value in report_paths.items():
        path = Path(path_value)
        report_hashes[key] = sha256_file(path) if path.exists() else None

    findings: list[dict[str, Any]] = []
    for payload in reports.values():
        if isinstance(payload, dict):
            findings.extend(payload.get("findings") or [])
    aggregate_status = _status_from_findings(findings)
    if text_operation != "translate":
        aggregate_status = "not_applicable"
    blocking_count = sum(1 for item in findings if item.get("severity") == "blocking")
    review_count = sum(1 for item in findings if item.get("severity") == "review")
    return {
        "schema": INDEX_SCHEMA,
        "text_operation": text_operation,
        "reports": {
            key: {"path": report_paths[key], "sha256": report_hashes[key]}
            for key in report_paths
        },
        "signature": build_quality_signature(run_dir, text_operation=text_operation),
        "aggregate_status": aggregate_status,
        "acceptable": blocking_count == 0,
        "blocking_count": blocking_count,
        "review_count": review_count,
        "invalidated_artifacts": invalidated_artifacts or [],
        "generated_at": utc_now(),
    }


def invalidate_stale_translation_postprocess(run_dir: Path, *, text_operation: str) -> list[str]:
    run_dir = run_dir.expanduser().resolve()
    if text_operation != "translate":
        return []
    index_path = run_dir / TRANSLATION_QUALITY_INDEX
    prior = _read_json(index_path)
    prior_signature = prior.get("signature") if isinstance(prior.get("signature"), dict) else {}
    current_signature = build_quality_signature(run_dir, text_operation=text_operation)
    if prior_signature == current_signature:
        return []

    removed: list[str] = []
    candidates = [
        run_dir / "polish-report.json",
        run_dir / "translated.polished.md",
        run_dir / "polish-warning.json",
        run_dir / POLISHED_OUTPUT_QUALITY_REPORT,
        run_dir / RAW_TRANSLATION_QUALITY_REPORT,
        index_path,
    ]
    old_epub = (
        prior.get("reports", {})
        .get("polished_output", {})
        if isinstance(prior.get("reports"), dict)
        else {}
    )
    polish_report = _read_json(run_dir / "polish-report.json")
    epub_value = (
        (polish_report.get("outputs") or {}).get("translated_polished_epub")
        if isinstance(polish_report.get("outputs"), dict)
        else None
    )
    if isinstance(epub_value, str) and epub_value.strip():
        candidates.append(Path(epub_value))

    for path in candidates:
        if path.exists():
            path.unlink()
            removed.append(str(path.relative_to(run_dir)) if path.is_relative_to(run_dir) else str(path))
    return removed


def write_translation_quality_bundle(
    run_dir: Path,
    *,
    text_operation: str,
    source_markdown: str,
    review_items: list[dict[str, Any]] | None = None,
    invalidated_artifacts: list[str] | None = None,
) -> dict[str, str]:
    run_dir = run_dir.expanduser().resolve()
    context = build_quality_context(run_dir)
    source_report = build_source_quality_report(run_dir, text_operation=text_operation, context=context)
    raw_report = build_raw_translation_quality_report(
        run_dir,
        text_operation=text_operation,
        source_markdown=source_markdown,
        review_items=review_items,
        context=context,
    )
    polished_report = build_polished_output_quality_report(
        run_dir,
        text_operation=text_operation,
        context=context,
    )
    source_path = run_dir / SOURCE_QUALITY_REPORT
    raw_path = run_dir / RAW_TRANSLATION_QUALITY_REPORT
    polished_path = run_dir / POLISHED_OUTPUT_QUALITY_REPORT
    write_quality_report(source_path, source_report)
    write_quality_report(raw_path, raw_report)
    write_quality_report(polished_path, polished_report)
    index = build_translation_quality_index(
        run_dir,
        reports={
            "source": source_report,
            "raw_translation": raw_report,
            "polished_output": polished_report,
        },
        context=context,
        text_operation=text_operation,
        invalidated_artifacts=invalidated_artifacts,
    )
    index_path = run_dir / TRANSLATION_QUALITY_INDEX
    write_quality_report(index_path, index)
    return {
        "source_quality_report": str(source_path),
        "raw_translation_quality_report": str(raw_path),
        "polished_output_quality_report": str(polished_path),
        "translation_quality_index": str(index_path),
    }


def translation_quality_summary(run_dir: Path) -> dict[str, Any]:
    index = _read_json(run_dir / TRANSLATION_QUALITY_INDEX)
    if not index:
        return {"translation_quality_blocking": False, "translation_quality_review_count": 0}
    return {
        "translation_quality_blocking": not bool(index.get("acceptable", False))
        and index.get("aggregate_status") == "blocked",
        "translation_quality_review_count": int(index.get("review_count") or 0),
    }


def _requires_translation_quality(run_dir: Path) -> bool:
    manifest = _read_json(run_dir / "manifest.json")
    text_operation = manifest.get("text_operation")
    translation_mode = (manifest.get("translation") or {}).get("mode")
    if text_operation != "translate" and translation_mode not in {"translated", "preserved"}:
        return False
    if text_operation == "preserve":
        return False
    if translation_mode in {"not_requested", "skipped_same_language"}:
        return False
    return text_operation == "translate" or translation_mode == "translated"


def assert_translation_quality_current(run_dir: Path) -> None:
    run_dir = run_dir.expanduser().resolve()
    if not _requires_translation_quality(run_dir):
        return
    required = [
        SOURCE_QUALITY_REPORT,
        RAW_TRANSLATION_QUALITY_REPORT,
        POLISHED_OUTPUT_QUALITY_REPORT,
        TRANSLATION_QUALITY_INDEX,
    ]
    missing = [name for name in required if not (run_dir / name).exists()]
    if missing:
        raise ValueError(
            "Export blocked: current translation quality artifacts are missing "
            f"({', '.join(missing)}). Create a new task."
        )
    index = _read_json(run_dir / TRANSLATION_QUALITY_INDEX)
    signature = index.get("signature") if isinstance(index.get("signature"), dict) else {}
    current = build_quality_signature(
        run_dir,
        text_operation=str(index.get("text_operation") or "translate"),
    )
    if signature != current:
        raise ValueError(
            "Export blocked: translation-quality-index.json is stale relative to current inputs. "
            "Re-run translation or create a new task."
        )
    reports = index.get("reports") if isinstance(index.get("reports"), dict) else {}
    for key, meta in reports.items():
        if not isinstance(meta, dict):
            continue
        path_value = meta.get("path")
        expected_hash = meta.get("sha256")
        if not isinstance(path_value, str) or not isinstance(expected_hash, str):
            continue
        path = Path(path_value)
        if not path.is_absolute():
            path = run_dir / path.name
        if not path.exists() or sha256_file(path) != expected_hash:
            raise ValueError(
                f"Export blocked: translation quality report {key} is missing or stale. Create a new task."
            )
    if int(index.get("blocking_count") or 0) > 0 or index.get("aggregate_status") == "blocked":
        raise ValueError(
            "Export blocked: blocking translation quality findings remain. "
            f"See {run_dir / TRANSLATION_QUALITY_INDEX}."
        )
