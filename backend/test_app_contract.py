import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def test_application_imports_without_tencent_sdk():
    module = importlib.import_module("main")
    assert module.app.title


def test_public_route_contract_removes_instant_translation():
    module = importlib.import_module("main")
    routes = {(route.path, method) for route in module.app.routes for method in getattr(route, "methods", set())}

    assert ("/api/translate", "POST") not in routes
    assert ("/api/books/{book_id}/chapters/{chapter_index}/translate", "POST") not in routes
    assert ("/api/books/{book_id}/overview", "POST") in routes
    assert ("/api/books/{book_id}/chapters/{chapter_index}/summary", "POST") in routes
    assert ("/api/review/project", "GET") in routes
    assert ("/api/review/chapter-marks", "POST") in routes
    assert ("/api/jobs", "POST") in routes
    assert ("/api/jobs/duplicates", "POST") in routes
    assert ("/api/jobs", "GET") in routes
    assert ("/api/jobs/{job_id}", "GET") in routes
    assert ("/api/jobs/{job_id}", "DELETE") in routes
    assert ("/api/jobs/{job_id}/resume", "POST") in routes
    assert ("/api/jobs/{job_id}/chapters/draft", "GET") in routes
    assert ("/api/jobs/{job_id}/chapters/confirm", "POST") in routes
    assert ("/api/jobs/{job_id}/events", "GET") in routes
    assert ("/api/jobs/{job_id}/review-link", "GET") in routes
    assert ("/api/workspace/books", "GET") in routes


def test_review_helpers_use_pdf_translator_virtualenv(tmp_path: Path, monkeypatch):
    from conftest import scaffold_book_weaver_home

    module = importlib.import_module("main")
    home = scaffold_book_weaver_home(tmp_path)
    python_path = home / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.touch()
    monkeypatch.setenv("BOOK_WEAVER_HOME", str(home))

    assert module._pdf_translator_python() == python_path


def test_review_helpers_prefer_unified_repo_root(monkeypatch):
    from engine_home import _REPO_ROOT

    module = importlib.import_module("main")
    monkeypatch.delenv("PDF_TRANSLATOR_HOME", raising=False)
    monkeypatch.delenv("BOOK_WEAVER_HOME", raising=False)

    assert module._pdf_translator_home() == _REPO_ROOT.resolve()


