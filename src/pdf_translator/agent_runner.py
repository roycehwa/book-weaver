from __future__ import annotations

import json
import os
import shutil
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pdf_translator.config import DEFAULT_TRANSLATION_CONCURRENCY, RunSettings
from pdf_translator.guardrails import DEFAULT_INGEST_TIMEOUT_SECONDS
from pdf_translator.pipeline import run_translation_pipeline
from pdf_translator.polish import run_polish


DEFAULT_AGENT_SOURCE_ROOT = Path.home() / "Desktop" / "文档"
SUPPORTED_BOOK_EXTENSIONS = {".pdf", ".epub"}


@dataclass(slots=True)
class AgentRunResult:
    status: str
    source_path: Path | None = None
    destination_dir: Path | None = None
    work_dir: Path | None = None
    message: str | None = None


class AgentLockError(RuntimeError):
    pass


@dataclass(slots=True)
class AgentWorkItem:
    source_path: Path
    lane: str
    output_parent: Path
    failed_dir: Path | None = None


def run_agent_once(
    *,
    source_root: Path = DEFAULT_AGENT_SOURCE_ROOT,
    target_language: str = "zh-CN",
    translator: str = "minimax",
    output_format: str = "epub",
    max_chunk_chars: int = 9000,
    translation_concurrency: int = DEFAULT_TRANSLATION_CONCURRENCY,
    ingest_timeout_seconds: int | None = DEFAULT_INGEST_TIMEOUT_SECONDS,
    max_file_size_mb: float | None = None,
    max_page_count: int | None = None,
    polish_english: bool = True,
    polish_translator: str | None = None,
    source_lanes: tuple[str, ...] = ("EN", "CN"),
) -> AgentRunResult:
    source_root = source_root.expanduser().resolve()
    source_root.mkdir(parents=True, exist_ok=True)
    for name in ("EN", "CN", "OK", "NG"):
        (source_root / name).mkdir(parents=True, exist_ok=True)

    lock_path = source_root / ".pdf-translator-agent.lock"
    with _agent_lock(lock_path):
        candidate = _pick_resume_book(source_root, source_lanes=source_lanes) or _pick_next_book(
            source_root,
            source_lanes=source_lanes,
        )
        if candidate is None:
            return AgentRunResult(status="no_work", message="No PDF or EPUB source found.")

        source_path = candidate.source_path
        lane = candidate.lane
        token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_parent = candidate.output_parent
        output_parent.mkdir(parents=True, exist_ok=True)
        expected_run_dir = output_parent / source_path.stem
        status_payload: dict[str, Any] = {
            "schema": "pdf_translator_agent_status_v1",
            "started_at": token,
            "source_path": str(source_path),
            "source_lane": lane,
            "target_language": target_language,
            "translator": translator,
            "polish_english": polish_english,
            "work_base": str(output_parent),
            "resume_from_ng": candidate.failed_dir is not None,
        }

        try:
            source_language = "en" if lane == "EN" else "zh-CN"
            settings = RunSettings(
                source_pdf=source_path,
                output_dir=output_parent,
                target_language=target_language,
                source_language=source_language,
                translator=translator,
                max_chunk_chars=max_chunk_chars,
                profile_name="book",
                output_format=output_format,
                translation_concurrency=translation_concurrency,
                ingest_timeout_seconds=ingest_timeout_seconds,
                max_file_size_mb=max_file_size_mb,
                max_page_count=max_page_count,
            )
            artifacts = run_translation_pipeline(settings)
            polish_report = None
            if lane == "EN" and polish_english:
                polish_report = run_polish(
                    run_dir=artifacts.output_dir,
                    target_language=target_language,
                    translator_name=polish_translator or translator,
                )

            destination_dir = _finalize_run(
                source_path=source_path,
                run_dir=artifacts.output_dir,
                destination_parent=source_root / "OK",
                failed_dir=candidate.failed_dir,
            )
            status_payload.update(
                {
                    "status": "ok",
                    "completed_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                    "destination_dir": str(destination_dir),
                    "manifest": str(destination_dir / "manifest.json"),
                    "polish_report": str(destination_dir / "polish-report.json") if polish_report else None,
                }
            )
            _write_status(destination_dir, status_payload)
            _cleanup_empty_work_base(output_parent)
            return AgentRunResult(
                status="ok",
                source_path=source_path,
                destination_dir=destination_dir,
                work_dir=output_parent,
            )
        except Exception as exc:
            if candidate.failed_dir is not None:
                destination_dir = candidate.failed_dir
            else:
                destination_dir = _finalize_failed_run(
                    source_path=source_path,
                    run_dir=expected_run_dir,
                    destination_parent=source_root / "NG",
                )
            status_payload.update(
                {
                    "status": "ng",
                    "completed_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                    "destination_dir": str(destination_dir),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
            _write_status(destination_dir, status_payload)
            return AgentRunResult(
                status="ng",
                source_path=source_path,
                destination_dir=destination_dir,
                work_dir=output_parent,
                message=str(exc),
            )


class _agent_lock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self.fd: int | None = None

    def __enter__(self) -> None:
        self._remove_stale_lock()
        try:
            self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise AgentLockError(f"Agent lock already exists: {self.lock_path}") from exc
        os.write(self.fd, str(os.getpid()).encode("utf-8"))

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass

    def _remove_stale_lock(self) -> None:
        if not self.lock_path.exists():
            return
        try:
            raw_pid = self.lock_path.read_text(encoding="utf-8").strip()
            pid = int(raw_pid)
        except (OSError, ValueError):
            return
        if pid <= 0:
            return
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            self.lock_path.unlink(missing_ok=True)
        except PermissionError:
            return


def _pick_resume_book(source_root: Path, *, source_lanes: tuple[str, ...] = ("EN", "CN")) -> AgentWorkItem | None:
    allowed_lanes = tuple(lane for lane in source_lanes if lane in {"EN", "CN"})
    candidates: list[tuple[float, Path, Path, str]] = []
    ng_dir = source_root / "NG"
    if not ng_dir.exists():
        return None
    for failed_dir in ng_dir.iterdir():
        if not failed_dir.is_dir() or failed_dir.name.startswith("."):
            continue
        status_path = failed_dir / "phase-a-status.json"
        if not status_path.exists():
            continue
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if status.get("status") != "ng":
            continue
        lane = str(status.get("source_lane") or "")
        if lane not in allowed_lanes:
            continue
        source_path = _find_source_book(failed_dir)
        if source_path is None:
            continue
        candidates.append((status_path.stat().st_mtime, failed_dir, source_path, lane))
    if not candidates:
        return None
    _, failed_dir, source_path, lane = sorted(candidates, key=lambda item: (item[0], str(item[1])))[0]
    return AgentWorkItem(
        source_path=source_path,
        lane=lane,
        output_parent=failed_dir,
        failed_dir=failed_dir,
    )


def _pick_next_book(source_root: Path, *, source_lanes: tuple[str, ...] = ("EN", "CN")) -> AgentWorkItem | None:
    candidates: list[tuple[float, str, Path]] = []
    allowed_lanes = tuple(lane for lane in source_lanes if lane in {"EN", "CN"})
    for lane in allowed_lanes:
        lane_dir = source_root / lane
        if not lane_dir.exists():
            continue
        for path in lane_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in SUPPORTED_BOOK_EXTENSIONS and not path.name.startswith("."):
                candidates.append((path.stat().st_mtime, lane, path))
    if not candidates:
        return None
    _, lane, path = sorted(candidates, key=lambda item: (item[0], item[1], str(item[2])))[0]
    return AgentWorkItem(
        source_path=path,
        lane=lane,
        output_parent=source_root / ".hermes-working",
    )


def _finalize_run(
    *,
    source_path: Path,
    run_dir: Path,
    destination_parent: Path,
    failed_dir: Path | None = None,
) -> Path:
    destination_dir = _unique_path(destination_parent / _safe_name(source_path.stem))
    destination_parent.mkdir(parents=True, exist_ok=True)
    if run_dir.exists():
        shutil.move(str(run_dir), str(destination_dir))
    else:
        destination_dir.mkdir(parents=True, exist_ok=False)
    _move_source_into(source_path, destination_dir)
    if failed_dir is not None:
        for child in failed_dir.iterdir():
            if child == destination_dir or child.name == "phase-a-status.json":
                continue
            shutil.move(str(child), str(_unique_path(destination_dir / child.name)))
        _cleanup_empty_work_base(failed_dir)
    return destination_dir


def _finalize_failed_run(*, source_path: Path, run_dir: Path, destination_parent: Path) -> Path:
    destination_dir = _unique_path(destination_parent / _safe_name(source_path.stem))
    destination_dir.mkdir(parents=True, exist_ok=False)
    if run_dir.exists():
        shutil.move(str(run_dir), str(destination_dir / run_dir.name))
        _cleanup_empty_work_base(run_dir.parent)
    _move_source_into(source_path, destination_dir)
    return destination_dir


def _find_source_book(directory: Path) -> Path | None:
    candidates = [
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_BOOK_EXTENSIONS and not path.name.startswith(".")
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name))[0]


def _move_source_into(source_path: Path, destination_dir: Path) -> None:
    if not source_path.exists():
        return
    shutil.move(str(source_path), str(_unique_path(destination_dir / source_path.name)))


def _cleanup_empty_work_base(work_base: Path) -> None:
    current = work_base
    while current.name in {work_base.name, ".hermes-working"}:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _write_status(destination_dir: Path, payload: dict[str, Any]) -> None:
    (destination_dir / "phase-a-status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate unique path below {path.parent}")


def _safe_name(value: str) -> str:
    name = "".join(" " if char in '\\/:*?"<>|' else char for char in value)
    name = " ".join(name.split()).strip(" .")
    return name[:140].rstrip(" .") or "book"
