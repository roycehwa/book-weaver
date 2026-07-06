from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

from config import get_settings
from engine_home import resolve_book_weaver_home

_TRANSLATION_ENV_KEYS = (
    "MINIMAX_API_KEY",
    "LLM_API_KEY",
    "MINIMAX_BASE_URL",
    "LLM_BASE_URL",
    "MINIMAX_MODEL",
    "LLM_MODEL",
    "MINIMAX_HTTP_TIMEOUT_SECONDS",
    "DEEPL_AUTH_KEY",
)


def _merge_env_file(path: Path, environment: dict[str, str]) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in environment:
            environment[key] = value


def _merge_shell_exports(path: Path, environment: dict[str, str], *, keys: tuple[str, ...]) -> None:
    if not path.is_file():
        return
    wanted = set(keys)
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in wanted or environment.get(key):
            continue
        value = value.strip().strip('"').strip("'")
        if value:
            environment[key] = value

_CHAPTER_CONFIRM_BLOCKED_STATES = frozenset({"created", "ingesting", "reconstructing"})
_PIPELINE_LOCKED_STATES = frozenset(
    {
        "created",
        "ingesting",
        "reconstructing",
        "preserving",
        "validating",
        "awaiting_glossary",
        "translating",
        "pre_review",
    }
)
_REVIEW_READY_STATES = frozenset({"awaiting_human_review", "completed", "exporting"})


class JobServiceError(RuntimeError):
    pass


class JobNotFound(JobServiceError):
    pass


