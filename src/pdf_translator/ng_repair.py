from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_AGENT_SOURCE_ROOT = Path.home() / "Desktop" / "文档"
SUPPORTED_BOOK_EXTENSIONS = {".pdf", ".epub"}


RETRYABLE_ERROR_TYPES = frozenset(
    {
        "IngestTimeoutError",
        "IngestExecutionError",
        "InputGateError",
        "RuntimeError",
        "ConnectionError",
        "TimeoutError",
    }
)

NON_RETRYABLE_ERROR_MARKERS = (
    "looks untranslated",
    "not a zip file",
    "retry_exhausted quality",
)


@dataclass(slots=True)
class NgRepairAction:
    action: str
    book_dir: Path
    detail: str | None = None


@dataclass(slots=True)
class NgRepairReport:
    removed_ghost_dirs: int = 0
    reset_retries: int = 0
    skipped: int = 0
    actions: list[NgRepairAction] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        if not self.actions:
            return []
        lines = ["NG repair:"]
        for item in self.actions:
            label = item.book_dir.name[:70]
            if item.detail:
                lines.append(f"- {item.action}: {label} ({item.detail})")
            else:
                lines.append(f"- {item.action}: {label}")
        lines.append(
            f"NG repair totals: removed_ghost_dirs={self.removed_ghost_dirs} "
            f"reset_retries={self.reset_retries} skipped={self.skipped}"
        )
        return lines


def repair_ng_directory(
    source_root: Path = DEFAULT_AGENT_SOURCE_ROOT,
    *,
    max_ng_retries: int = 2,
    max_auto_repair_resets: int = 3,
) -> NgRepairReport:
    source_root = source_root.expanduser().resolve()
    ng_dir = source_root / "NG"
    ok_dir = source_root / "OK"
    report = NgRepairReport()
    if not ng_dir.exists():
        return report

    for book_dir in sorted(ng_dir.iterdir(), key=lambda path: path.name):
        if not book_dir.is_dir() or book_dir.name.startswith("."):
            continue

        status_path = book_dir / "phase-a-status.json"
        status = _load_status(status_path)
        has_source = _has_source_book(book_dir)
        ok_match = _find_ok_match(book_dir.name, ok_dir)

        if not has_source and ok_match is not None:
            shutil.rmtree(book_dir)
            report.removed_ghost_dirs += 1
            report.actions.append(
                NgRepairAction(
                    action="removed_ghost_dir",
                    book_dir=book_dir,
                    detail=f"already in OK/{ok_match.name[:50]}",
                )
            )
            continue

        if status is None or status.get("status") != "ng":
            report.skipped += 1
            continue

        if not has_source:
            report.skipped += 1
            continue

        attempt_count = _status_attempt_count(status)
        retry_exhausted = bool(status.get("retry_exhausted")) or (
            max_ng_retries >= 0 and attempt_count >= max_ng_retries
        )
        if not retry_exhausted:
            report.skipped += 1
            continue

        if not _is_retryable_failure(status):
            report.skipped += 1
            continue

        auto_repair_count = int(status.get("auto_repair_count") or 0)
        if auto_repair_count >= max_auto_repair_resets:
            report.skipped += 1
            report.actions.append(
                NgRepairAction(
                    action="skipped_retry_cap",
                    book_dir=book_dir,
                    detail=f"auto_repair_count={auto_repair_count}",
                )
            )
            continue

        _reset_status_for_retry(status_path, status, auto_repair_count=auto_repair_count + 1)
        report.reset_retries += 1
        report.actions.append(
            NgRepairAction(
                action="reset_retry",
                book_dir=book_dir,
                detail=status.get("error_type") or "unknown",
            )
        )

    return report


def _load_status(status_path: Path) -> dict[str, Any] | None:
    if not status_path.exists():
        return None
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _status_attempt_count(status: dict[str, Any]) -> int:
    raw = status.get("attempt_count")
    if isinstance(raw, int):
        return max(raw, 0)
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 1 if status.get("resume_from_ng") else 0


def _has_source_book(directory: Path) -> bool:
    return any(
        path.is_file() and path.suffix.lower() in SUPPORTED_BOOK_EXTENSIONS and not path.name.startswith(".")
        for path in directory.iterdir()
    )


def _find_ok_match(ng_name: str, ok_dir: Path) -> Path | None:
    if not ok_dir.exists():
        return None
    prefix = ng_name[:40]
    matches = [path for path in ok_dir.iterdir() if path.is_dir() and path.name.startswith(prefix)]
    if not matches:
        return None
    return sorted(matches, key=lambda path: path.name)[0]


def _is_retryable_failure(status: dict[str, Any]) -> bool:
    error = str(status.get("error") or "")
    error_type = str(status.get("error_type") or "")
    lowered = error.lower()

    if any(marker in lowered for marker in NON_RETRYABLE_ERROR_MARKERS):
        return False

    if error_type in RETRYABLE_ERROR_TYPES:
        return True
    if "translation failed for chunk" in lowered:
        return True
    if "minimax translation failed" in lowered:
        return True
    if "ingest timed out" in lowered:
        return True
    if "mps tensor" in lowered or "float64" in lowered:
        return True
    if "file size" in lowered and "exceeds" in lowered:
        return True
    return False


def _reset_status_for_retry(status_path: Path, status: dict[str, Any], *, auto_repair_count: int) -> None:
    status["attempt_count"] = 0
    status.pop("retry_exhausted", None)
    status.pop("non_retryable", None)
    status.pop("completed_at", None)
    status.pop("error", None)
    status.pop("error_type", None)
    status.pop("traceback", None)
    status["auto_repair_count"] = auto_repair_count
    status["auto_repaired_at"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
