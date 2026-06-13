from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import threading
import uuid
import zipfile
from typing import Any


PROCESSING_MODES = frozenset({"auto", "translate", "preserve"})
TEXT_OPERATIONS = frozenset({"translate", "preserve"})
JOB_STATES = frozenset(
    {
        "created",
        "ingesting",
        "reconstructing",
        "translating",
        "preserving",
        "validating",
        "pre_review",
        "awaiting_human_review",
        "exporting",
        "completed",
        "failed",
    }
)


def normalize_language_family(language: str | None) -> str | None:
    if language is None:
        return None
    normalized = language.strip().lower().replace("_", "-")
    if not normalized:
        return None
    aliases = {
        "chinese": "zh",
        "mandarin": "zh",
        "english": "en",
    }
    return aliases.get(normalized, normalized.split("-", 1)[0])


def resolve_text_operation(
    processing_mode: str,
    source_language: str | None,
    target_language: str,
) -> str:
    mode = processing_mode.strip().lower()
    if mode not in PROCESSING_MODES:
        allowed = ", ".join(sorted(PROCESSING_MODES))
        raise ValueError(f"Unsupported processing mode {processing_mode!r}; expected one of: {allowed}.")
    if mode in TEXT_OPERATIONS:
        return mode

    source_family = normalize_language_family(source_language)
    target_family = normalize_language_family(target_language)
    if source_family is not None and source_family == target_family:
        return "preserve"
    return "translate"


