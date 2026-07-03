from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


JOB_SCHEMA = "translation_job_v1"
EVENT_SCHEMA = "translation_event_v1"
PROGRESS_SCHEMA = "translation_progress_v1"


def _now() -> float:
    return time.time()


def _isoish(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))


def _completed_indices_from_cache(run_dir: Path) -> list[int]:
    cache_dir = run_dir / "translation-cache"
    if not cache_dir.is_dir():
        return []
    indices: set[int] = set()
    for path in cache_dir.glob("chunk-*.md"):
        match = re.match(r"chunk-(\d+)-", path.name)
        if not match:
            continue
        if path.read_text(encoding="utf-8").strip():
            indices.add(int(match.group(1)))
    return sorted(indices)


def _jobs_dir(run_dir: Path) -> Path:
    return run_dir / "jobs"


def _job_path(run_dir: Path) -> Path:
    return _jobs_dir(run_dir) / "translation-job.json"


def _events_path(run_dir: Path) -> Path:
    return _jobs_dir(run_dir) / "translation-events.jsonl"


def _progress_path(run_dir: Path) -> Path:
    return _jobs_dir(run_dir) / "progress.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def load_progress(run_dir: Path) -> dict[str, Any]:
    path = _progress_path(run_dir)
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def load_translation_job(run_dir: Path) -> dict[str, Any]:
    path = _job_path(run_dir)
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def load_translation_events(run_dir: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = _events_path(run_dir)
    if not path.exists():
        raise FileNotFoundError(path)
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    if limit is not None and limit > 0:
        return events[-limit:]
    return events


def format_progress_bar(completed: int, total: int, *, width: int = 28) -> str:
    total = max(total, 1)
    completed = max(min(completed, total), 0)
    filled = int(width * completed / total)
    return f"[{'#' * filled}{'-' * (width - filled)}]"


def _format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "ETA --"
    remaining = max(int(seconds), 0)
    minutes, secs = divmod(remaining, 60)
    if minutes:
        return f"ETA {minutes}m{secs:02d}s"
    return f"ETA {secs}s"


def format_progress_line(progress: dict[str, Any]) -> str:
    completed = int(progress.get("completed_chunks", 0))
    total = int(progress.get("total_chunks", 0))
    percent = int((completed / total) * 100) if total else 0
    bar = format_progress_bar(completed, total)
    status = str(progress.get("status") or "unknown")
    cache_hits = int(progress.get("cache_hit_chunks", 0))
    failed = int(progress.get("failed_chunks", 0))
    running = int(progress.get("running_chunks", 0))
    eta = _format_eta(progress.get("estimated_remaining_seconds"))
    return (
        f"Translation {bar} {completed}/{total} ({percent}%) "
        f"status={status} running={running} cache={cache_hits} failed={failed} {eta}"
    )


def format_progress_report(run_dir: Path) -> str:
    progress = load_progress(run_dir)
    job = load_translation_job(run_dir)
    lines = [
        format_progress_line(progress),
        (
            f"Job {job.get('job_id')} | translator={job.get('translator')} | "
            f"resume={job.get('resume')} | elapsed={progress.get('elapsed_seconds')}s"
        ),
        f"Artifacts: {run_dir / 'jobs' / 'progress.json'}",
    ]
    return "\n".join(lines)


def format_event_line(event: dict[str, Any]) -> str:
    name = str(event.get("event") or "unknown")
    chunk_index = event.get("chunk_index")
    chunk_part = f" chunk={chunk_index}" if chunk_index is not None else ""
    timestamp = str(event.get("timestamp") or "")
    details: list[str] = []
    if event.get("attempt") is not None:
        details.append(f"attempt={event['attempt']}")
    if event.get("error_type"):
        details.append(f"error={event['error_type']}")
    if event.get("message"):
        details.append(str(event["message"])[:120])
    if event.get("reason"):
        details.append(str(event["reason"])[:120])
    if event.get("status"):
        details.append(f"status={event['status']}")
    detail_part = f" | {'; '.join(details)}" if details else ""
    return f"{timestamp} {name}{chunk_part}{detail_part}"


@dataclass(slots=True)
class LiveProgressTranslationJobObserver:
    inner: TranslationJobObserver
    stream: Any = sys.stderr
    _last_line: str = ""

    def _emit(self) -> None:
        line = format_progress_line(load_progress(self.inner.run_dir))
        if line == self._last_line:
            return
        print(f"\r{line}", end="", file=self.stream, flush=True)
        self._last_line = line

    def finish_live(self) -> None:
        if self._last_line:
            print(file=self.stream, flush=True)
            self._last_line = ""

    def attempt_start(self, *, chunk_index: int, input_hash: str, attempt: int) -> None:
        self.inner.attempt_start(chunk_index=chunk_index, input_hash=input_hash, attempt=attempt)
        self._emit()

    def attempt_success(self, *, chunk_index: int, input_hash: str, cache_path: Path | None) -> None:
        self.inner.attempt_success(chunk_index=chunk_index, input_hash=input_hash, cache_path=cache_path)
        self._emit()

    def attempt_failure(
        self,
        *,
        chunk_index: int,
        input_hash: str,
        attempt: int,
        error_type: str,
        message: str,
        retryable: bool,
    ) -> None:
        self.inner.attempt_failure(
            chunk_index=chunk_index,
            input_hash=input_hash,
            attempt=attempt,
            error_type=error_type,
            message=message,
            retryable=retryable,
        )
        self._emit()

    def cache_hit(self, *, chunk_index: int, input_hash: str, cache_path: Path) -> None:
        self.inner.cache_hit(chunk_index=chunk_index, input_hash=input_hash, cache_path=cache_path)
        self._emit()

    def cache_invalidated(self, *, chunk_index: int, input_hash: str, cache_path: Path, reason: str) -> None:
        self.inner.cache_invalidated(
            chunk_index=chunk_index,
            input_hash=input_hash,
            cache_path=cache_path,
            reason=reason,
        )
        self._emit()

    def finish(self, *, status: str) -> None:
        self.inner.finish(status=status)
        self._emit()
        self.finish_live()


@dataclass(slots=True)
class TranslationJobObserver:
    run_dir: Path
    job_id: str
    total_chunks: int
    started_at: float
    progress_sink: Callable[[dict[str, Any]], None] | None = None
    _progress_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def _load_progress(self) -> dict[str, Any]:
        return load_progress(self.run_dir)

    def _record_chunk_done(self, progress: dict[str, Any], chunk_index: int) -> bool:
        progress["total_chunks"] = max(
            int(progress.get("total_chunks", 0)),
            int(chunk_index) + 1,
        )
        indices = list(progress.get("completed_chunk_indices") or [])
        if chunk_index in indices:
            return False
        indices.append(int(chunk_index))
        progress["completed_chunk_indices"] = sorted(indices)
        progress["completed_chunks"] = len(indices)
        return True

    def _write_progress(self, progress: dict[str, Any]) -> None:
        with self._progress_lock:
            progress = dict(progress)
            progress["updated_at"] = _isoish(_now())
            elapsed = max(_now() - self.started_at, 0.0)
            progress["elapsed_seconds"] = round(elapsed, 3)
            total = max(int(progress.get("total_chunks", 0)), 0)
            completed = min(int(progress.get("completed_chunks", 0)), total) if total else int(
                progress.get("completed_chunks", 0)
            )
            progress["completed_chunks"] = completed
            progress["remaining_chunks"] = max(total - completed - int(progress.get("failed_chunks", 0)), 0)
            if completed > 0:
                per_chunk = elapsed / completed
                progress["estimated_remaining_seconds"] = round(per_chunk * progress["remaining_chunks"], 3)
            else:
                progress["estimated_remaining_seconds"] = None
            _atomic_write_json(_progress_path(self.run_dir), progress)
            if self.progress_sink is not None:
                self.progress_sink(dict(progress))

    def _event(self, event: str, **payload: Any) -> None:
        entry = {
            "schema": EVENT_SCHEMA,
            "job_id": self.job_id,
            "event": event,
            "timestamp": _isoish(_now()),
            **payload,
        }
        _append_jsonl(_events_path(self.run_dir), entry)

    def attempt_start(self, *, chunk_index: int, input_hash: str, attempt: int) -> None:
        with self._progress_lock:
            progress = self._load_progress()
            progress["total_chunks"] = max(
                int(progress.get("total_chunks", 0)),
                int(chunk_index) + 1,
            )
            progress["running_chunks"] = int(progress.get("running_chunks", 0)) + 1
            progress["status"] = "running"
            self._write_progress(progress)
        self._event("attempt_start", chunk_index=chunk_index, input_hash=input_hash, attempt=attempt)

    def attempt_success(self, *, chunk_index: int, input_hash: str, cache_path: Path | None) -> None:
        with self._progress_lock:
            progress = self._load_progress()
            progress["running_chunks"] = max(int(progress.get("running_chunks", 0)) - 1, 0)
            progress["retrying_chunks"] = max(int(progress.get("retrying_chunks", 0)) - 1, 0)
            self._record_chunk_done(progress, chunk_index)
            self._write_progress(progress)
        self._event(
            "attempt_success",
            chunk_index=chunk_index,
            input_hash=input_hash,
            cache_path=str(cache_path) if cache_path else None,
        )

    def attempt_failure(
        self,
        *,
        chunk_index: int,
        input_hash: str,
        attempt: int,
        error_type: str,
        message: str,
        retryable: bool,
    ) -> None:
        with self._progress_lock:
            progress = self._load_progress()
            progress["running_chunks"] = max(int(progress.get("running_chunks", 0)) - 1, 0)
            if retryable:
                progress["retrying_chunks"] = int(progress.get("retrying_chunks", 0)) + 1
            else:
                progress["failed_chunks"] = int(progress.get("failed_chunks", 0)) + 1
            self._write_progress(progress)
        self._event(
            "attempt_failure",
            chunk_index=chunk_index,
            input_hash=input_hash,
            attempt=attempt,
            error_type=error_type,
            message=message[:1000],
            retryable=retryable,
        )

    def cache_hit(self, *, chunk_index: int, input_hash: str, cache_path: Path) -> None:
        with self._progress_lock:
            progress = self._load_progress()
            if self._record_chunk_done(progress, chunk_index):
                progress["cache_hit_chunks"] = int(progress.get("cache_hit_chunks", 0)) + 1
            self._write_progress(progress)
        self._event("cache_hit", chunk_index=chunk_index, input_hash=input_hash, cache_path=str(cache_path))

    def cache_invalidated(self, *, chunk_index: int, input_hash: str, cache_path: Path, reason: str) -> None:
        with self._progress_lock:
            progress = self._load_progress()
            progress["invalid_cache_chunks"] = int(progress.get("invalid_cache_chunks", 0)) + 1
            self._write_progress(progress)
        self._event(
            "cache_invalidated",
            chunk_index=chunk_index,
            input_hash=input_hash,
            cache_path=str(cache_path),
            reason=reason,
        )

    def finish(self, *, status: str) -> None:
        with self._progress_lock:
            progress = self._load_progress()
            progress["status"] = status
            progress["running_chunks"] = 0
            if status == "completed":
                actual_total = len(progress.get("completed_chunk_indices") or [])
                progress["total_chunks"] = actual_total
            self._write_progress(progress)
            job = json.loads(_job_path(self.run_dir).read_text(encoding="utf-8"))
            job["status"] = status
            if status == "completed":
                job["total_chunks"] = actual_total
            job["finished_at"] = _isoish(_now())
            _atomic_write_json(_job_path(self.run_dir), job)
        self._event("job_finished", status=status)


def create_translation_job(
    *,
    run_dir: Path,
    translator: str,
    source_language: str | None,
    target_language: str,
    total_chunks: int,
    concurrency: int,
    max_chunk_chars: int,
    resume: bool,
    live_progress: bool = False,
    progress_sink: Callable[[dict[str, Any]], None] | None = None,
) -> TranslationJobObserver | LiveProgressTranslationJobObserver:
    started = _now()
    job_id = f"translation-{int(started)}"
    jobs_dir = _jobs_dir(run_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    events_path = _events_path(run_dir)
    if resume and events_path.exists():
        pass
    else:
        events_path.write_text("", encoding="utf-8")
    job = {
        "schema": JOB_SCHEMA,
        "job_id": job_id,
        "status": "running",
        "started_at": _isoish(started),
        "finished_at": None,
        "translator": translator,
        "source_language": source_language,
        "target_language": target_language,
        "total_chunks": total_chunks,
        "concurrency": concurrency,
        "max_chunk_chars": max_chunk_chars,
        "resume": resume,
    }
    # Cache filenames alone cannot prove validity because the current input hash
    # is only known when each chunk is processed. Count hits through cache_hit().
    completed_indices: list[int] = []
    completed = len(completed_indices)
    progress = {
        "schema": PROGRESS_SCHEMA,
        "job_id": job_id,
        "status": "running",
        "started_at": _isoish(started),
        "updated_at": _isoish(started),
        "elapsed_seconds": 0.0,
        "estimated_remaining_seconds": None,
        "total_chunks": total_chunks,
        "completed_chunks": completed,
        "completed_chunk_indices": completed_indices,
        "remaining_chunks": max(total_chunks - completed, 0),
        "running_chunks": 0,
        "failed_chunks": 0,
        "retrying_chunks": 0,
        "cache_hit_chunks": completed if resume else 0,
        "invalid_cache_chunks": 0,
    }
    _atomic_write_json(_job_path(run_dir), job)
    _atomic_write_json(_progress_path(run_dir), progress)
    observer = TranslationJobObserver(
        run_dir=run_dir,
        job_id=job_id,
        total_chunks=total_chunks,
        started_at=started,
        progress_sink=progress_sink,
    )
    observer._event("job_started", total_chunks=total_chunks, concurrency=concurrency)
    if live_progress:
        return LiveProgressTranslationJobObserver(observer)
    return observer
