from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf_translator.content_policy_dependencies import (
    analyze_content_policy_dependencies,
    collect_content_policy_dependency_evidence,
    findings_from_evidence,
    validate_acknowledged_dependencies,
)


def _synthetic_epub_book() -> dict:
    body_markdown = "See [the note](OPS/Text/notes.xhtml#n1)."
    return {
        "metadata": {"chapter_source": "epub_spine"},
        "chapters": [
            {
                "index": 1,
                "chapter_id": "body",
                "title": "Body",
                "markdown": body_markdown,
                "source_internal_path": "OPS/Text/body.xhtml",
                "dom_units": [
                    {
                        "markdown": body_markdown,
                        "resource_path": "OPS/Text/body.xhtml",
                        "dom_path": "/body[1]/p[1]",
                        "char_start": 0,
                        "char_end": 20,
                        "element_id": "p1",
                        "link_targets": ["OPS/Text/notes.xhtml#n1"],
                    }
                ],
            },
            {
                "index": 2,
                "chapter_id": "notes",
                "title": "Notes",
                "markdown": "1. Original note.",
                "source_internal_path": "OPS/Text/notes.xhtml",
                "dom_units": [
                    {
                        "markdown": "1. Original note.",
                        "resource_path": "OPS/Text/notes.xhtml",
                        "dom_path": "/body[1]/p[1]",
                        "char_start": 0,
                        "char_end": 18,
                        "element_id": "n1",
                        "link_targets": [],
                    }
                ],
            },
            {
                "index": 3,
                "chapter_id": "refs",
                "title": "References",
                "markdown": "Ref.",
                "source_internal_path": "OPS/Text/refs.xhtml",
                "dom_units": [
                    {
                        "markdown": "See [bib](OPS/Text/refs.xhtml#r1).",
                        "resource_path": "OPS/Text/refs.xhtml",
                        "link_targets": ["OPS/Text/refs.xhtml#r1"],
                    }
                ],
            },
        ],
    }


def test_collect_evidence_resolves_relative_epub_href_and_fragment() -> None:
    book = _synthetic_epub_book()
    evidence = collect_content_policy_dependency_evidence(book)
    assert any(
        item["source_chapter_id"] == "body"
        and item["target_chapter_id"] == "notes"
        and item["link_evidence"] == "OPS/Text/notes.xhtml#n1"
        for item in evidence
    )


def test_no_false_positive_for_unrelated_links() -> None:
    book = _synthetic_epub_book()
    evidence = collect_content_policy_dependency_evidence(book)
    assert not any(item["target_chapter_id"] == "refs" for item in evidence)


def test_findings_require_exclude_on_notes_and_stable_ids() -> None:
    book = _synthetic_epub_book()
    evidence = collect_content_policy_dependency_evidence(book)
    canonical = [
        {"chapter_id": "body", "title": "Body", "content_policy": "translate"},
        {"chapter_id": "notes", "title": "Notes", "content_policy": "exclude"},
    ]
    findings = findings_from_evidence(evidence, canonical)
    assert len(findings) == 1
    first = findings[0]
    assert first["dependency_id"].startswith("cpd-")
    assert first["recommended_policy"] == "preserve"
    second = findings_from_evidence(evidence, canonical)
    assert second[0]["dependency_id"] == first["dependency_id"]


def test_preserve_notes_produces_no_blocking_findings() -> None:
    book = _synthetic_epub_book()
    analysis = analyze_content_policy_dependencies(
        book,
        [
            {"chapter_id": "body", "title": "Body", "content_policy": "translate"},
            {"chapter_id": "notes", "title": "Notes", "content_policy": "preserve"},
        ],
    )
    assert analysis["findings"] == []


def test_validate_rejects_unknown_acknowledgement_ids() -> None:
    findings = [{"dependency_id": "cpd-abc"}]
    with pytest.raises(ValueError):
        validate_acknowledged_dependencies(findings, ["cpd-stale"])


def test_job_service_blocks_before_canonical_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from job_service import BookJobService, JobServiceError

    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    job_dir = service.jobs_dir / "job-1"
    run_dir = job_dir / "artifacts" / "run-1"
    run_dir.mkdir(parents=True)
    book = _synthetic_epub_book()
    (run_dir / "book.json").write_text(json.dumps(book), encoding="utf-8")
    snapshot = {
        "schema": "book_job_v1",
        "job_id": "job-1",
        "updated_at": "2026-06-12T10:00:00Z",
        "state": "awaiting_chapter_confirmation",
        "revision": 1,
        "artifacts": {"book": {"href": "artifacts/run-1/book.json"}},
    }
    (job_dir / "job.json").write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setattr(
        service,
        "_build_confirmed_book",
        lambda job_id, canonical: {"chapters": [], "metadata": {}},
    )
    monkeypatch.setattr(service, "_write_chapter_segment_preview", lambda *args, **kwargs: None)

    with pytest.raises(JobServiceError) as exc_info:
        service.confirm_chapters(
            "job-1",
            chapters=[
                {
                    "index": 1,
                    "chapter_id": "body",
                    "title": "Body",
                    "page_start": 1,
                    "page_end": 1,
                    "content_policy": "translate",
                },
                {
                    "index": 2,
                    "chapter_id": "notes",
                    "title": "Notes",
                    "page_start": 2,
                    "page_end": 2,
                    "content_policy": "exclude",
                },
            ],
        )
    assert exc_info.value.payload is not None
    assert exc_info.value.payload.get("code") == "content_policy_dependencies_unacknowledged"
    assert not (job_dir / "artifacts" / "canonical-chapters.json").exists()

    dep_id = exc_info.value.payload["findings"][0]["dependency_id"]
    service.confirm_chapters(
        "job-1",
        chapters=[
            {
                "index": 1,
                "chapter_id": "body",
                "title": "Body",
                "page_start": 1,
                "page_end": 1,
                "content_policy": "translate",
            },
            {
                "index": 2,
                "chapter_id": "notes",
                "title": "Notes",
                "page_start": 2,
                "page_end": 2,
                "content_policy": "exclude",
            },
        ],
        acknowledged_dependency_ids=[dep_id],
    )
    assert (run_dir / "content-policy-dependencies.json").is_file()
    assert (job_dir / "artifacts" / "canonical-chapters.json").is_file()