def _write_review_project(run_dir: Path, *, open_items: int = 0):
    run_dir.mkdir(parents=True)
    (run_dir / "segments.json").write_text(
        json.dumps({"segments": [{"segment_id": "s1", "chapter_title": "Chapter 1"}]}),
        encoding="utf-8",
    )
    (run_dir / "translated_segments.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
    (run_dir / "review_items.json").write_text(json.dumps({"items": [{"segment_id": "s1"}]}), encoding="utf-8")
    (run_dir / "review_state.json").write_text(
        json.dumps(
            {
                "summary": {"total_items": 1, "open_items": open_items},
                "decisions": {
                    "s1": {"status": "open", "action": "model_rewrite"},
                },
            }
        ),
        encoding="utf-8",
    )


def test_review_project_export_does_not_imply_review_completed(tmp_path: Path):
    module = importlib.import_module("main")
    run_dir = tmp_path / "review"
    _write_review_project(run_dir, open_items=1)
    version_dir = run_dir / "versions" / "v1"
    version_dir.mkdir(parents=True)
    (version_dir / "version-manifest.json").write_text("{}", encoding="utf-8")

    item = module._build_review_project_item(run_dir)

    assert item is not None
    assert item.export_completed is True
    assert item.review_completed is False
    assert item.review_status == "in_review"


def test_review_project_marks_reviewed_before_export(tmp_path: Path):
    module = importlib.import_module("main")
    run_dir = tmp_path / "review"
    _write_review_project(run_dir, open_items=0)
    (run_dir / "review_state.json").write_text(
        json.dumps(
            {
                "summary": {"total_items": 1, "open_items": 0},
                "workflow": {"human_review_mode": "issues_only"},
                "decisions": {
                    "s1": {"status": "approved", "action": "manual_edit"},
                },
            }
        ),
        encoding="utf-8",
    )

    item = module._build_review_project_item(run_dir)

    assert item is not None
    assert item.review_completed is True
    assert item.export_completed is False
    assert item.review_status == "reviewed"


def test_review_project_discovery_includes_nested_job_review(tmp_path: Path):
    module = importlib.import_module("main")
    job_dir = tmp_path / "job-1"
    artifact_dir = job_dir / "artifacts" / "book "
    _write_review_project(artifact_dir)
    (job_dir / "review").symlink_to(artifact_dir, target_is_directory=True)

    discovered = module._discover_review_run_dirs(tmp_path)

    assert discovered == [job_dir / "review"]


def test_review_project_item_preserves_zero_open_items_and_pending_rewrites(tmp_path: Path):
    module = importlib.import_module("main")
    run_dir = tmp_path / "review"
    _write_review_project(run_dir, open_items=0)

    item = module._build_review_project_item(run_dir)

    assert item is not None
    assert item.qa_items_open == 0
    assert item.pending_rewrites == 1
    assert item.rewrites_needing_instruction == 1


def test_review_project_list_prefers_desktop_job_copy(tmp_path: Path, monkeypatch):
    module = importlib.import_module("main")
    legacy_root = tmp_path / "legacy"
    jobs_root = tmp_path / "jobs"
    legacy_run = legacy_root / "same-book-review"
    job_run = jobs_root / "same-book-review"
    for run_dir in (legacy_run, job_run):
        _write_review_project(run_dir)
        (run_dir / "manifest.json").write_text(
            json.dumps({"source_pdf": "/books/Same Book.epub"}),
            encoding="utf-8",
        )

    monkeypatch.setattr(module, "_review_roots", lambda: [legacy_root, jobs_root])
    monkeypatch.setattr(
        module,
        "get_job_service",
        lambda: SimpleNamespace(jobs_dir=jobs_root),
    )

    response = TestClient(module.app).get("/api/review/projects")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_projects"] == 1
    assert payload["projects"][0]["run_dir"] == str(job_run)


def test_review_project_list_marks_workspace_job_origin(tmp_path: Path, monkeypatch):
    module = importlib.import_module("main")
    jobs_root = tmp_path / "jobs"
    job_dir = jobs_root / "job-1"
    run_dir = job_dir / "review"
    _write_review_project(run_dir)
    (job_dir / "job.json").write_text(json.dumps({"schema": "book_job_v1"}), encoding="utf-8")
    monkeypatch.setattr(module, "_review_roots", lambda: [jobs_root])
    monkeypatch.setattr(
        module,
        "get_job_service",
        lambda: SimpleNamespace(jobs_dir=jobs_root),
    )

    response = TestClient(module.app).get("/api/review/projects")

    assert response.status_code == 200
    assert response.json()["projects"][0]["workspace_job_id"] == "job-1"


def test_review_project_can_be_hidden_without_deleting_files(tmp_path: Path, monkeypatch):
    module = importlib.import_module("main")
    jobs_root = tmp_path / "jobs"
    run_dir = jobs_root / "book-review"
    _write_review_project(run_dir)
    monkeypatch.setattr(module, "_review_roots", lambda: [jobs_root])
    monkeypatch.setattr(
        module,
        "get_job_service",
        lambda: SimpleNamespace(jobs_dir=jobs_root),
    )

    response = TestClient(module.app).delete(
        "/api/review/projects",
        params={"run_dir": str(run_dir), "mode": "hide"},
    )

    assert response.status_code == 200
    assert run_dir.exists()
    assert TestClient(module.app).get("/api/review/projects").json()["total_projects"] == 0


def test_review_project_delete_is_limited_to_jobs_root(tmp_path: Path, monkeypatch):
    module = importlib.import_module("main")
    jobs_root = tmp_path / "jobs"
    legacy_run = tmp_path / "legacy" / "book-review"
    _write_review_project(legacy_run)
    monkeypatch.setattr(module, "_review_roots", lambda: [legacy_run.parent, jobs_root])
    monkeypatch.setattr(
        module,
        "get_job_service",
        lambda: SimpleNamespace(jobs_dir=jobs_root),
    )

    response = TestClient(module.app).delete(
        "/api/review/projects",
        params={"run_dir": str(legacy_run), "mode": "delete"},
    )

    assert response.status_code == 400
    assert legacy_run.exists()


def test_review_rewrite_rejects_missing_instruction(tmp_path: Path, monkeypatch):
    module = importlib.import_module("main")
    run_dir = tmp_path / "review"
    _write_review_project(run_dir, open_items=0)
    monkeypatch.setattr(module, "_resolve_review_run_dir", lambda _: run_dir)

    response = TestClient(module.app).post(
        "/api/review/rewrite",
        params={"run_dir": str(run_dir)},
        json={},
    )

    assert response.status_code == 400
    assert "尚未填写给模型的重译要求" in response.json()["detail"]


def test_review_rewrite_uses_nested_cli_command(tmp_path: Path, monkeypatch):
    module = importlib.import_module("main")
    run_dir = tmp_path / "review"
    _write_review_project(run_dir, open_items=0)
    state = json.loads((run_dir / "review_state.json").read_text(encoding="utf-8"))
    state["decisions"] = {
        "segment-1": {
            "status": "open",
            "action": "model_rewrite",
            "reviewer_comment": "请完整重译。",
        }
    }
    (run_dir / "review_state.json").write_text(json.dumps(state), encoding="utf-8")
    calls = []

    def fake_cli(args, **_):
        calls.append(args)
        return SimpleNamespace(stdout="Rewritten candidates: 1", stderr="", returncode=0)

    monkeypatch.setattr(module, "_resolve_review_run_dir", lambda _: run_dir)
    monkeypatch.setattr(module, "_run_pdf_translator_cli", fake_cli)

    response = TestClient(module.app).post(
        "/api/review/rewrite",
        params={"run_dir": str(run_dir)},
        json={"segment_id": "segment-1"},
    )

    assert response.status_code == 200
    assert calls[0][:3] == ["review", "rewrite", str(run_dir)]


def test_review_export_uses_nested_cli_command(tmp_path: Path, monkeypatch):
    module = importlib.import_module("main")
    run_dir = tmp_path / "review"
    _write_review_project(run_dir, open_items=0)
    calls = []

    def fake_cli(args, **_):
        calls.append(args)
        version_dir = run_dir / "versions" / "final"
        version_dir.mkdir(parents=True)
        (version_dir / "book.pdf").write_bytes(b"%PDF-test")
        (version_dir / "book.epub").write_bytes(b"epub-test")
        (version_dir / "version-manifest.json").write_text(
            json.dumps(
                {
                    "version": "final",
                    "files": {
                        "translated_pdf": "versions/final/book.pdf",
                        "translated_epub": "versions/final/book.epub",
                    },
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(stdout="Exported final", stderr="", returncode=0)

    monkeypatch.setattr(module, "_resolve_review_run_dir", lambda _: run_dir)
    monkeypatch.setattr(module, "_run_pdf_translator_cli", fake_cli)
    monkeypatch.setattr(module, "_review_delivery_root", lambda: tmp_path / "delivery")

    response = TestClient(module.app).post(
        "/api/review/export",
        params={"run_dir": str(run_dir)},
        json={"version": "final", "output_format": "both"},
    )

    assert response.status_code == 200
    assert calls[0][:3] == ["review", "export", str(run_dir)]
    assert set(response.json()["delivered_files"]) == {
        "translated_pdf",
        "translated_epub",
    }


def test_pdf_translator_cli_hides_traceback_and_clears_foreign_virtualenv(
    tmp_path: Path,
    monkeypatch,
):
    module = importlib.import_module("main")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["environment"] = kwargs["env"]
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "warning: VIRTUAL_ENV=/private/review/.venv does not match\n"
                "Traceback (most recent call last):\n"
                '  File "/private/pdf-translator/cli.py", line 1, in main\n'
                "ValueError: Approved review export blocked: unresolved review "
                "items remain (6)."
            ),
        )

    monkeypatch.setattr(module, "_pdf_translator_home", lambda: tmp_path)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setenv("VIRTUAL_ENV", "/private/review/.venv")

    with pytest.raises(module.HTTPException) as exc_info:
        module._run_pdf_translator_cli(["review-export", "/private/book", "--version", "final"])

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "导出已拦截：仍有 6 个审阅项未完成。"
    assert "VIRTUAL_ENV" not in observed["environment"]


def test_review_decision_summary_does_not_double_count_approved_items(
    tmp_path: Path,
    monkeypatch,
):
    module = importlib.import_module("main")
    run_dir = tmp_path / "review"
    _write_review_project(run_dir, open_items=2)
    (run_dir / "segments.json").write_text(
        json.dumps({"segments": [{"segment_id": "s1"}, {"segment_id": "s2"}]}),
        encoding="utf-8",
    )
    (run_dir / "review_items.json").write_text(
        json.dumps({"items": [{"segment_id": "s1"}, {"segment_id": "s2"}]}),
        encoding="utf-8",
    )
    state = json.loads((run_dir / "review_state.json").read_text())
    state["summary"]["total_items"] = 2
    state["decisions"]["s2"] = {"status": "open", "action": "manual_edit"}
    (run_dir / "review_state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(module, "_resolve_review_run_dir", lambda _: run_dir)
    item_ids = [
        item["segment_id"]
        for item in json.loads((run_dir / "review_items.json").read_text())["items"]
    ]

    client = TestClient(module.app)
    for segment_id in item_ids:
        response = client.post(
            f"/api/review/segments/{segment_id}/decision",
            params={"run_dir": str(run_dir)},
            json={
                "status": "approved",
                "action": "manual_edit",
                "approved_text": "译文",
            },
        )
        assert response.status_code == 200

    summary = response.json()["review_state"]["summary"]
    assert summary == {
        "total_items": 2,
        "open_items": 0,
        "approved_items": 2,
        "resolved_items": 0,
    }


def test_manual_edit_draft_survives_project_reload(tmp_path: Path, monkeypatch):
    module = importlib.import_module("main")
    run_dir = tmp_path / "review"
    _write_review_project(run_dir, open_items=1)
    monkeypatch.setattr(module, "_resolve_review_run_dir", lambda _: run_dir)

    client = TestClient(module.app)
    response = client.post(
        "/api/review/segments/s1/decision",
        params={"run_dir": str(run_dir)},
        json={
            "status": "open",
            "action": "manual_edit",
            "approved_text": "尚未确认但必须保留的草稿",
            "reviewer_comment": "草稿意见",
        },
    )

    assert response.status_code == 200
    persisted = json.loads((run_dir / "review_state.json").read_text(encoding="utf-8"))
    decision = persisted["decisions"]["s1"]
    assert decision["status"] == "open"
    assert decision["action"] == "manual_edit"
    assert decision["approved_text"] == "尚未确认但必须保留的草稿"
    assert decision["reviewer_comment"] == "草稿意见"
    assert decision["updated_at"]


def test_review_json_write_preserves_original_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch,
):
    module = importlib.import_module("main")
    run_dir = tmp_path / "review"
    run_dir.mkdir()
    target = run_dir / "review_state.json"
    target.write_text('{"stable": true}', encoding="utf-8")

    def fail_replace(_source, _target):
        raise OSError("simulated interruption")

    monkeypatch.setattr(module.os, "replace", fail_replace)

    try:
        module._write_review_json(run_dir, "review_state.json", {"stable": False})
    except OSError:
        pass

    assert json.loads(target.read_text(encoding="utf-8")) == {"stable": True}


def _fake_book():
    chapter = SimpleNamespace(index=0, title="Introduction", content="Chapter content")
    metadata = SimpleNamespace(title="Test Book")
    return SimpleNamespace(metadata=metadata, chapters=[chapter])


def test_overview_maps_missing_ai_configuration_to_503(monkeypatch):
    module = importlib.import_module("main")

    class Storage:
        async def get_book(self, _):
            return _fake_book()

    class Service:
        async def generate_book_overview(self, **_):
            raise module.AIBackendUnavailable("AI backend is not configured.")

    async def fake_ai_service():
        return Service()

    monkeypatch.setattr(module, "get_storage", lambda: _async_value(Storage()))
    monkeypatch.setattr(module, "get_ai_service", fake_ai_service)
    response = TestClient(module.app).post("/api/books/book-1/overview", json={})
    assert response.status_code == 503


def test_summary_maps_invalid_model_output_to_502(monkeypatch):
    module = importlib.import_module("main")

    class Storage:
        async def get_book(self, _):
            return _fake_book()

    class Service:
        async def generate_chapter_summary(self, **_):
            raise module.AIOutputError("AI backend returned an empty chapter summary.")

    async def fake_ai_service():
        return Service()

    monkeypatch.setattr(module, "get_storage", lambda: _async_value(Storage()))
    monkeypatch.setattr(module, "get_ai_service", fake_ai_service)
    response = TestClient(module.app).post("/api/books/book-1/chapters/0/summary", json={})
    assert response.status_code == 502


async def _async_value(value):
    return value


def test_job_upload_returns_202_and_executes_in_background(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    calls = []
    snapshot = {
        "schema": "book_job_v1",
        "job_id": "job-1",
        "revision": 1,
        "state": "created",
        "artifacts": {},
    }

    class Service:
        jobs_dir = tmp_path / "jobs"

        def list(self):
            return []

        def create(self, **kwargs):
            calls.append(("create", kwargs))
            return snapshot

        def execute(self, job_id):
            calls.append(("execute", job_id))

    service = Service()
    monkeypatch.setattr(module, "get_job_service", lambda: service)

    response = TestClient(module.app).post(
        "/api/jobs",
        files={"file": ("sample.epub", b"epub", "application/epub+zip")},
        data={
            "processing_mode": "preserve",
            "target_language": "zh-CN",
            "translator": "mock",
            "output_format": "epub",
        },
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-1"
    assert calls[0][0] == "create"
    assert calls[0][1]["processing_mode"] == "preserve"
    assert calls[1] == ("execute", "job-1")


def test_job_duplicate_check_finds_existing_workspace_job(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    existing = _job_snapshot(
        "job-existing",
        filename="sample.epub",
        state="awaiting_human_review",
        text_operation="translate",
        artifacts={"book": {"href": "artifacts/book.json"}},
    )

    class Service:
        jobs_dir = tmp_path / "jobs"

        def list(self):
            return [existing]

    Service.jobs_dir.mkdir()
    monkeypatch.setattr(module, "get_job_service", lambda: Service())
    monkeypatch.setattr(module, "_review_roots", lambda: [Service.jobs_dir])

    response = TestClient(module.app).post(
        "/api/jobs/duplicates",
        files={"file": ("sample.epub", b"epub", "application/epub+zip")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_matches"] is True
    assert payload["matches"][0]["kind"] == "workspace_job"
    assert payload["matches"][0]["id"] == "job-existing"


def test_job_upload_defaults_to_preserve_without_explicit_translation(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    calls = []
    snapshot = {
        "schema": "book_job_v1",
        "job_id": "job-default",
        "revision": 1,
        "state": "created",
        "artifacts": {},
    }

    class Service:
        jobs_dir = tmp_path / "jobs"

        def list(self):
            return []

        def create(self, **kwargs):
            calls.append(("create", kwargs))
            return snapshot

        def execute(self, job_id):
            calls.append(("execute", job_id))

    monkeypatch.setattr(module, "get_job_service", lambda: Service())

    response = TestClient(module.app).post(
        "/api/jobs",
        files={"file": ("sample.epub", b"epub", "application/epub+zip")},
        data={
            "target_language": "zh-CN",
            "output_format": "epub",
        },
    )

    assert response.status_code == 202
    assert calls[0][0] == "create"
    assert calls[0][1]["processing_mode"] == "preserve"
    assert calls[0][1]["translator"] == "mock"
    assert calls[1] == ("execute", "job-default")


def test_job_upload_rejects_duplicate_unless_confirmed(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    existing = _job_snapshot(
        "job-existing",
        filename="sample.epub",
        state="awaiting_human_review",
        text_operation="translate",
        artifacts={"book": {"href": "artifacts/book.json"}},
    )
    calls = []

    class Service:
        jobs_dir = tmp_path / "jobs"

        def list(self):
            return [existing]

        def create(self, **kwargs):
            calls.append(("create", kwargs))
            return {
                "schema": "book_job_v1",
                "job_id": "job-new",
                "revision": 1,
                "state": "created",
                "artifacts": {},
            }

        def execute(self, job_id):
            calls.append(("execute", job_id))

    Service.jobs_dir.mkdir()
    service = Service()
    monkeypatch.setattr(module, "get_job_service", lambda: service)
    monkeypatch.setattr(module, "_review_roots", lambda: [service.jobs_dir])

    blocked = TestClient(module.app).post(
        "/api/jobs",
        files={"file": ("sample.epub", b"epub", "application/epub+zip")},
        data={"processing_mode": "preserve", "allow_duplicate": "false"},
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["matches"][0]["id"] == "job-existing"
    assert calls == []

    allowed = TestClient(module.app).post(
        "/api/jobs",
        files={"file": ("sample.epub", b"epub", "application/epub+zip")},
        data={"processing_mode": "preserve", "allow_duplicate": "true"},
    )

    assert allowed.status_code == 202
    assert allowed.json()["job_id"] == "job-new"
    assert calls[0][0] == "create"
    assert calls[1] == ("execute", "job-new")


def test_job_upload_rejects_unsupported_files(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    monkeypatch.setattr(
        module,
        "get_job_service",
        lambda: SimpleNamespace(jobs_dir=tmp_path / "jobs"),
    )

    response = TestClient(module.app).post(
        "/api/jobs",
        files={"file": ("notes.txt", b"text", "text/plain")},
    )

    assert response.status_code == 400


def test_reprocess_job_creates_new_job_from_existing_source(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    snapshot = {
        "schema": "book_job_v1",
        "job_id": "job-2",
        "state": "created",
        "request": {"processing_mode": "preserve"},
    }
    calls = []

    class Service:
        def create_from_existing(self, job_id, **kwargs):
            calls.append(("create_from_existing", job_id, kwargs))
            return snapshot

        def execute(self, job_id):
            calls.append(("execute", job_id))

    service = Service()
    monkeypatch.setattr(module, "get_job_service", lambda: service)

    response = TestClient(module.app).post(
        "/api/jobs/job-1/reprocess",
        json={
            "processing_mode": "preserve",
            "target_language": "zh-CN",
            "translator": "mock",
            "output_format": "epub",
        },
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-2"
    assert calls[0][0] == "create_from_existing"
    assert calls[0][1] == "job-1"
    assert calls[0][2]["processing_mode"] == "preserve"
    assert calls[0][2]["translator"] == "mock"
    assert calls[1] == ("execute", "job-2")


def test_delete_job_removes_workspace_copy(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    calls = []

    class Service:
        def delete(self, job_id):
            calls.append(job_id)

    monkeypatch.setattr(module, "get_job_service", lambda: Service())

    response = TestClient(module.app).delete("/api/jobs/job-1")

    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "job_id": "job-1"}
    assert calls == ["job-1"]


def _job_snapshot(
    job_id: str,
    *,
    filename: str,
    state: str,
    text_operation: str | None,
    failed_stage: str | None = None,
    artifacts: dict | None = None,
):
    return {
        "schema": "book_job_v1",
        "job_id": job_id,
        "revision": 3,
        "created_at": "2026-06-16T09:00:00Z",
        "updated_at": "2026-06-16T09:05:00Z",
        "state": state,
        "failed_stage": failed_stage,
        "source": {
            "filename": filename,
            "media_type": "application/epub+zip",
            "sha256": "abc",
            "size_bytes": 123,
        },
        "request": {
            "processing_mode": "auto",
            "source_language": None,
            "target_language": "zh-CN",
            "translator": "minimax",
            "output_format": "epub",
        },
        "resolved": {
            "source_language": "en",
            "text_operation": text_operation,
        },
        "progress": {
            "stage_percent": 100,
            "overall_percent": 80,
            "translation_chunks_total": 4,
            "translation_chunks_completed": 4,
            "translation_cache_hits": 0,
            "translation_attempts": 4,
            "translation_retries": 0,
        },
        "artifacts": artifacts or {},
        "error": None,
    }


def test_workspace_books_marks_translation_review_as_required_for_translated_jobs(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-translate",
        filename="English Book.epub",
        state="awaiting_human_review",
        text_operation="translate",
        artifacts={
            "book": {"href": "artifacts/book.json"},
            "review_items": {"href": "artifacts/review_items.json"},
        },
    )

    monkeypatch.setattr(module, "get_job_service", lambda: SimpleNamespace(list=lambda: [snapshot]))

    response = TestClient(module.app).get("/api/workspace/books")

    assert response.status_code == 200
    book = response.json()["books"][0]
    assert book["book_id"] == "job-translate"
    assert book["pipeline_status"] == "needs_translation_review"
    assert book["next_action"]["kind"] == "review_translation"
    assert book["steps"]["translation_review"]["status"] == "action_required"
    assert book["steps"]["chapter_confirmation"]["status"] == "action_required"
    assert book["workflow_path"] == "translation_edition"
    assert "translation_review" in book["workflow_step_order"]
    assert book["knowledge_ready"] is False
    assert response.json()["total_source_books"] == 1
    assert response.json()["source_books"][0]["text_versions"][0]["kind"] == "translated"


def test_workspace_books_marks_translation_review_after_chapters_confirmed(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-translate-ready",
        filename="English Book.epub",
        state="awaiting_human_review",
        text_operation="translate",
        artifacts={
            "book": {"href": "artifacts/book.json"},
            "review_items": {"href": "artifacts/review_items.json"},
            "canonical_chapters": {"href": "artifacts/canonical-chapters.json"},
        },
    )

    monkeypatch.setattr(module, "get_job_service", lambda: SimpleNamespace(list=lambda: [snapshot]))

    response = TestClient(module.app).get("/api/workspace/books")

    assert response.status_code == 200
    book = response.json()["books"][0]
    assert book["pipeline_status"] == "needs_translation_review"
    assert book["next_action"]["kind"] == "review_translation"
    assert book["steps"]["translation_review"]["status"] == "action_required"
    assert book["steps"]["chapter_confirmation"]["status"] == "done"


def test_workspace_books_next_action_confirm_chapters_after_glossary_ready(monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-glossary-ready",
        filename="Policy Book.epub",
        state="awaiting_glossary",
        text_operation="translate",
        artifacts={"glossary_candidates": {"href": "artifacts/glossary/candidates.json"}},
    )
    service = SimpleNamespace(
        list=lambda: [snapshot],
        glossary_workflow=lambda job_id: (
            {"stage": "glossary_ready", "glossary_finalized_by_user": True}
            if job_id == "job-glossary-ready"
            else None
        ),
    )

    monkeypatch.setattr(module, "get_job_service", lambda: service)

    response = TestClient(module.app).get("/api/workspace/books")

    assert response.status_code == 200
    book = response.json()["books"][0]
    assert book["next_action"]["kind"] == "confirm_chapters"
    assert book["next_action"]["label"] == "确认章节后开始翻译"
    assert book["steps"]["glossary_finalization"]["status"] == "done"


def test_workspace_books_next_action_rejects_auto_confirmed_chapters_after_glossary_ready(monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-auto-chapters",
        filename="Policy Book.epub",
        state="awaiting_glossary",
        text_operation="translate",
        artifacts={
            "glossary_candidates": {"href": "artifacts/glossary/candidates.json"},
            "canonical_chapters": {
                "href": "artifacts/canonical-chapters.json",
                "source_artifact": "book",
            },
        },
    )
    monkeypatch.setattr(
        module,
        "get_job_service",
        lambda: SimpleNamespace(
            glossary_workflow=lambda job_id: {
                "stage": "glossary_ready",
                "glossary_finalized_by_user": True,
            },
        ),
    )

    book = module._workspace_book_from_job(snapshot)

    assert book["next_action"]["kind"] == "confirm_chapters"
    assert book["steps"]["chapter_confirmation"]["status"] == "action_required"


def test_workspace_books_accepts_legacy_user_confirmed_canonical_file(
    tmp_path: Path,
    monkeypatch,
):
    module = importlib.import_module("main")
    canonical_path = tmp_path / "canonical-chapters.json"
    canonical_path.write_text(
        json.dumps(
            {
                "schema": "bookmate_canonical_chapters_v1",
                "source_artifact": "user_confirmation",
                "chapters": [{"index": 1, "chapter_id": "ch-1", "title": "Manual"}],
            }
        ),
        encoding="utf-8",
    )
    snapshot = _job_snapshot(
        "job-legacy-user-chapters",
        filename="Policy Book.epub",
        state="awaiting_glossary",
        text_operation="translate",
        artifacts={
            "glossary_candidates": {"href": "artifacts/glossary/candidates.json"},
            "canonical_chapters": {"href": "artifacts/canonical-chapters.json"},
        },
    )
    monkeypatch.setattr(
        module,
        "get_job_service",
        lambda: SimpleNamespace(
            artifact_path=lambda job_id, artifact_name: canonical_path,
            glossary_workflow=lambda job_id: {
                "stage": "glossary_ready",
                "glossary_finalized_by_user": True,
            },
        ),
    )

    book = module._workspace_book_from_job(snapshot)

    assert book["next_action"]["kind"] == "start_translation"
    assert book["steps"]["chapter_confirmation"]["status"] == "done"


def test_workspace_books_next_action_start_translation_after_glossary_and_chapters_ready(monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-ready",
        filename="Policy Book.epub",
        state="awaiting_glossary",
        text_operation="translate",
        artifacts={
            "glossary_candidates": {"href": "artifacts/glossary/candidates.json"},
            "canonical_chapters": {
                "href": "artifacts/canonical-chapters.json",
                "source_artifact": "user_confirmation",
            },
        },
    )
    monkeypatch.setattr(
        module,
        "get_job_service",
        lambda: SimpleNamespace(
            glossary_workflow=lambda job_id: {
                "stage": "glossary_ready",
                "glossary_finalized_by_user": True,
            },
        ),
    )

    book = module._workspace_book_from_job(snapshot)
    assert book["next_action"]["kind"] == "start_translation"


def test_workspace_books_keeps_glossary_open_after_translation_started_without_finalization(monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-translating",
        filename="Policy Book.epub",
        state="translating",
        text_operation="translate",
        artifacts={
            "book": {"href": "artifacts/book.json"},
            "glossary_candidates": {"href": "artifacts/glossary/candidates.json"},
        },
    )
    service = SimpleNamespace(
        list=lambda: [snapshot],
        glossary_workflow=lambda _job_id: {"stage": "translating"},
    )
    monkeypatch.setattr(module, "get_job_service", lambda: service)

    response = TestClient(module.app).get("/api/workspace/books")

    assert response.status_code == 200
    book = response.json()["books"][0]
    assert book["steps"]["glossary_finalization"]["status"] == "blocked"


def test_workspace_books_reports_polish_outcome_and_step_done(monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-polish-done",
        filename="Policy Book.epub",
        state="pre_review",
        text_operation="translate",
        artifacts={
            "book": {"href": "artifacts/book.json"},
            "review_items": {"href": "artifacts/review_items.json"},
        },
    )
    snapshot["progress"] = {"polish_outcome": "no_candidates"}
    monkeypatch.setattr(module, "get_job_service", lambda: SimpleNamespace(list=lambda: [snapshot]))

    response = TestClient(module.app).get("/api/workspace/books")

    assert response.status_code == 200
    book = response.json()["books"][0]
    assert "polish" in book["workflow_step_order"]
    assert book["steps"]["polish"]["status"] == "done"
    assert "未发现需要润色" in book["steps"]["polish"]["description"]


def test_workspace_books_polish_failure_prompts_resume(monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-polish-failed",
        filename="Policy Book.epub",
        state="failed",
        failed_stage="polishing",
        text_operation="translate",
        artifacts={"book": {"href": "artifacts/book.json"}},
    )
    monkeypatch.setattr(module, "get_job_service", lambda: SimpleNamespace(list=lambda: [snapshot]))

    response = TestClient(module.app).get("/api/workspace/books")

    assert response.status_code == 200
    book = response.json()["books"][0]
    assert book["pipeline_status"] == "failed"
    assert book["steps"]["polish"]["status"] == "failed"
    assert book["next_action"]["kind"] == "resume_job"
    assert book["next_action"]["label"] == "重试润色"


def test_workspace_books_failed_translation_respects_resume_gate(monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-translation-human-gate",
        filename="Policy Book.epub",
        state="failed",
        failed_stage="translating",
        text_operation="translate",
        artifacts={"book": {"href": "artifacts/book.json"}},
    )
    snapshot["translation_resume"] = {
        "available": False,
        "reason": "human_gate_required",
        "detail": "请先人工确认章节目录，再开始全文翻译。",
    }
    monkeypatch.setattr(module, "get_job_service", lambda: SimpleNamespace(list=lambda: [snapshot]))

    book = module._workspace_book_from_job(snapshot)

    assert book["pipeline_status"] == "failed"
    assert book["next_action"]["kind"] == "confirm_chapters"
    assert book["next_action"]["label"] == "请先人工确认章节目录，再开始全文翻译。"


def test_workspace_books_skips_translation_review_for_preserved_jobs(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-preserve",
        filename="Chinese Book.epub",
        state="awaiting_human_review",
        text_operation="preserve",
        artifacts={
            "book": {"href": "artifacts/book.json"},
            "review_items": {"href": "artifacts/review_items.json"},
        },
    )

    monkeypatch.setattr(module, "get_job_service", lambda: SimpleNamespace(list=lambda: [snapshot]))

    response = TestClient(module.app).get("/api/workspace/books")

    assert response.status_code == 200
    book = response.json()["books"][0]
    assert book["pipeline_status"] == "needs_chapter_confirmation"
    assert book["next_action"]["kind"] == "confirm_chapters"
    assert book["steps"]["translation_review"]["status"] == "skipped"
    assert book["steps"]["chapter_confirmation"]["status"] == "action_required"
    assert book["workflow_path"] == "source_edition"
    assert "translation_review" not in book["workflow_step_order"]
    assert book["knowledge_ready"] is False
    source_book = response.json()["source_books"][0]
    assert source_book["chapter_structure"]["status"] == "needs_confirmation"
    assert source_book["text_versions"][0]["kind"] == "source"


def test_workspace_books_marks_translation_failure_on_text_processing(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-translate-failed",
        filename="English Book.epub",
        state="failed",
        failed_stage="translating",
        text_operation="translate",
    )

    monkeypatch.setattr(module, "get_job_service", lambda: SimpleNamespace(list=lambda: [snapshot]))

    response = TestClient(module.app).get("/api/workspace/books")

    assert response.status_code == 200
    book = response.json()["books"][0]
    assert book["pipeline_status"] == "failed"
    assert book["steps"]["import"]["status"] == "done"
    assert book["steps"]["structure"]["status"] == "done"
    assert book["steps"]["text_processing"]["status"] == "failed"
    assert book["steps"]["translation_review"]["status"] == "blocked"
    assert book["lifecycle_state"] == "failed"
    assert book["lifecycle_stage"] == "translating"


def test_workspace_books_reports_active_lifecycle_stage(monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-preserving",
        filename="Source Book.epub",
        state="preserving",
        text_operation="preserve",
        artifacts={"book": {"href": "artifacts/book.json"}},
    )
    monkeypatch.setattr(module, "get_job_service", lambda: SimpleNamespace(list=lambda: [snapshot]))

    response = TestClient(module.app).get("/api/workspace/books")

    assert response.status_code == 200
    book = response.json()["books"][0]
    assert book["lifecycle_state"] == "active"
    assert book["lifecycle_stage"] == "preserving"


def test_workspace_books_groups_source_book_text_versions_and_history(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    preserve = _job_snapshot(
        "job-preserve",
        filename="Same Book.epub",
        state="awaiting_human_review",
        text_operation="preserve",
        artifacts={
            "book": {"href": "artifacts/book.json"},
            "canonical_chapters": {"href": "artifacts/canonical-chapters.json"},
        },
    )
    translated_old = _job_snapshot(
        "job-translate-old",
        filename="Same Book.epub",
        state="failed",
        failed_stage="ingesting",
        text_operation="translate",
    )
    translated_new = _job_snapshot(
        "job-translate-new",
        filename="Same Book.epub",
        state="awaiting_human_review",
        text_operation="translate",
        artifacts={
            "book": {"href": "artifacts/book.json"},
            "review_items": {"href": "artifacts/review_items.json"},
        },
    )
    translated_old["updated_at"] = "2026-06-16T09:01:00Z"
    translated_new["updated_at"] = "2026-06-16T09:02:00Z"

    monkeypatch.setattr(
        module,
        "get_job_service",
        lambda: SimpleNamespace(list=lambda: [preserve, translated_old, translated_new]),
    )

    response = TestClient(module.app).get("/api/workspace/books")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_books"] == 3
    assert payload["total_source_books"] == 1
    source_book = payload["source_books"][0]
    assert source_book["chapter_structure"]["status"] == "confirmed"
    assert [version["kind"] for version in source_book["text_versions"]] == ["source", "translated"]
    assert source_book["text_versions"][1]["job_id"] == "job-translate-new"
    assert source_book["task_history_count"] == 3
    assert source_book["hidden_task_count"] == 1


def test_workspace_books_prefers_in_flight_translation_over_stale_review_ready(monkeypatch):
    module = importlib.import_module("main")
    stale_review = _job_snapshot(
        "job-translate-old",
        filename="Same Book.epub",
        state="awaiting_human_review",
        text_operation="translate",
        artifacts={
            "book": {"href": "artifacts/book.json"},
            "review_items": {"href": "artifacts/review_items.json"},
        },
    )
    in_flight = _job_snapshot(
        "job-translate-new",
        filename="Same Book.epub",
        state="translating",
        text_operation="translate",
        artifacts={
            "book": {"href": "artifacts/book.json"},
            "glossary_candidates": {"href": "artifacts/glossary/candidates.json"},
        },
    )
    stale_review["updated_at"] = "2026-06-28T09:00:00Z"
    in_flight["updated_at"] = "2026-06-29T09:00:00Z"
    in_flight["progress"] = {
        "stage_percent": 38,
        "overall_percent": 44,
        "translation_chunks_total": 52,
        "translation_chunks_completed": 20,
        "translation_cache_hits": 20,
        "translation_attempts": 20,
        "translation_retries": 0,
    }

    monkeypatch.setattr(
        module,
        "get_job_service",
        lambda: SimpleNamespace(
            list=lambda: [stale_review, in_flight],
            glossary_workflow=lambda job_id: (
                {"stage": "translating"} if job_id == "job-translate-new" else None
            ),
        ),
    )

    response = TestClient(module.app).get("/api/workspace/books")

    assert response.status_code == 200
    source_book = response.json()["source_books"][0]
    translated = next(version for version in source_book["text_versions"] if version["kind"] == "translated")
    assert translated["job_id"] == "job-translate-new"
    assert translated["pipeline_status"] == "processing"
    assert translated["job_state"] == "translating"
    assert translated["status_label"] == "正在翻译全文"
    assert source_book["chapter_structure"]["label"] == "翻译进行中"


def test_confirm_job_chapters_marks_preserved_job_ready_for_knowledge(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-preserve",
        filename="Chinese Book.epub",
        state="awaiting_human_review",
        text_operation="preserve",
        artifacts={
            "book": {"href": "artifacts/book.json"},
            "canonical_chapters": {"href": "artifacts/canonical-chapters.json"},
        },
    )
    calls = []

    class Service:
        def confirm_chapters(self, job_id, *, chapters=None):
            assert chapters is None
            calls.append(job_id)
            return snapshot

    monkeypatch.setattr(module, "get_job_service", lambda: Service())

    response = TestClient(module.app).post("/api/jobs/job-preserve/chapters/confirm")

    assert response.status_code == 200
    assert calls == ["job-preserve"]
    body = response.json()
    assert body["job"]["artifacts"]["canonical_chapters"]["href"] == "artifacts/canonical-chapters.json"
    assert body["workspace_book"]["pipeline_status"] == "ready_for_knowledge"
    assert body["workspace_book"]["steps"]["chapter_confirmation"]["status"] == "done"
    assert body["workspace_book"]["knowledge_ready"] is True


def test_get_job_chapter_draft_returns_editable_chapters(tmp_path, monkeypatch):
    module = importlib.import_module("main")

    class Service:
        def draft_chapters(self, job_id):
            assert job_id == "job-1"
            return [{"index": 1, "chapter_id": "ch-1", "title": "Intro"}]

    monkeypatch.setattr(module, "get_job_service", lambda: Service())

    response = TestClient(module.app).get("/api/jobs/job-1/chapters/draft")

    assert response.status_code == 200
    assert response.json()["chapters"][0]["title"] == "Intro"


def test_get_job_source_returns_original_file(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")

    class Service:
        def source_path(self, job_id):
            assert job_id == "job-1"
            return source

    monkeypatch.setattr(module, "get_job_service", lambda: Service())

    response = TestClient(module.app).get("/api/jobs/job-1/source")

    assert response.status_code == 200
    assert response.content == b"%PDF"
    assert response.headers["content-type"] == "application/pdf"


def test_confirm_job_chapters_passes_user_edited_chapters(tmp_path, monkeypatch):
    module = importlib.import_module("main")
    snapshot = _job_snapshot(
        "job-1",
        filename="Book.epub",
        state="awaiting_human_review",
        text_operation="preserve",
        artifacts={
            "book": {"href": "artifacts/book.json"},
            "canonical_chapters": {"href": "artifacts/canonical-chapters.json"},
        },
    )
    observed = {}

    class Service:
        def confirm_chapters(self, job_id, *, chapters=None):
            observed["job_id"] = job_id
            observed["chapters"] = chapters
            return snapshot

    monkeypatch.setattr(module, "get_job_service", lambda: Service())

    response = TestClient(module.app).post(
        "/api/jobs/job-1/chapters/confirm",
        json={"chapters": [{"index": 1, "chapter_id": "manual-1", "title": "手动章节"}]},
    )

    assert response.status_code == 200
    assert observed["job_id"] == "job-1"
    assert observed["chapters"][0]["title"] == "手动章节"


def test_job_glossary_endpoint_returns_active_entries(monkeypatch) -> None:
    module = importlib.import_module("main")

    payload = {
        "schema": "phase_a_glossary_v1",
        "updated_at": "2026-06-27T10:00:00Z",
        "entries": [
            {"source": "Yellow Emperor", "target": None, "status": "candidate"},
            {"source": "Ritual Office", "target": "礼官署", "status": "active"},
        ],
        "status": {"candidate_count": 2, "active_count": 1, "entry_count": 2},
    }

    class Service:
        def glossary(self, job_id: str):
            assert job_id == "job-1"
            return payload

    monkeypatch.setattr(module, "get_job_service", lambda: Service())

    response = TestClient(module.app).get("/api/jobs/job-1/glossary")

    assert response.status_code == 200
    assert response.json()["entries"][1]["target"] == "礼官署"


def test_job_glossary_profile_put_reextracts(monkeypatch) -> None:
    module = importlib.import_module("main")
    calls: list[str] = []

    class Service:
        def glossary_set_profile(self, job_id: str, profile: str):
            assert job_id == "job-1"
            calls.append(profile)
            return {
                "schema": "phase_a_glossary_v1",
                "profile": {"id": profile, "label": "社会·经济·哲学", "overridden": True},
                "candidates": [],
                "entries": [],
                "status": {"candidate_count": 0, "active_count": 0, "entry_count": 0},
            }

    monkeypatch.setattr(module, "get_job_service", lambda: Service())

    response = TestClient(module.app).put(
        "/api/jobs/job-1/glossary/profile",
        json={"profile": "social_econ_philosophy"},
    )

    assert response.status_code == 200
    assert calls == ["social_econ_philosophy"]
    assert response.json()["profile"]["id"] == "social_econ_philosophy"


def test_job_glossary_suggest_defaults_to_minimax(monkeypatch) -> None:
    module = importlib.import_module("main")
    calls: list[dict[str, str]] = []

    class Service:
        def glossary_suggest_async(self, job_id: str, *, target_lang: str, translator: str):
            assert job_id == "job-1"
            calls.append({"target_lang": target_lang, "translator": translator})
            return {"status": "running", "processed_count": 0, "total_count": 1}

        def glossary(self, job_id: str):
            return {
                "schema": "phase_a_glossary_v1",
                "candidates": [
                    {
                        "source": "Shareholder Primacy",
                        "target_suggestion": "股东至上",
                        "suggestion_confidence": 0.95,
                    }
                ],
                "entries": [],
                "status": {"candidate_count": 1, "active_count": 0, "entry_count": 0},
                "suggest_status": {"status": "running", "processed_count": 0, "total_count": 1},
            }

    monkeypatch.setattr(module, "get_job_service", lambda: Service())

    response = TestClient(module.app).post("/api/jobs/job-1/glossary/suggest", json={})

    assert response.status_code == 202
    assert calls == [{"target_lang": "zh-CN", "translator": "minimax"}]
    body = response.json()
    assert body["status"] == "started"
    assert body["glossary"]["candidates"][0]["target_suggestion"] == "股东至上"