class BookJobService:
    def __init__(
        self,
        *,
        project_home: str | Path | None = None,
        jobs_dir: str | Path | None = None,
    ):
        settings = get_settings()
        if project_home is not None:
            # Tests may inject a lightweight fake checkout; production resolves via engine_home.
            self.project_home = Path(project_home).expanduser().resolve()
        else:
            self.project_home = resolve_book_weaver_home()
        self.jobs_dir = Path(
            jobs_dir or settings.BOOKMATE_JOBS_DIR
        ).expanduser().resolve()
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        source_path: Path,
        processing_mode: str,
        source_language: str | None,
        target_language: str,
        translator: str,
        output_format: str,
        ingest_timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        args = [
            "job",
            "create",
            str(source_path),
            "--mode",
            processing_mode,
            "--target-lang",
            target_language,
            "--translator",
            translator,
            "--format",
            output_format,
            "--jobs-dir",
            str(self.jobs_dir),
            "--json",
        ]
        if ingest_timeout_seconds is not None:
            args.extend(["--ingest-timeout-seconds", str(ingest_timeout_seconds)])
        if source_language:
            args.extend(["--source-lang", source_language])
        return self._run_json(args)

    def create_from_existing(
        self,
        job_id: str,
        *,
        processing_mode: str,
        source_language: str | None,
        target_language: str,
        translator: str,
        output_format: str,
        ingest_timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        source_path = self.source_path(job_id)
        return self.create(
            source_path=source_path,
            processing_mode=processing_mode,
            source_language=source_language,
            target_language=target_language,
            translator=translator,
            output_format=output_format,
            ingest_timeout_seconds=ingest_timeout_seconds,
        )

    def execute(self, job_id: str) -> None:
        self._validate_job_id(job_id)
        self._acquire_worker_lock(job_id)
        try:
            self._run(["job", "execute", job_id, "--jobs-dir", str(self.jobs_dir), "--json"])
        finally:
            self._release_worker_lock(job_id)

    def resume(self, job_id: str) -> None:
        self._validate_job_id(job_id)
        self._acquire_worker_lock(job_id)
        try:
            self._run(["job", "resume", job_id, "--jobs-dir", str(self.jobs_dir), "--json"])
        finally:
            self._release_worker_lock(job_id)

    def _worker_lock_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "translation-worker.lock"

    def translation_worker_lock_held(self, job_id: str) -> bool:
        path = self._worker_lock_path(job_id)
        if not path.is_file():
            return False
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            return False

    def _acquire_worker_lock(self, job_id: str) -> None:
        if self.translation_worker_lock_held(job_id):
            raise JobServiceError(f"Translation worker already running for job {job_id}.")
        self._worker_lock_path(job_id).write_text(str(os.getpid()), encoding="utf-8")

    def _release_worker_lock(self, job_id: str) -> None:
        self._worker_lock_path(job_id).unlink(missing_ok=True)

    def delete(self, job_id: str) -> None:
        self._validate_job_id(job_id)
        job_dir = self._job_dir(job_id).resolve()
        jobs_root = self.jobs_dir.resolve()
        if not job_dir.is_dir() or job_dir == jobs_root or jobs_root not in job_dir.parents:
            raise JobNotFound(f"Job not found: {job_id}")
        shutil.rmtree(job_dir)

    @staticmethod
    def _last_translation_job_status(events_path: Path) -> str | None:
        if not events_path.is_file():
            return None
        for line in reversed(events_path.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("event") != "job_finished":
                continue
            status = event.get("status")
            return str(status) if isinstance(status, str) else None
        return None

    def _last_resume_request_at(self, job_id: str) -> datetime | None:
        path = self._job_dir(job_id) / "last-resume-request.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        raw = payload.get("requested_at") if isinstance(payload, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            return None
        normalized = raw.replace("Z", "+00:00")
        try:
            requested_at = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=timezone.utc)
        return requested_at

    def record_resume_request(self, job_id: str) -> None:
        path = self._job_dir(job_id) / "last-resume-request.json"
        path.write_text(
            json.dumps(
                {"requested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _resume_cooldown_remaining(self, job_id: str, *, cooldown_seconds: int = 120) -> int:
        requested_at = self._last_resume_request_at(job_id)
        if requested_at is None:
            return 0
        elapsed = int((datetime.now(timezone.utc) - requested_at).total_seconds())
        return max(cooldown_seconds - elapsed, 0)

    def _maybe_reconcile_stale_translating(
        self,
        snapshot: dict[str, Any],
        activity: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if str(snapshot.get("state") or "") != "translating":
            return snapshot
        job_id = str(snapshot.get("job_id") or "")
        if not job_id or self.translation_worker_lock_held(job_id):
            return snapshot
        if not activity or activity.get("status") != "failed":
            return snapshot
        last_error = str(activity.get("last_error") or "翻译进程已停止。")
        path = self._job_dir(job_id) / "job.json"
        merged = dict(snapshot)
        merged["state"] = "failed"
        merged["failed_stage"] = "translating"
        merged["error"] = {
            "code": "job_stage_failed",
            "message": "Job failed during translating.",
            "retryable": True,
            "details": {"stage": "translating", "reason": last_error},
        }
        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return merged

    def get(self, job_id: str) -> dict[str, Any]:
        path = self._job_dir(job_id) / "job.json"
        if not path.is_file():
            raise JobNotFound(f"Job not found: {job_id}")
        snapshot = self._read_json(path, expected_schema="book_job_v1")
        stale_derived = {
            key: snapshot.pop(key)
            for key in ("translation_activity", "translation_resume")
            if key in snapshot
        }
        if stale_derived:
            path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        snapshot = self._merge_live_translation_progress(snapshot)
        activity = self.translation_activity(snapshot)
        snapshot = self._maybe_reconcile_stale_translating(snapshot, activity)
        if snapshot.get("state") != "translating" and activity and activity.get("status") == "failed":
            activity = self.translation_activity(snapshot)
        resume = self.translation_resume(snapshot, activity)
        enriched = dict(snapshot)
        if activity is not None:
            enriched["translation_activity"] = activity
        if resume is not None:
            enriched["translation_resume"] = resume
        return enriched

    def _translation_cache_stats(self, job_id: str) -> tuple[int, datetime | None]:
        cache_dirs = sorted(
            self._job_dir(job_id).glob("artifacts/*/translation-cache"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not cache_dirs:
            return 0, None
        indices: set[int] = set()
        latest_mtime: datetime | None = None
        for path in cache_dirs[0].glob("chunk-*.md"):
            match = re.match(r"chunk-(\d+)-", path.name)
            if not match:
                continue
            indices.add(int(match.group(1)))
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if latest_mtime is None or mtime > latest_mtime:
                latest_mtime = mtime
        return len(indices), latest_mtime

    def _translation_cache_chunk_count(self, job_id: str) -> int:
        count, _ = self._translation_cache_stats(job_id)
        return count

    def _merge_live_translation_progress(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not self._is_translate_job(snapshot):
            return snapshot
        job_id = str(snapshot.get("job_id") or "")
        progress_path = self._translation_progress_path(job_id)
        cache_completed = self._translation_cache_chunk_count(job_id) if job_id else 0
        if progress_path is None:
            if cache_completed <= 0:
                return snapshot
            live: dict[str, Any] = {}
        else:
            try:
                live = self._read_json_any(progress_path)
            except JobServiceError:
                live = {}
        merged = dict(snapshot)
        progress = dict(merged.get("progress") or {})
        total = max(int(live.get("total_chunks") or 0), int(progress.get("translation_chunks_total") or 0), 0)
        completed = max(int(live.get("completed_chunks") or 0), int(progress.get("translation_chunks_completed") or 0), cache_completed)
        if total:
            completed = min(completed, total)
            progress["translation_chunks_total"] = total
            progress["translation_chunks_completed"] = completed
            progress["translation_cache_hits"] = max(int(live.get("cache_hit_chunks") or 0), cache_completed)
            progress["translation_retries"] = int(live.get("retrying_chunks") or 0)
            state = str(snapshot.get("state") or "")
            failed_stage = str(snapshot.get("failed_stage") or "")
            translating_like = state in {"translating", "preserving"} or (
                state == "failed" and failed_stage in {"translating", "preserving"} and completed > 0
            )
            if translating_like:
                base = 25
                span = 50 - base
                progress["stage_percent"] = int(completed / total * 100)
                progress["overall_percent"] = base + span * completed // total
            elif state in {"exporting", "awaiting_human_review", "completed", "pre_review", "validating"}:
                progress["translation_chunks_completed"] = total
                progress["stage_percent"] = 100
        elif cache_completed > 0:
            progress["translation_chunks_completed"] = cache_completed
        merged["progress"] = progress
        return merged

    @staticmethod
    def _is_translate_job(snapshot: dict[str, Any]) -> bool:
        resolved = snapshot.get("resolved") if isinstance(snapshot.get("resolved"), dict) else {}
        request = snapshot.get("request") if isinstance(snapshot.get("request"), dict) else {}
        return (
            resolved.get("text_operation") == "translate"
            or request.get("processing_mode") == "translate"
        )

    def _translation_progress_path(self, job_id: str) -> Path | None:
        progress_paths = sorted(
            self._job_dir(job_id).glob("artifacts/*/jobs/progress.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return progress_paths[0] if progress_paths else None

    @staticmethod
    def _last_translation_failure(events_path: Path) -> str | None:
        if not events_path.is_file():
            return None
        for line in reversed(events_path.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("event") != "attempt_failure":
                continue
            message = event.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
            error_type = event.get("error_type")
            if isinstance(error_type, str) and error_type.strip():
                return error_type.strip()
        return None

    def translation_activity(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        state = str(snapshot.get("state") or "")
        failed_stage = str(snapshot.get("failed_stage") or "")
        if not self._is_translate_job(snapshot):
            return None
        if state not in {"failed", "translating"}:
            return None
        if state == "failed" and failed_stage not in {"translating", "preserving"}:
            return None

        job_id = str(snapshot.get("job_id") or "")
        progress_path = self._translation_progress_path(job_id)
        cache_completed, cache_last_mtime = (
            self._translation_cache_stats(job_id) if job_id else (0, None)
        )
        if progress_path is None and cache_completed <= 0:
            return None
        if progress_path is not None:
            try:
                progress = self._read_json_any(progress_path)
            except JobServiceError:
                progress = {}
        else:
            progress = {}

        events_path = progress_path.parent / "translation-events.jsonl" if progress_path else None
        last_error = self._last_translation_failure(events_path) if events_path else None

        updated_at_raw = progress.get("updated_at")
        updated_at = None
        if isinstance(updated_at_raw, str) and updated_at_raw.strip():
            normalized = updated_at_raw.replace("Z", "+00:00")
            try:
                updated_at = datetime.fromisoformat(normalized)
            except ValueError:
                updated_at = None

        now = datetime.now(timezone.utc)
        seconds_since_update = None
        if updated_at is not None:
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            seconds_since_update = max(int((now - updated_at).total_seconds()), 0)

        running_chunks = int(progress.get("running_chunks") or 0)
        snapshot_progress = snapshot.get("progress") if isinstance(snapshot.get("progress"), dict) else {}
        completed = max(
            int(progress.get("completed_chunks") or 0),
            int(snapshot_progress.get("translation_chunks_completed") or 0),
            cache_completed,
        )
        total = max(
            int(progress.get("total_chunks") or 0),
            int(snapshot_progress.get("translation_chunks_total") or 0),
        )
        progress_status = str(progress.get("status") or "unknown")
        last_job_status = (
            self._last_translation_job_status(events_path) if events_path else None
        )

        cache_recent = False
        if cache_last_mtime is not None:
            cache_recent = int((now - cache_last_mtime).total_seconds()) <= 180
        cache_ahead = cache_completed > int(progress.get("completed_chunks") or 0)

        if self.translation_worker_lock_held(job_id):
            activity_status = "waiting" if running_chunks > 0 else "active"
        elif cache_recent or cache_ahead:
            activity_status = "active"
        elif state == "failed":
            activity_status = "failed"
        elif progress_status == "failed" or last_job_status == "failed":
            activity_status = "failed"
        elif seconds_since_update is None:
            activity_status = "unknown"
        elif seconds_since_update <= 120:
            activity_status = "active"
        elif running_chunks > 0 and seconds_since_update <= 600:
            activity_status = "waiting"
        elif seconds_since_update <= 180:
            activity_status = "active"
        else:
            activity_status = "stalled"

        last_event_at = None
        if events_path is not None and events_path.is_file():
            for line in reversed(events_path.read_text(encoding="utf-8").splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and isinstance(event.get("timestamp"), str):
                    last_event_at = event["timestamp"]
                    break

        return {
            "status": activity_status,
            "progress_status": progress_status,
            "updated_at": updated_at_raw if isinstance(updated_at_raw, str) else None,
            "last_event_at": last_event_at,
            "seconds_since_update": seconds_since_update,
            "running_chunks": running_chunks,
            "completed_chunks": completed,
            "total_chunks": total,
            "last_error": last_error,
        }

    def translation_resume(
        self,
        snapshot: dict[str, Any],
        activity: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self._is_translate_job(snapshot):
            return None

        state = str(snapshot.get("state") or "")
        failed_stage = str(snapshot.get("failed_stage") or "")
        error = snapshot.get("error") if isinstance(snapshot.get("error"), dict) else {}
        job_id = str(snapshot.get("job_id") or "")

        if job_id and self.translation_worker_lock_held(job_id):
            return {
                "available": False,
                "reason": "already_running",
                "detail": "翻译引擎正在后台运行，请等待当前批次完成后再试。",
            }

        cooldown = self._resume_cooldown_remaining(job_id)
        if cooldown > 0:
            return {
                "available": False,
                "reason": "cooldown",
                "detail": f"刚刚已提交恢复请求，请等待约 {cooldown} 秒后再试，避免重复调用 API。",
            }

        if state == "failed":
            if error.get("retryable") and failed_stage in {"translating", "preserving"}:
                detail = str(error.get("message") or "翻译阶段失败，可从断点继续。")
                if activity and activity.get("last_error"):
                    detail = str(activity["last_error"])
                return {
                    "available": True,
                    "label": "从检查点恢复",
                    "reason": "translation_failed",
                    "detail": detail,
                }
            return {
                "available": False,
                "reason": "not_retryable",
                "detail": str(error.get("message") or "当前失败不可自动恢复。"),
            }

        if state == "translating":
            act = activity if activity is not None else self.translation_activity(snapshot)
            if act and act.get("status") in {"waiting", "active"}:
                if act.get("status") == "waiting":
                    detail = (
                        f"有 {int(act.get('running_chunks') or 0)} 个翻译块正在等待模型响应，"
                        "请耐心等待，无需重复启动。"
                    )
                else:
                    detail = "翻译引擎正在运行，页面会自动刷新进度，请勿重复启动。"
                return {
                    "available": False,
                    "reason": "already_running",
                    "detail": detail,
                }
            detail = "翻译进程已停止或无响应，可从已完成块继续。"
            if act and act.get("last_error"):
                detail = f"{detail} 最近错误：{act['last_error']}"
            reason = "stalled" if act and act.get("status") == "stalled" else "unknown"
            return {
                "available": True,
                "label": "继续翻译（从断点）",
                "reason": reason,
                "detail": detail,
            }

        return {
            "available": False,
            "reason": "wrong_state",
            "detail": "当前阶段不需要恢复翻译。",
        }

    def source_path(self, job_id: str) -> Path:
        snapshot = self.get(job_id)
        source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
        filename = source.get("filename")
        if not isinstance(filename, str) or not filename:
            raise JobNotFound(f"Source file not found for job: {job_id}")
        job_dir = self._job_dir(job_id).resolve()
        path = (job_dir / "source" / filename).resolve()
        source_dir = (job_dir / "source").resolve()
        if not path.is_relative_to(source_dir) or not path.is_file():
            raise JobNotFound(f"Source file not found for job: {job_id}")
        return path

    def list(self) -> list[dict[str, Any]]:
        snapshots = []
        for path in self.jobs_dir.glob("*/job.json"):
            try:
                snapshots.append(self._read_json(path, expected_schema="book_job_v1"))
            except JobServiceError:
                continue
        return sorted(
            snapshots,
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )

    def events(self, job_id: str) -> list[dict[str, Any]]:
        path = self._job_dir(job_id) / "events.jsonl"
        if not path.is_file():
            raise JobNotFound(f"Job not found: {job_id}")
        events = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JobServiceError(
                    f"Invalid event history at line {line_number}."
                ) from exc
            if not isinstance(event, dict) or event.get("schema") != "book_job_event_v1":
                raise JobServiceError(f"Invalid event history at line {line_number}.")
            events.append(event)
        return events

    def artifact_path(self, job_id: str, artifact_name: str) -> Path:
        snapshot = self.get(job_id)
        artifact = snapshot.get("artifacts", {}).get(artifact_name)
        href = artifact.get("href") if isinstance(artifact, dict) else None
        if not isinstance(href, str):
            raise JobNotFound(f"Artifact not found: {artifact_name}")
        job_dir = self._job_dir(job_id).resolve()
        path = (job_dir / href).resolve()
        if not path.is_relative_to(job_dir) or not path.is_file():
            raise JobNotFound(f"Artifact not found: {artifact_name}")
        return path

    def chapter_draft(
        self,
        job_id: str,
        *,
        toc_page_start: int | None = None,
        toc_page_end: int | None = None,
        page_offset: int | None = None,
        toc_depth: int | None = None,
        persist_prefs: bool = False,
    ) -> dict[str, Any]:
        if persist_prefs and any(
            value is not None
            for value in (toc_page_start, toc_page_end, page_offset, toc_depth)
        ):
            self._save_chapter_draft_prefs(
                job_id,
                toc_page_start=toc_page_start,
                toc_page_end=toc_page_end,
                page_offset=page_offset,
                toc_depth=toc_depth,
            )
        chapters, draft_source, draft_source_detail, meta = self._resolve_draft_chapters(
            job_id,
            toc_page_start=toc_page_start,
            toc_page_end=toc_page_end,
            page_offset=page_offset,
            toc_depth=toc_depth,
        )
        snapshot = self.get(job_id)
        prefs = self._chapter_draft_prefs(snapshot)
        return {
            "job_id": job_id,
            "chapters": chapters,
            "draft_source": draft_source,
            "draft_source_detail": draft_source_detail,
            "suggested_page_offset": int(
                meta.get("page_offset")
                if meta.get("page_offset") is not None
                else snapshot.get("chapter_page_offset")
                or prefs.get("page_offset")
                or 0
            ),
            "toc_page_start": meta.get("toc_page_start") or prefs.get("toc_page_start"),
            "toc_page_end": meta.get("toc_page_end") or prefs.get("toc_page_end"),
            "page_offset": meta.get("page_offset"),
            "toc_depth": meta.get("toc_depth") if meta.get("toc_depth") is not None else prefs.get("toc_depth", 1),
        }

    def update_chapter_draft_prefs(
        self,
        job_id: str,
        *,
        toc_page_start: int | None = None,
        toc_page_end: int | None = None,
        page_offset: int | None = None,
        toc_depth: int | None = None,
    ) -> dict[str, Any]:
        self._save_chapter_draft_prefs(
            job_id,
            toc_page_start=toc_page_start,
            toc_page_end=toc_page_end,
            page_offset=page_offset,
            toc_depth=toc_depth,
        )
        return self.chapter_draft(job_id)

    def _chapter_draft_prefs(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        prefs = snapshot.get("chapter_draft_prefs")
        return prefs if isinstance(prefs, dict) else {}

    def _save_chapter_draft_prefs(
        self,
        job_id: str,
        *,
        toc_page_start: int | None = None,
        toc_page_end: int | None = None,
        page_offset: int | None = None,
        toc_depth: int | None = None,
    ) -> None:
        snapshot = self.get(job_id)
        prefs = self._chapter_draft_prefs(snapshot)
        if toc_page_start is not None:
            prefs["toc_page_start"] = int(toc_page_start)
        if toc_page_end is not None:
            prefs["toc_page_end"] = int(toc_page_end)
        if page_offset is not None:
            prefs["page_offset"] = int(page_offset)
            snapshot["chapter_page_offset"] = int(page_offset)
        if toc_depth is not None:
            prefs["toc_depth"] = int(toc_depth)
        snapshot["chapter_draft_prefs"] = prefs
        snapshot["updated_at"] = (
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        snapshot["revision"] = int(snapshot.get("revision") or 0) + 1
        self._write_json_atomic(self._job_dir(job_id) / "job.json", snapshot)

    def draft_chapters(self, job_id: str) -> list[dict[str, Any]]:
        chapters, _, _, _ = self._resolve_draft_chapters(job_id)
        return chapters

    def _resolve_draft_chapters(
        self,
        job_id: str,
        *,
        toc_page_start: int | None = None,
        toc_page_end: int | None = None,
        page_offset: int | None = None,
        toc_depth: int | None = None,
    ) -> tuple[list[dict[str, Any]], str, str | None, dict[str, Any]]:
        snapshot = self.get(job_id)
        prefs = self._chapter_draft_prefs(snapshot)
        resolved_toc_start = toc_page_start if toc_page_start is not None else prefs.get("toc_page_start")
        resolved_toc_end = toc_page_end if toc_page_end is not None else prefs.get("toc_page_end")
        resolved_offset = (
            page_offset
            if page_offset is not None
            else prefs.get("page_offset", snapshot.get("chapter_page_offset"))
        )
        resolved_toc_depth = toc_depth if toc_depth is not None else prefs.get("toc_depth", 1)
        meta: dict[str, Any] = {"toc_depth": resolved_toc_depth}

        canonical_artifact = snapshot.get("artifacts", {}).get("canonical_chapters")
        canonical_href = canonical_artifact.get("href") if isinstance(canonical_artifact, dict) else None
        if isinstance(canonical_href, str):
            job_dir = self._job_dir(job_id).resolve()
            canonical_path = (job_dir / canonical_href).resolve()
            if canonical_path.is_relative_to(job_dir) and canonical_path.is_file():
                canonical_payload = self._read_json_any(canonical_path)
                canonical_chapters = (
                    canonical_payload.get("chapters")
                    if isinstance(canonical_payload, dict)
                    else None
                )
                if isinstance(canonical_chapters, list) and canonical_chapters:
                    canonical = [
                        self._canonical_chapter(chapter, index)
                        for index, chapter in enumerate(canonical_chapters, start=1)
                        if isinstance(chapter, dict)
                    ]
                    if canonical:
                        return canonical, "canonical_saved", "已保存的章节目录", meta

        force_text_toc = (
            resolved_toc_start is not None
            or resolved_toc_end is not None
            or int(resolved_toc_depth or 1) > 1
        )
        if not force_text_toc:
            embedded_toc, embedded_meta = self._pdf_embedded_toc_draft_chapters(job_id)
            if embedded_toc:
                meta.update(embedded_meta)
                return embedded_toc, "pdf_toc", "PDF 内置目录", meta

        text_toc, text_meta = self._pdf_text_toc_draft_chapters(
            job_id,
            toc_page_start=resolved_toc_start,
            toc_page_end=resolved_toc_end,
            page_offset=resolved_offset,
            toc_depth=resolved_toc_depth,
        )
        if text_toc:
            meta.update(text_meta)
            detail = "PDF 目录页文本"
            if text_meta.get("toc_page_start") and text_meta.get("toc_page_end"):
                detail += f"（第 {text_meta['toc_page_start']}–{text_meta['toc_page_end']} 页）"
            return text_toc, "pdf_text_toc", detail, meta

        book_path = self.artifact_path(job_id, "book")
        book = self._read_json_any(book_path)
        chapters = book.get("chapters") if isinstance(book, dict) else None
        if not isinstance(chapters, list) or not chapters:
            raise JobServiceError("Book structure does not contain chapters.")
        canonical = [
            self._canonical_chapter(chapter, index)
            for index, chapter in enumerate(chapters, start=1)
            if isinstance(chapter, dict)
        ]
        if not canonical:
            raise JobServiceError("Book structure does not contain valid chapters.")
        return canonical, "book_structure", "解析阶段生成的章节结构", meta

    def _pdf_embedded_toc_draft_chapters(
        self,
        job_id: str,
    ) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
        chapters = self._pdf_toc_draft_chapters(job_id)
        return chapters, {}

    def _pdf_text_toc_draft_chapters(
        self,
        job_id: str,
        *,
        toc_page_start: int | None,
        toc_page_end: int | None,
        page_offset: int | None,
        toc_depth: int | None = 1,
    ) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
        try:
            source = self.source_path(job_id)
        except JobNotFound:
            return None, {}
        if source.suffix.lower() != ".pdf":
            return None, {}
        try:
            from toc_text_parser import extract_text_toc_from_pdf

            depth_value = int(toc_depth if toc_depth is not None else 1)
            max_depth = None if depth_value <= 0 else depth_value
            result = extract_text_toc_from_pdf(
                str(source),
                page_start=int(toc_page_start) if toc_page_start is not None else None,
                page_end=int(toc_page_end) if toc_page_end is not None else None,
                page_offset=int(page_offset) if page_offset is not None else None,
                max_depth=max_depth,
            )
            if not result:
                return None, {}
            chapters: list[dict[str, Any]] = []
            for index, chapter in enumerate(result["chapters"], start=1):
                start_page = chapter.get("start_page")
                end_page = chapter.get("end_page")
                source_pages = chapter.get("source_pages")
                if not isinstance(source_pages, list):
                    source_pages = []
                    if isinstance(start_page, int) and isinstance(end_page, int) and end_page >= start_page:
                        source_pages = list(range(start_page, end_page + 1))
                chapters.append(
                    {
                        "index": index,
                        "chapter_id": f"toc-text-{index:03d}",
                        "title": str(chapter.get("title") or f"Chapter {index}"),
                        "page_start": start_page,
                        "page_end": end_page,
                        "source_pages": source_pages,
                        "printed_page": chapter.get("printed_page"),
                    }
                )
            meta = {
                "toc_page_start": result.get("toc_page_start"),
                "toc_page_end": result.get("toc_page_end"),
                "page_offset": result.get("page_offset"),
                "toc_depth": depth_value,
            }
            return chapters or None, meta
        except Exception:
            return None, {}

    def _pdf_toc_draft_chapters(self, job_id: str) -> list[dict[str, Any]] | None:
        try:
            source = self.source_path(job_id)
        except JobNotFound:
            return None
        if source.suffix.lower() != ".pdf":
            return None
        try:
            import fitz
            from toc_extractor import PDFTOCFetcher

            fetcher = PDFTOCFetcher()
            toc_items = fetcher.extract_toc(str(source))
            if not toc_items:
                return None
            doc = fitz.open(str(source))
            total_pages = doc.page_count
            doc.close()
            raw = fetcher.toc_to_chapters(toc_items, total_pages)
            if not raw:
                return None
            chapters: list[dict[str, Any]] = []
            for index, chapter in enumerate(raw, start=1):
                start_page = chapter.get("start_page")
                end_page = chapter.get("end_page")
                source_pages: list[int] = []
                if isinstance(start_page, int) and isinstance(end_page, int) and end_page >= start_page:
                    source_pages = list(range(start_page, end_page + 1))
                chapters.append(
                    {
                        "index": index,
                        "chapter_id": f"toc-{index:03d}",
                        "title": str(chapter.get("title") or f"Chapter {index}"),
                        "page_start": start_page,
                        "page_end": end_page,
                        "source_pages": source_pages,
                    }
                )
            return chapters or None
        except Exception:
            return None

    def confirm_chapters(
        self,
        job_id: str,
        *,
        chapters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        snapshot = self.get(job_id)
        state = str(snapshot.get("state") or "")
        if state in _CHAPTER_CONFIRM_BLOCKED_STATES:
            raise JobServiceError("章节确认需等待结构解析完成后再进行。")
        source_artifact = "user_confirmation" if chapters is not None else "book"
        source_chapters = chapters if chapters is not None else self.draft_chapters(job_id)
        if not isinstance(source_chapters, list) or not source_chapters:
            raise JobServiceError("Chapter confirmation requires at least one chapter.")

        canonical = {
            "schema": "bookmate_canonical_chapters_v1",
            "job_id": job_id,
            "source_artifact": source_artifact,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "chapters": [
                self._canonical_chapter(chapter, index)
                for index, chapter in enumerate(source_chapters, start=1)
                if isinstance(chapter, dict)
            ],
        }
        if not canonical["chapters"]:
            raise JobServiceError("Book structure does not contain valid chapters.")

        job_dir = self._job_dir(job_id)
        canonical_path = job_dir / "artifacts" / "canonical-chapters.json"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(canonical_path, canonical)

        snapshot.setdefault("artifacts", {})["canonical_chapters"] = {
            "href": canonical_path.relative_to(job_dir).as_posix()
        }
        snapshot["updated_at"] = canonical["created_at"]
        snapshot["revision"] = int(snapshot.get("revision") or 0) + 1
        self._write_json_atomic(job_dir / "job.json", snapshot)
        return snapshot

    def review_run_dir(self, job_id: str) -> Path:
        snapshot = self.get(job_id)
        state = str(snapshot.get("state") or "")
        if state not in _REVIEW_READY_STATES:
            raise JobNotFound("Review is not available until translation and pre-review complete.")
        run_dir = self.artifact_path(job_id, "review_items").parent
        required = [
            "segments.json",
            "translated_segments.json",
            "review_items.json",
            "review_state.json",
        ]
        if any(not (run_dir / name).is_file() for name in required):
            raise JobNotFound("Review artifacts are not ready.")
        if run_dir.name != run_dir.name.strip():
            alias = self._job_dir(job_id) / "review"
            if alias.is_symlink() and alias.resolve() != run_dir.resolve():
                alias.unlink()
            if not alias.exists():
                alias.symlink_to(run_dir, target_is_directory=True)
            return alias
        return run_dir

    def glossary_workflow(self, job_id: str) -> dict[str, Any] | None:
        workflow_path = self._run_dir(job_id) / "workflow.json"
        if not workflow_path.is_file():
            return None
        workflow = self._read_json_any(workflow_path)
        return workflow if isinstance(workflow, dict) else None

    def glossary_workflow_stage(self, job_id: str) -> str | None:
        workflow = self.glossary_workflow(job_id)
        stage = workflow.get("stage") if isinstance(workflow, dict) else None
        if isinstance(stage, str) and stage:
            return str(stage)
        return None

    @staticmethod
    def _glossary_suggest_status(run_dir: Path) -> dict[str, Any]:
        glossary_dir = run_dir / "glossary"
        running_path = glossary_dir / "suggest-running.json"
        if running_path.is_file():
            try:
                payload = json.loads(running_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {"status": "running"}
            if isinstance(payload, dict):
                return payload

        report_path = glossary_dir / "suggestions.json"
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                report = {}
            if isinstance(report, dict):
                return {
                    "status": "idle",
                    "last_generated_at": report.get("generated_at"),
                    "suggested_count": report.get("suggested_count"),
                    "candidate_count": report.get("candidate_count"),
                    "glossary_suggest_strategy": report.get("glossary_suggest_strategy"),
                    "deepl_fallback_count": report.get("deepl_fallback_count"),
                    "skipped_locked_count": report.get("skipped_locked_count"),
                    "suggest_scope": report.get("suggest_scope"),
                }
        return {"status": "idle"}

    @staticmethod
    def _with_effective_suggest_strategy(policy: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(policy, dict):
            return policy
        try:
            from pdf_translator.glossary_suggestions import (
                _deepl_available,
                _resolve_suggest_strategy,
                effective_suggest_strategy_label,
                glossary_deepl_trigger_rules,
            )

            effective = _resolve_suggest_strategy(policy, primary_translator="minimax")
            deepl_configured = _deepl_available(primary_translator="minimax")
            return {
                **policy,
                "glossary_suggest_strategy_effective": effective,
                "glossary_suggest_strategy_label": effective_suggest_strategy_label(
                    policy,
                    primary_translator="minimax",
                ),
                "deepl_configured": deepl_configured,
                "deepl_trigger_rules": glossary_deepl_trigger_rules(
                    effective_strategy=effective,
                    deepl_configured=deepl_configured,
                ),
            }
        except Exception:
            return policy

    def glossary_reset_review(self, job_id: str, *, clear_suggestions: bool = True) -> dict[str, Any]:
        run_dir = self._require_awaiting_glossary(job_id)
        args = [
            "glossary",
            "reset-review",
            str(run_dir),
        ]
        if not clear_suggestions:
            args.append("--keep-suggestions")
        args.extend(["--decided-by", "user"])
        self._run(args)
        return self.glossary(job_id)

    def glossary_clear_suggestions(self, job_id: str) -> dict[str, Any]:
        run_dir = self._require_awaiting_glossary(job_id)
        self._run(["glossary", "clear-suggestions", str(run_dir)])
        return self.glossary(job_id)

    def glossary(self, job_id: str) -> dict[str, Any]:
        snapshot = self.get(job_id)
        artifacts = snapshot.get("artifacts") if isinstance(snapshot.get("artifacts"), dict) else {}
        run_dir = self._run_dir(job_id)
        workflow = None
        workflow_path = run_dir / "workflow.json"
        if workflow_path.is_file():
            workflow = self._read_json_any(workflow_path)
        policy = None
        policy_path = run_dir / "glossary" / "extraction-policy.json"
        if policy_path.is_file():
            policy = self._read_json_any(policy_path)

        candidates: list[dict[str, Any]] = []
        if "glossary_candidates" in artifacts:
            try:
                payload = self._read_json_any(self.artifact_path(job_id, "glossary_candidates"))
                raw = payload.get("candidates")
                candidates = raw if isinstance(raw, list) else []
            except JobNotFound:
                candidates = []
        excluded_sources_list = (
            [item for item in (policy.get("excluded_sources") or []) if isinstance(item, str)]
            if isinstance(policy, dict)
            else []
        )
        excluded_sources = set(excluded_sources_list)
        excluded_candidates: list[dict[str, Any]] = []
        if excluded_sources and candidates:
            kept: list[dict[str, Any]] = []
            for entry in candidates:
                if not isinstance(entry, dict):
                    kept.append(entry)
                    continue
                source = entry.get("source") or ""
                if source in excluded_sources:
                    excluded_candidates.append(entry)
                else:
                    kept.append(entry)
            candidates = kept

        active_payload = None
        active_entries: list[dict[str, Any]] = []
        if "glossary_active" in artifacts:
            try:
                active_payload = self._read_json_any(self.artifact_path(job_id, "glossary_active"))
                raw_entries = active_payload.get("entries")
                active_entries = raw_entries if isinstance(raw_entries, list) else []
            except JobNotFound:
                active_entries = []

        active_count = sum(
            1
            for entry in active_entries
            if isinstance(entry, dict) and entry.get("status") == "active"
        )

        profile_summary = None
        if isinstance(policy, dict) and policy.get("glossary_profile"):
            profile_summary = {
                "id": policy.get("glossary_profile"),
                "label": policy.get("glossary_profile_label"),
                "source": policy.get("glossary_profile_source"),
                "confidence": policy.get("glossary_profile_confidence"),
                "overridden": bool(policy.get("glossary_profile_overridden")),
                "humanities_subhints": policy.get("humanities_subhints") or [],
            }

        policy = self._with_effective_suggest_strategy(policy)
        suggest_status = self._glossary_suggest_status(run_dir)

        return {
            "schema": "phase_a_glossary_v1",
            "updated_at": active_payload.get("updated_at") if active_payload else None,
            "candidates": candidates,
            "excluded_candidates": excluded_candidates,
            "excluded_sources": excluded_sources_list,
            "entries": active_entries,
            "status": {
                "candidate_count": len(candidates),
                "active_count": active_count,
                "entry_count": len(active_entries),
                "excluded_count": len(excluded_candidates),
            },
            "workflow": workflow,
            "policy": policy,
            "profile": profile_summary,
            "suggest_status": suggest_status,
        }

    def glossary_apply(
        self,
        job_id: str,
        *,
        source: str,
        target: str | None,
        term_type: str,
        status: str,
    ) -> dict[str, Any]:
        run_dir = self._run_dir(job_id)
        args = [
            "glossary",
            "apply",
            str(run_dir),
            "--source",
            source,
            "--type",
            term_type,
            "--status",
            status,
        ]
        if target is not None:
            args.extend(["--target", target])
        self._run(args)
        return self.glossary(job_id)

    def glossary_ready(self, job_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(job_id)
        self._run(["glossary", "ready", str(run_dir)])
        return self.glossary(job_id)

    def _require_awaiting_glossary(self, job_id: str) -> Path:
        snapshot = self.get(job_id)
        state = str(snapshot.get("state") or "")
        run_dir = self._run_dir(job_id)
        workflow_stage = None
        workflow_path = run_dir / "workflow.json"
        if workflow_path.is_file():
            workflow = self._read_json_any(workflow_path)
            workflow_stage = workflow.get("stage")
        if state != "awaiting_glossary" and workflow_stage != "awaiting_glossary":
            raise JobServiceError(
                "Glossary profile can only be changed while the job is awaiting glossary finalization."
            )
        return run_dir

    def glossary_profile(self, job_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(job_id)
        policy_path = run_dir / "glossary" / "extraction-policy.json"
        if not policy_path.is_file():
            raise JobNotFound(f"Glossary policy not found for job {job_id}")
        policy = self._read_json_any(policy_path)
        return {
            "schema": "phase_a_glossary_profile_v1",
            "profile": policy.get("glossary_profile"),
            "label": policy.get("glossary_profile_label"),
            "source": policy.get("glossary_profile_source"),
            "confidence": policy.get("glossary_profile_confidence"),
            "overridden": bool(policy.get("glossary_profile_overridden")),
            "scores": policy.get("glossary_profile_scores") or {},
            "humanities_subhints": policy.get("humanities_subhints") or [],
            "options": [
                {"id": "humanities_history", "label": "人文·历史·艺术"},
                {"id": "social_econ_philosophy", "label": "社会·经济·哲学"},
                {"id": "science_tech_engineering", "label": "科学·技术·工程"},
                {"id": "formal_logic_philosophy", "label": "逻辑·语言哲学"},
            ],
        }

    def glossary_set_profile(self, job_id: str, profile: str) -> dict[str, Any]:
        valid = {
            "humanities_history",
            "social_econ_philosophy",
            "science_tech_engineering",
            "formal_logic_philosophy",
        }
        if profile not in valid:
            raise JobServiceError(f"Unsupported glossary profile: {profile}")
        run_dir = self._require_awaiting_glossary(job_id)
        self._run(
            [
                "glossary",
                "extract",
                str(run_dir),
                "--profile",
                profile,
                "--profile-source",
                "user",
            ]
        )
        return self.glossary(job_id)

    def glossary_exclude(
        self,
        job_id: str,
        *,
        source: str,
        action: str = "exclude",
    ) -> dict[str, Any]:
        """Add or remove a term from the persistent exclusion list.

        ``action="exclude"`` records ``source`` in the
        ``extraction-policy.json`` ``excluded_sources`` array so that
        future candidate regeneration will skip it. The change takes
        effect immediately for the current candidate view as well.

        ``action="restore"`` removes ``source`` from the exclusion list
        and surfaces it again as a candidate.
        """
        run_dir = self._require_awaiting_glossary(job_id)
        glossary_dir = run_dir / "glossary"
        policy_path = glossary_dir / "extraction-policy.json"
        if not policy_path.is_file():
            policy = {}
        else:
            policy = self._read_json_any(policy_path) or {}

        excluded = list(policy.get("excluded_sources") or [])
        if action == "exclude":
            if source not in excluded:
                excluded.append(source)
        elif action == "restore":
            if source in excluded:
                excluded = [item for item in excluded if item != source]
        else:
            raise JobServiceError(f"Unknown exclude action: {action}")

        policy["excluded_sources"] = excluded
        policy["excluded_sources_updated_at"] = datetime.now(timezone.utc).isoformat()
        glossary_dir.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Strip the entry from any current active decision list so that
        # the workbench reflects the exclusion without waiting for a
        # full re-extract.
        decisions_path = glossary_dir / "decisions.jsonl"
        if decisions_path.is_file() and action == "exclude":
            kept_lines: list[str] = []
            for line in decisions_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    kept_lines.append(line)
                    continue
                if isinstance(record, dict) and record.get("source") == source:
                    continue
                kept_lines.append(line)
            decisions_path.write_text(
                "\n".join(kept_lines) + ("\n" if kept_lines else ""),
                encoding="utf-8",
            )

        return self.glossary(job_id)

    def glossary_reextract(self, job_id: str) -> dict[str, Any]:
        run_dir = self._require_awaiting_glossary(job_id)
        policy_path = run_dir / "glossary" / "extraction-policy.json"
        args = ["glossary", "extract", str(run_dir)]
        if policy_path.is_file():
            policy = self._read_json_any(policy_path)
            current = policy.get("glossary_profile")
            if isinstance(current, str) and current:
                args.extend(["--profile", current])
        self._run(args)
        return self.glossary(job_id)

    def glossary_suggest(
        self,
        job_id: str,
        *,
        target_lang: str = "zh-CN",
        translator: str = "minimax",
        from_background: bool = False,
    ) -> dict[str, Any]:
        run_dir = self._require_awaiting_glossary(job_id)
        if not from_background:
            current = self._glossary_suggest_status(run_dir)
            if str(current.get("status") or "") == "running":
                raise JobServiceError("术语建议生成正在进行中，请稍候刷新页面。")
        self._run(
            [
                "glossary",
                "suggest",
                str(run_dir),
                "--target-lang",
                target_lang,
                "--translator",
                translator,
            ]
        )
        return self.glossary(job_id)

    def glossary_suggest_async(
        self,
        job_id: str,
        *,
        target_lang: str = "zh-CN",
        translator: str = "minimax",
    ) -> dict[str, Any]:
        run_dir = self._require_awaiting_glossary(job_id)
        current = self._glossary_suggest_status(run_dir)
        if str(current.get("status") or "") == "running":
            raise JobServiceError("术语建议生成正在进行中。")
        glossary_dir = run_dir / "glossary"
        glossary_dir.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(
            glossary_dir / "suggest-running.json",
            {
                "schema": "phase_a_glossary_suggest_running_v1",
                "status": "running",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "detail": "排队中，即将开始生成…",
                "processed_count": 0,
            },
        )
        return self._glossary_suggest_status(run_dir)

    def start_translation(self, job_id: str) -> dict[str, Any]:
        snapshot = self.get(job_id)
        artifacts = snapshot.get("artifacts")
        if not isinstance(artifacts, dict) or "canonical_chapters" not in artifacts:
            raise JobServiceError("请先确认章节目录，再开始全文翻译。")
        state = str(snapshot.get("state") or "")
        if state not in {"awaiting_glossary", "failed", "translating"}:
            raise JobServiceError(f"当前状态无法开始翻译：{state}")
        return snapshot

    def run_translation(self, job_id: str) -> None:
        self._validate_job_id(job_id)
        self._acquire_worker_lock(job_id)
        try:
            self._run(
                ["job", "translate", job_id, "--jobs-dir", str(self.jobs_dir), "--json"],
                timeout_seconds=-1,
            )
        finally:
            self._release_worker_lock(job_id)

    def _run_dir(self, job_id: str) -> Path:
        book_artifact = self.artifact_path(job_id, "book")
        return book_artifact.parent

    def _run_json(self, args: list[str]) -> dict[str, Any]:
        result = self._run(args)
        start = result.stdout.find("{")
        if start < 0:
            raise JobServiceError("pdf-translator did not return a job snapshot.")
        try:
            payload = json.loads(result.stdout[start:])
        except json.JSONDecodeError as exc:
            raise JobServiceError("pdf-translator returned invalid JSON.") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "book_job_v1":
            raise JobServiceError("pdf-translator returned an invalid job snapshot.")
        return payload

    def _run(self, args: list[str], *, timeout_seconds: int | None = None) -> subprocess.CompletedProcess[str]:
        if not (self.project_home / "pyproject.toml").is_file():
            raise JobServiceError(
                f"pdf-translator project not found at {self.project_home}"
            )
        settings = get_settings()
        if timeout_seconds == -1:
            effective_timeout = None
        elif timeout_seconds is not None:
            effective_timeout = int(timeout_seconds)
        else:
            effective_timeout = int(
                getattr(settings, "BOOKMATE_JOB_EXECUTE_TIMEOUT_SECONDS", None)
                or settings.BOOKMATE_INGEST_TIMEOUT_SECONDS + 300
            )
        environment = os.environ.copy()
        environment.pop("VIRTUAL_ENV", None)
        self._augment_subprocess_env(environment)
        self._normalize_provider_env(environment)
        # 启动 launchd 环境下 PATH 不含 ~/.local/bin，找不到 uv 时回退到 pdf-translator 自带 venv
        cmd = self._resolve_runner_cmd(environment)
        result = subprocess.run(
            [*cmd, "pdf-translator", *args],
            cwd=self.project_home,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            check=False,
            env=environment,
        )
        if result.returncode != 0:
            detail = "\n".join(
                part.strip() for part in (result.stderr, result.stdout) if part.strip()
            )
            raise JobServiceError(
                (detail or "pdf-translator command failed")[:2000]
            )
        return result


    def _resolve_runner_cmd(self, environment: dict[str, str]) -> list[str]:
        """决定调用 pdf-translator 的方式：优先用 PATH 中的 uv，否则用 ~/.local/bin/uv，
        最后回退到 pdf-translator 自带 venv 里的 python -m pdf_translator。"""
        for candidate in ("uv",):
            path = shutil.which(candidate, path=environment.get("PATH"))
            if path:
                return [path, "run"]
        # 常见的 uv 安装位置
        for fallback in (
            Path.home() / ".local" / "bin" / "uv",
            Path("/opt/homebrew/bin/uv"),
            Path("/usr/local/bin/uv"),
        ):
            if fallback.is_file() and os.access(fallback, os.X_OK):
                return [str(fallback), "run"]
        # 最后回退：直接用项目 venv 里的 python
        venv_python = self.project_home / ".venv" / "bin" / "python"
        if venv_python.is_file():
            return [str(venv_python), "-m"]
        raise JobServiceError("未找到 uv 或 pdf-translator 的 venv python，无法执行子任务")

    def _job_dir(self, job_id: str) -> Path:
        self._validate_job_id(job_id)
        return self.jobs_dir / job_id

    def _augment_subprocess_env(self, environment: dict[str, str]) -> None:
        candidates = [
            Path(__file__).resolve().parent / ".env",
            Path(__file__).resolve().parent.parent / ".env",
            self.project_home / ".env",
        ]
        for path in candidates:
            if path.is_file():
                _merge_env_file(path, environment)
        for rc_name in (".zshrc", ".zprofile", ".bash_profile", ".bashrc"):
            _merge_shell_exports(Path.home() / rc_name, environment, keys=_TRANSLATION_ENV_KEYS)
        settings = get_settings()
        for key in _TRANSLATION_ENV_KEYS:
            if environment.get(key):
                continue
            value = getattr(settings, key, None)
            if isinstance(value, str) and value.strip():
                environment[key] = value.strip()

    @staticmethod
    def _normalize_provider_env(environment: dict[str, str]) -> None:
        base = environment.get("MINIMAX_BASE_URL") or environment.get("LLM_BASE_URL")
        default = "https://api.minimaxi.com/anthropic/v1/messages"
        if base and "minimaxi.com" in base and "/anthropic/" not in base.rstrip("/"):
            environment["MINIMAX_BASE_URL"] = default
            return
        if environment.get("MINIMAX_API_KEY") and not base:
            environment["MINIMAX_BASE_URL"] = default

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", job_id):
            raise JobNotFound("Job not found.")

    @staticmethod
    def _read_json(path: Path, *, expected_schema: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JobServiceError(f"Invalid JSON file: {path.name}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != expected_schema:
            raise JobServiceError(f"Invalid schema in {path.name}")
        return payload

    @staticmethod
    def _read_json_any(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JobServiceError(f"Invalid JSON file: {path.name}") from exc
        if not isinstance(payload, dict):
            raise JobServiceError(f"Invalid JSON file: {path.name}")
        return payload

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)

    @staticmethod
    def _canonical_chapter(chapter: dict[str, Any], fallback_index: int) -> dict[str, Any]:
        title = str(chapter.get("title") or f"Chapter {fallback_index}").strip()
        return {
            "index": int(chapter.get("index") or fallback_index),
            "chapter_id": str(chapter.get("chapter_id") or f"chapter-{fallback_index:03d}"),
            "title": title or f"Chapter {fallback_index}",
            "page_start": chapter.get("page_start"),
            "page_end": chapter.get("page_end"),
            "source_pages": chapter.get("source_pages") if isinstance(chapter.get("source_pages"), list) else [],
        }


_job_service: BookJobService | None = None


def get_job_service() -> BookJobService:
    global _job_service
    if _job_service is None:
        _job_service = BookJobService()
    return _job_service
