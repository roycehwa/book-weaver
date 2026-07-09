#!/usr/bin/env python3
"""Independent translation supervisor process.

Runs as a standalone CLI alongside the FastAPI backend. Every
``--scan-interval`` seconds it asks the backend for jobs in state
``translating``, uses the activity endpoint to detect stalled workers,
and POSTs ``/api/jobs/{id}/resume`` to recover them automatically.

Decoupling this from the uvicorn lifespan means the supervisor keeps
running across backend restarts and the backend can crash without
killing translation.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("translation_supervisor")


# ----------------------------- HTTP client -----------------------------


class BackendClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.reason}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return 0, {"error": str(e)}

    def _post(self, path: str) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method="POST", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.reason}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return 0, {"error": str(e)}

    def list_jobs(self) -> list[dict[str, Any]]:
        code, body = self._get("/api/jobs")
        if code != 200 or not isinstance(body, dict):
            LOG.warning("list_jobs failed: code=%s body=%r", code, body)
            return []
        jobs = body.get("jobs") or []
        return [j for j in jobs if isinstance(j, dict)]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        code, body = self._get(f"/api/jobs/{job_id}")
        if code != 200 or not isinstance(body, dict):
            return None
        return body

    def resume_job(self, job_id: str) -> tuple[int, Any]:
        return self._post(f"/api/jobs/{job_id}/resume")


# ----------------------------- decision logic -----------------------------


@dataclass
class StallAssessment:
    stalled: bool
    reason: str
    seconds_since_update: float | None
    running_chunks: int
    last_error: str | None


def assess_stall(snapshot: dict[str, Any], activity: dict[str, Any] | None, *, stuck_threshold: float) -> StallAssessment:
    if not isinstance(activity, dict):
        return StallAssessment(False, "no_activity", None, 0, None)
    status = str(activity.get("status") or "unknown")
    last_error = activity.get("last_error") if isinstance(activity.get("last_error"), str) else None
    seconds = activity.get("seconds_since_update")
    running = int(activity.get("running_chunks") or 0)
    if status in {"waiting", "active"}:
        return StallAssessment(False, f"status={status}", _to_float(seconds), running, last_error)
    if status == "stalled":
        return StallAssessment(True, "activity_reports_stalled", _to_float(seconds), running, last_error)
    if status == "unknown":
        sec = _to_float(seconds)
        if sec is not None and sec >= stuck_threshold:
            return StallAssessment(True, f"no_progress_for_{int(sec)}s", sec, running, last_error)
        return StallAssessment(False, "no_activity_record", sec, running, last_error)
    return StallAssessment(False, f"status={status}", _to_float(seconds), running, last_error)


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ----------------------------- rate limiting -----------------------------


class RateLimiter:
    def __init__(self, max_per_hour: int) -> None:
        self.max = max_per_hour
        self.history: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        if self.max <= 0:
            return True
        now = time.time()
        q = self.history.setdefault(key, deque())
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= self.max:
            return False
        q.append(now)
        return True


# ----------------------------- supervisor -----------------------------


class Supervisor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.client = BackendClient(args.backend_url, timeout=args.http_timeout)
        self.scan_interval = args.scan_interval
        self.stuck_threshold = args.stuck_threshold
        self.dry_run = args.dry_run
        self.metrics_path = Path(args.metrics_file) if args.metrics_file else None
        self.rate_limiter = RateLimiter(args.max_resume_per_hour)
        self._stop = False
        self.metrics = {
            "started_at": _utcnow_iso(),
            "scans": 0,
            "resumes_triggered": 0,
            "resumes_skipped_cooldown": 0,
            "resumes_skipped_rate": 0,
            "resumes_failed": 0,
            "last_scan_at": None,
            "stalled_jobs": [],
        }

    def stop(self, *_: Any) -> None:
        LOG.info("Stop signal received; finishing current scan")
        self._stop = True

    def run(self) -> None:
        LOG.info(
            "translation supervisor starting backend=%s scan_interval=%ds stuck_threshold=%ds dry_run=%s",
            self.client.base_url, self.scan_interval, self.stuck_threshold, self.dry_run,
        )
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        while not self._stop:
            try:
                self._scan_once()
            except Exception:
                LOG.exception("Scan iteration crashed")
            self._sleep(self.scan_interval)
        LOG.info("translation supervisor stopped")
        self._flush_metrics()

    def _scan_once(self) -> None:
        self.metrics["scans"] += 1
        self.metrics["last_scan_at"] = _utcnow_iso()
        jobs = [j for j in self.client.list_jobs() if j.get("state") == "translating"]
        LOG.debug("scanning %d translating jobs", len(jobs))
        stalled: list[dict[str, Any]] = []
        for snap in jobs:
            job_id = str(snap.get("job_id") or "")
            if not job_id:
                continue
            snap_full = self.client.get_job(job_id) or snap
            activity = snap_full.get("translation_activity") if isinstance(snap_full, dict) else None
            verdict = assess_stall(snap, activity, stuck_threshold=self.stuck_threshold)
            LOG.debug(
                "job=%s status=%s stalled=%s reason=%s seconds_since_update=%s running=%s",
                job_id, (activity or {}).get("status"), verdict.stalled, verdict.reason,
                verdict.seconds_since_update, verdict.running_chunks,
            )
            if not verdict.stalled:
                continue
            stalled.append({
                "job_id": job_id,
                "reason": verdict.reason,
                "seconds_since_update": verdict.seconds_since_update,
                "running_chunks": verdict.running_chunks,
                "last_error": verdict.last_error,
            })
            self._attempt_resume(job_id, verdict)
        self.metrics["stalled_jobs"] = stalled[:20]
        self._flush_metrics()

    def _attempt_resume(self, job_id: str, verdict: StallAssessment) -> None:
        if not self.rate_limiter.allow(job_id):
            self.metrics["resumes_skipped_rate"] += 1
            LOG.warning("rate-limit: skipping resume for job=%s", job_id)
            return
        if self.dry_run:
            LOG.info("dry-run: would resume job=%s reason=%s", job_id, verdict.reason)
            return
        code, body = self.client.resume_job(job_id)
        if code in {200, 202}:
            self.metrics["resumes_triggered"] += 1
            LOG.info("resume triggered job=%s code=%s reason=%s", job_id, code, verdict.reason)
        else:
            detail = ""
            if isinstance(body, dict):
                detail = str(body.get("detail") or body.get("error") or "")
            if detail and ("cooldown" in detail.lower() or "wait" in detail.lower()):
                self.metrics["resumes_skipped_cooldown"] += 1
                LOG.info("resume cooldown job=%s detail=%s", job_id, detail)
            else:
                self.metrics["resumes_failed"] += 1
                LOG.warning("resume failed job=%s code=%s body=%r", job_id, code, body)

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self._stop and time.time() < end:
            time.sleep(min(0.5, max(0.0, end - time.time())))

    def _flush_metrics(self) -> None:
        if not self.metrics_path:
            return
        try:
            self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
            self.metrics_path.write_text(
                json.dumps(self.metrics, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            LOG.exception("failed to flush metrics to %s", self.metrics_path)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------- entry point -----------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Translation supervisor (independent process)")
    p.add_argument("--backend-url", default=os.environ.get("BOOK_WEAVER_BACKEND", "http://127.0.0.1:8000"))
    p.add_argument("--scan-interval", type=int, default=30, help="seconds between scans (default 30)")
    p.add_argument("--stuck-threshold", type=int, default=90, help="seconds without progress to consider stalled (default 90)")
    p.add_argument("--http-timeout", type=float, default=5.0)
    p.add_argument("--max-resume-per-hour", type=int, default=6)
    p.add_argument("--metrics-file", default="", help="optional path to write JSON metrics")
    p.add_argument("--dry-run", action="store_true", help="detect and log but never POST resume")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    Supervisor(args).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
