from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SUPERVISOR_INTERVAL_SECONDS = 30
_active_resumes: set[str] = set()
_HUMAN_GATE_MARKERS = ("人工确认章节", "完成术语确认")


def _max_auto_resume_attempts() -> int:
    raw = os.getenv("BOOKMATE_AUTO_RESUME_MAX_ATTEMPTS", "3").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 3


async def translation_supervisor_loop(service: Any) -> None:
    """Resume stalled translate jobs automatically; at most one resume per job at a time."""
    while True:
        await asyncio.sleep(SUPERVISOR_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(_scan_and_resume_stalled, service)
        except Exception:
            logger.exception("Translation supervisor tick failed")


def _scan_and_resume_stalled(service: Any) -> None:
    # The standalone ``scripts/translation_supervisor.py`` is the primary
    # detector. The in-lifespan loop is a safety net; it is enabled by
    # default and can be disabled explicitly if it would double-trigger
    # with the external supervisor.
    flag = os.getenv("BOOKMATE_DISABLE_EMBEDDED_SUPERVISOR", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return
    max_attempts = _max_auto_resume_attempts()
    for snapshot in service.list():
        job_id = str(snapshot.get("job_id") or "")
        state = str(snapshot.get("state") or "")
        if not job_id or state not in {"translating", "failed"}:
            continue
        if job_id in _active_resumes:
            continue
        enriched = service.get(job_id)
        resume = enriched.get("translation_resume")
        if not isinstance(resume, dict) or not resume.get("available"):
            continue
        if service.translation_worker_lock_held(job_id):
            continue
        attempts = service.auto_resume_attempts(job_id) if hasattr(service, "auto_resume_attempts") else 0
        if attempts >= max_attempts:
            if hasattr(service, "mark_translation_auto_resume_exhausted"):
                service.mark_translation_auto_resume_exhausted(job_id, max_attempts)
            logger.warning(
                "Auto-resume exhausted for job %s after %s attempts",
                job_id,
                max_attempts,
            )
            continue
        logger.info("Auto-resuming stalled translation job %s", job_id)
        _active_resumes.add(job_id)
        try:
            if hasattr(service, "record_auto_resume_attempt"):
                service.record_auto_resume_attempt(job_id)
            service.resume(job_id)
        except Exception as exc:
            message = str(exc)
            if any(marker in message for marker in _HUMAN_GATE_MARKERS) and hasattr(
                service, "mark_translation_resume_blocked"
            ):
                service.mark_translation_resume_blocked(job_id, message)
                logger.warning("Auto-resume blocked by human gate for job %s: %s", job_id, message)
                continue
            logger.exception("Auto-resume failed for job %s", job_id)
        finally:
            _active_resumes.discard(job_id)
