"""Unit tests for the independent translation supervisor."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import translation_supervisor as ts  # noqa: E402


class FakeClient:
    def __init__(self) -> None:
        self.list_calls = 0
        self.resume_calls: list[str] = []
        self.jobs: list[dict] = []
        self.activities: dict[str, dict] = {}

    def list_jobs(self) -> list[dict]:
        self.list_calls += 1
        return self.jobs

    def get_job(self, job_id: str) -> dict | None:
        act = self.activities.get(job_id)
        if act is None:
            return None
        return {"job_id": job_id, "translation_activity": act}

    def resume_job(self, job_id: str) -> tuple[int, object]:
        self.resume_calls.append(job_id)
        return 202, {"ok": True}


def _args(**overrides):
    base = dict(
        backend_url="http://test",
        scan_interval=30,
        stuck_threshold=90,
        http_timeout=1.0,
        max_resume_per_hour=6,
        metrics_file="",
        dry_run=False,
    )
    base.update(overrides)
    return type("Args", (), base)


def _job(job_id: str, state: str = "translating") -> dict:
    return {"job_id": job_id, "state": state}


# --- assess_stall -----------------------------------------------------------


def test_assess_stall_no_activity():
    a = ts.assess_stall(_job("j1"), None, stuck_threshold=90)
    assert a.stalled is False
    assert a.reason == "no_activity"


def test_assess_stall_active_worker_is_not_stalled():
    a = ts.assess_stall(_job("j1"), {"status": "active", "running_chunks": 3}, stuck_threshold=90)
    assert a.stalled is False
    assert a.reason == "status=active"


def test_assess_stall_waiting_worker_is_not_stalled():
    a = ts.assess_stall(_job("j1"), {"status": "waiting", "running_chunks": 5}, stuck_threshold=90)
    assert a.stalled is False


def test_assess_stall_explicit_stalled_flag():
    a = ts.assess_stall(_job("j1"), {"status": "stalled", "seconds_since_update": 200}, stuck_threshold=90)
    assert a.stalled is True
    assert a.reason == "activity_reports_stalled"


def test_assess_stall_unknown_old_progress_is_stalled():
    a = ts.assess_stall(_job("j1"), {"status": "unknown", "seconds_since_update": 200}, stuck_threshold=90)
    assert a.stalled is True
    assert "no_progress_for_" in a.reason


def test_assess_stall_unknown_recent_progress_not_stalled():
    a = ts.assess_stall(_job("j1"), {"status": "unknown", "seconds_since_update": 30}, stuck_threshold=90)
    assert a.stalled is False


# --- RateLimiter ------------------------------------------------------------


def test_rate_limiter_per_key():
    rl = ts.RateLimiter(max_per_hour=2)
    assert rl.allow("a") is True
    assert rl.allow("a") is True
    assert rl.allow("a") is False
    # other key not affected
    assert rl.allow("b") is True


def test_rate_limiter_zero_means_unlimited():
    rl = ts.RateLimiter(max_per_hour=0)
    for _ in range(50):
        assert rl.allow("a") is True


# --- Supervisor end-to-end scan --------------------------------------------


def test_scan_no_jobs_runs_clean():
    fake = FakeClient()
    sup = ts.Supervisor(_args())
    sup.client = fake
    sup._scan_once()
    assert fake.list_calls == 1
    assert fake.resume_calls == []
    assert sup.metrics["scans"] == 1


def test_scan_skips_non_translating_jobs():
    fake = FakeClient()
    fake.jobs = [_job("j1", state="awaiting_human_review")]
    sup = ts.Supervisor(_args())
    sup.client = fake
    sup._scan_once()
    assert fake.resume_calls == []


def test_scan_triggers_resume_for_stalled():
    fake = FakeClient()
    fake.jobs = [_job("j1")]
    fake.activities["j1"] = {"status": "stalled", "seconds_since_update": 200, "running_chunks": 0}
    sup = ts.Supervisor(_args())
    sup.client = fake
    sup._scan_once()
    assert fake.resume_calls == ["j1"]
    assert sup.metrics["resumes_triggered"] == 1


def test_scan_dry_run_does_not_resume():
    fake = FakeClient()
    fake.jobs = [_job("j1")]
    fake.activities["j1"] = {"status": "stalled", "seconds_since_update": 200}
    sup = ts.Supervisor(_args(dry_run=True))
    sup.client = fake
    sup._scan_once()
    assert fake.resume_calls == []
    assert sup.metrics["resumes_triggered"] == 0


def test_scan_respects_rate_limit():
    fake = FakeClient()
    fake.jobs = [_job("j1")]
    fake.activities["j1"] = {"status": "stalled", "seconds_since_update": 200}
    sup = ts.Supervisor(_args(max_resume_per_hour=1))
    sup.client = fake
    sup._scan_once()
    sup._scan_once()
    sup._scan_once()
    assert len(fake.resume_calls) == 1
    assert sup.metrics["resumes_skipped_rate"] == 2


def test_scan_active_worker_not_resumed():
    fake = FakeClient()
    fake.jobs = [_job("j1")]
    fake.activities["j1"] = {"status": "active", "running_chunks": 2, "seconds_since_update": 1}
    sup = ts.Supervisor(_args())
    sup.client = fake
    sup._scan_once()
    assert fake.resume_calls == []
    assert sup.metrics["stalled_jobs"] == []


def test_scan_metrics_persisted(tmp_path):
    fake = FakeClient()
    fake.jobs = [_job("j1")]
    fake.activities["j1"] = {"status": "stalled", "seconds_since_update": 200}
    metrics = tmp_path / "metrics.json"
    sup = ts.Supervisor(_args(metrics_file=str(metrics)))
    sup.client = fake
    sup._scan_once()
    sup._flush_metrics()
    data = json.loads(metrics.read_text())
    assert data["scans"] == 1
    assert data["resumes_triggered"] == 1
    assert data["stalled_jobs"][0]["job_id"] == "j1"


def test_parse_args_defaults():
    args = ts.parse_args([])
    assert args.backend_url == "http://127.0.0.1:8000"
    assert args.scan_interval == 30
    assert args.stuck_threshold == 90
    assert args.max_resume_per_hour == 6
    assert args.dry_run is False


def test_parse_args_env_override(monkeypatch):
    monkeypatch.setenv("BOOK_WEAVER_BACKEND", "http://example:9000")
    args = ts.parse_args([])
    assert args.backend_url == "http://example:9000"