class JobRepository:
    def __init__(self, jobs_dir: str | Path):
        self.jobs_dir = Path(jobs_dir).expanduser().resolve()
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def job_dir(self, job_id: str) -> Path:
        if not job_id or Path(job_id).name != job_id:
            raise ValueError("Invalid job ID.")
        return self.jobs_dir / job_id

    def create(
        self,
        *,
        source_path: str | Path,
        processing_mode: str = "auto",
        source_language: str | None = None,
        target_language: str = "zh-CN",
        translator: str = "minimax",
        output_format: str = "epub",
        job_id: str | None = None,
    ) -> dict[str, Any]:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        mode = processing_mode.strip().lower()
        if mode not in PROCESSING_MODES:
            resolve_text_operation(mode, source_language, target_language)

        identifier = job_id or uuid.uuid4().hex
        directory = self.job_dir(identifier)
        with self._lock:
            directory.mkdir(parents=False, exist_ok=False)
            source_dir = directory / "source"
            source_dir.mkdir()
            (directory / "artifacts").mkdir()
            (directory / "cache" / "translation").mkdir(parents=True)
            (directory / "versions").mkdir()
            stored_source = source_dir / source.name
            shutil.copy2(source, stored_source)

            now = _utc_now()
            snapshot = {
                "schema": "book_job_v1",
                "job_id": identifier,
                "revision": 1,
                "created_at": now,
                "updated_at": now,
                "state": "created",
                "failed_stage": None,
                "source": {
                    "filename": source.name,
                    "media_type": _media_type(source),
                    "sha256": _sha256(stored_source),
                    "size_bytes": stored_source.stat().st_size,
                },
                "request": {
                    "processing_mode": mode,
                    "source_language": source_language,
                    "target_language": target_language,
                    "translator": translator,
                    "output_format": output_format,
                },
                "resolved": {
                    "source_language": None,
                    "text_operation": None,
                },
                "progress": {
                    "stage_percent": 0,
                    "overall_percent": 0,
                    "translation_chunks_total": 0,
                    "translation_chunks_completed": 0,
                    "translation_cache_hits": 0,
                    "translation_attempts": 0,
                    "translation_retries": 0,
                },
                "artifacts": {},
                "error": None,
            }
            self._write_snapshot(directory / "job.json", snapshot)
            self._append_event_unlocked(
                identifier,
                event_type="job_created",
                stage="created",
                data={},
            )
        return deepcopy(snapshot)

    def load(self, job_id: str) -> dict[str, Any]:
        path = self.job_dir(job_id) / "job.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise KeyError(f"Unknown job: {job_id}") from None
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid job snapshot for {job_id}.") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "book_job_v1":
            raise ValueError(f"Invalid job snapshot for {job_id}.")
        return payload

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            snapshot = self.load(job_id)
            if "state" in changes and changes["state"] not in JOB_STATES:
                raise ValueError(f"Unsupported job state: {changes['state']!r}.")
            for key, value in changes.items():
                if key in {"schema", "job_id", "revision", "created_at", "updated_at"}:
                    raise ValueError(f"Job field {key!r} cannot be updated directly.")
                if key in {"progress", "resolved", "artifacts"}:
                    if not isinstance(value, dict):
                        raise TypeError(f"Job field {key!r} must be a mapping.")
                    snapshot[key] = {**snapshot.get(key, {}), **value}
                else:
                    snapshot[key] = value
            snapshot["revision"] += 1
            snapshot["updated_at"] = _utc_now()
            self._write_snapshot(self.job_dir(job_id) / "job.json", snapshot)
            return deepcopy(snapshot)

    def append_event(
        self,
        job_id: str,
        *,
        event_type: str,
        stage: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self.load(job_id)
            return self._append_event_unlocked(
                job_id,
                event_type=event_type,
                stage=stage,
                data=data or {},
            )

    def list_events(self, job_id: str) -> list[dict[str, Any]]:
        path = self.job_dir(job_id) / "events.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            raise KeyError(f"Unknown job: {job_id}") from None
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid event history for {job_id} at line {line_number}."
                ) from exc
            if not isinstance(event, dict) or event.get("schema") != "book_job_event_v1":
                raise ValueError(f"Invalid event history for {job_id} at line {line_number}.")
            events.append(event)
        return events

    def _append_event_unlocked(
        self,
        job_id: str,
        *,
        event_type: str,
        stage: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        events = self.list_events(job_id) if (self.job_dir(job_id) / "events.jsonl").exists() else []
        event = {
            "schema": "book_job_event_v1",
            "sequence": len(events) + 1,
            "time": _utc_now(),
            "job_id": job_id,
            "type": event_type,
            "stage": stage,
            "data": data,
        }
        path = self.job_dir(job_id) / "events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return deepcopy(event)

    @staticmethod
    def _write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class BookJobRunner:
    _OVERALL_PERCENT = {
        "created": 0,
        "ingesting": 5,
        "reconstructing": 15,
        "translating": 25,
        "preserving": 25,
        "validating": 75,
        "pre_review": 85,
        "awaiting_human_review": 90,
    }

    def __init__(self, repository: JobRepository, *, pipeline_runner=None):
        self.repository = repository
        if pipeline_runner is None:
            from pdf_translator.pipeline import run_translation_pipeline

            pipeline_runner = run_translation_pipeline
        self.pipeline_runner = pipeline_runner

    def run(self, job_id: str) -> dict[str, Any]:
        snapshot = self.repository.load(job_id)
        if snapshot["state"] not in {"created", "failed"}:
            raise ValueError(f"Job {job_id} cannot run from state {snapshot['state']!r}.")

        current_stage: str | None = None

        def on_stage(stage: str, data: dict[str, Any]) -> None:
            nonlocal current_stage
            if stage not in JOB_STATES or stage in {"created", "failed", "completed"}:
                raise ValueError(f"Unsupported pipeline job stage: {stage!r}.")
            if current_stage is not None and current_stage != stage:
                self._complete_stage(job_id, current_stage)
            if current_stage != stage:
                self.repository.append_event(
                    job_id,
                    event_type="stage_started",
                    stage=stage,
                    data={},
                )
            current_stage = stage
            resolved: dict[str, Any] = {}
            if "source_language" in data:
                resolved["source_language"] = data["source_language"]
            if "text_operation" in data:
                resolved["text_operation"] = data["text_operation"]
            changes: dict[str, Any] = {
                "state": stage,
                "progress": {
                    "stage_percent": int(data.get("stage_percent", 0)),
                    "overall_percent": self._OVERALL_PERCENT.get(stage, 0),
                },
            }
            if resolved:
                changes["resolved"] = resolved
            self.repository.update(job_id, **changes)

        try:
            settings = self._settings(snapshot)
            artifacts = self.pipeline_runner(settings, on_stage)
            if current_stage is not None:
                self._complete_stage(job_id, current_stage)
            artifact_map = self._artifact_map(job_id, artifacts)
            completed = self.repository.update(
                job_id,
                state="awaiting_human_review",
                failed_stage=None,
                error=None,
                artifacts=artifact_map,
                progress={"stage_percent": 100, "overall_percent": 90},
            )
            self.repository.append_event(
                job_id,
                event_type="review_ready",
                stage="awaiting_human_review",
                data={"artifacts": sorted(artifact_map)},
            )
            return completed
        except Exception as exc:
            failed_stage = current_stage or snapshot.get("failed_stage") or "created"
            error_code, retryable = self._classify_failure(exc, failed_stage)
            self.repository.update(
                job_id,
                state="failed",
                failed_stage=failed_stage,
                error={
                    "code": error_code,
                    "message": f"Job failed during {failed_stage}.",
                    "retryable": retryable,
                    "details": {"stage": failed_stage},
                },
            )
            self.repository.append_event(
                job_id,
                event_type="job_failed",
                stage=failed_stage,
                data={"code": error_code, "retryable": retryable},
            )
            raise

    def resume(self, job_id: str) -> dict[str, Any]:
        snapshot = self.repository.load(job_id)
        if snapshot["state"] != "failed":
            raise ValueError(f"Job {job_id} cannot resume from state {snapshot['state']!r}.")
        self.repository.append_event(
            job_id,
            event_type="job_resumed",
            stage=snapshot.get("failed_stage") or "created",
            data={},
        )
        return self.run(job_id)

    def _settings(self, snapshot: dict[str, Any]):
        from pdf_translator.config import RunSettings

        job_dir = self.repository.job_dir(snapshot["job_id"])
        request = snapshot["request"]
        return RunSettings(
            source_pdf=job_dir / "source" / snapshot["source"]["filename"],
            output_dir=job_dir / "artifacts",
            target_language=request["target_language"],
            source_language=request.get("source_language"),
            translator=request["translator"],
            max_chunk_chars=9000,
            profile_name="book",
            output_format=request["output_format"],
            processing_mode=request["processing_mode"],
        )

    def _complete_stage(self, job_id: str, stage: str) -> None:
        self.repository.update(
            job_id,
            progress={
                "stage_percent": 100,
                "overall_percent": self._OVERALL_PERCENT.get(stage, 0),
            },
        )
        self.repository.append_event(
            job_id,
            event_type="stage_completed",
            stage=stage,
            data={},
        )

    def _artifact_map(self, job_id: str, artifacts: Any) -> dict[str, Any]:
        job_dir = self.repository.job_dir(job_id)
        paths = {
            "manifest": artifacts.manifest_path,
            "normalized_markdown": artifacts.normalized_markdown_path,
            "normalized_json": artifacts.normalized_json_path,
            "profile": artifacts.profile_json_path,
            "reconstructed_markdown": artifacts.reconstructed_markdown_path,
            "translation_input": artifacts.translation_input_markdown_path,
            "translated_markdown": artifacts.translated_markdown_path,
            "book": artifacts.book_json_path,
            "book_markdown": artifacts.book_markdown_path,
            "book_trace": artifacts.book_trace_markdown_path,
            "epub": artifacts.translated_epub_path,
            "pdf": artifacts.translated_pdf_path,
        }
        output_dir = Path(artifacts.output_dir)
        paths.update(
            {
                "translated_chapters": output_dir / "translated-chapters.json",
                "chapter_report": output_dir / "chapter-report.json",
                "segments": output_dir / "segments.json",
                "translated_segments": output_dir / "translated-segments.json",
                "review_items": output_dir / "review_items.json",
                "pre_review": output_dir / "pre_review.json",
                "review_state": output_dir / "review_state.json",
                "review_chapter_marks": output_dir / "review_chapter_marks.json",
            }
        )
        mapped: dict[str, Any] = {}
        for name, path in paths.items():
            if path is None:
                continue
            candidate = Path(path)
            if not candidate.exists():
                continue
            try:
                href = candidate.resolve().relative_to(job_dir).as_posix()
            except ValueError as exc:
                raise ValueError(f"Artifact {candidate} is outside job directory.") from exc
            mapped[name] = {"href": href}
        return mapped

    @staticmethod
    def _classify_failure(exc: Exception, stage: str) -> tuple[str, bool]:
        if stage == "ingesting" and _exception_chain_contains(
            exc,
            (FileNotFoundError, zipfile.BadZipFile),
        ):
            return "invalid_source", False
        try:
            from pdf_translator.guardrails import InputGateError

            if isinstance(exc, InputGateError):
                return "invalid_source", False
        except ImportError:
            pass
        return "job_stage_failed", True


def _exception_chain_contains(exc: BaseException, types: tuple[type[BaseException], ...]) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, types):
            return True
        current = current.__cause__ or current.__context__
    return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_type(path: Path) -> str:
    if path.suffix.lower() == ".epub":
        return "application/epub+zip"
    if path.suffix.lower() == ".pdf":
        return "application/pdf"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
