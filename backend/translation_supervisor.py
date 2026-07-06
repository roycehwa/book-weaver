from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SUPERVISOR_INTERVAL_SECONDS = 90
_active_resumes: set[str] = set()


async def translation_supervisor_loop(service: Any) -> None:
    """Resume stalled translate jobs automatically; at most one resume per job at a time."""
    while True:
        await asyncio.sleep(SUPERVISOR_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(_scan_and_resume_stalled, service)
        except Exception:
            logger.exception("Translation supervisor tick failed")


def _scan_and_resume_stalled(service: Any) -> None:
    if os.getenv("BOOKMATE_AUTO_RESUME_TRANSLATION", "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        return
    for snapshot in service.list():
        job_id = str(snapshot.get("job_id") or "")
        if not job_id or snapshot.get("state") != "translating":
            continue
        if job_id in _active_resumes:
            continue
        enriched = service.get(job_id)
        resume = enriched.get("translation_resume")
        if not isinstance(resume, dict) or not resume.get("available"):
            continue
        if service.translation_worker_lock_held(job_id):
            continue
        logger.info("Auto-resuming stalled translation job %s", job_id)
        _active_resumes.add(job_id)
        try:
            service.resume(job_id)
        except Exception:
            logger.exception("Auto-resume failed for job %s", job_id)
        finally:
            _active_resumes.discard(job_id)
