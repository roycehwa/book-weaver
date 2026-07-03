import json
from pathlib import Path

from pdf_translator import review_migration
from pdf_translator.review_migration import migrate_legacy_review_run


def test_legacy_review_migration_rebuilds_book_without_changing_review_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job_dir = tmp_path / "job"
    artifacts_dir = job_dir / "artifacts"
    run_dir = artifacts_dir / "book"
    run_dir.mkdir(parents=True)
    source_path = job_dir / "source.pdf"
    source_path.write_bytes(b"%PDF")
    normalized_path = run_dir / "normalized.json"
    normalized_path.write_text("{}", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_pdf": str(source_path),
                "files": {"normalized_json": str(normalized_path)},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "pages": [{"page_no": 1, "has_content": True}],
                "chapters": [],
            }
        ),
        encoding="utf-8",
    )
    review_state_path = run_dir / "review_state.json"
    review_state_path.write_text(
        '{"decisions":{"s1":{"status":"approved","approved_text":"译文"}}}',
        encoding="utf-8",
    )
    (artifacts_dir / "canonical-chapters.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "title": "Confirmed",
                        "page_start": 1,
                        "page_end": 1,
                        "source_pages": [1],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        review_migration,
        "build_book_reconstruction",
        lambda *_args, **_kwargs: {
            "metadata": {"chapter_source": "test"},
            "pages": [{"page_no": 1, "has_content": True}],
            "chapters": [
                {
                    "index": 1,
                    "chapter_id": "chapter-001",
                    "title": "Original",
                    "page_start": 1,
                    "page_end": 1,
                    "source_pages": [1],
                    "markdown": "Body.",
                    "trace_markdown": "[[page: 1]]\n\nBody.",
                    "translate": True,
                }
            ],
        },
    )

    before = review_state_path.read_bytes()
    result = migrate_legacy_review_run(run_dir)

    assert result["migrated"] is True
    assert result["page_ledger"]["summary"]["required_coverage_ratio"] == 1.0
    assert result["integrity_ledger"]["dimensions"]["pages"]["ratio"] == 1.0
    assert (run_dir / "integrity-ledger.json").exists()
    assert review_state_path.read_bytes() == before
    assert (
        run_dir
        / "migration-backups"
        / result["backup_id"]
        / "book.json"
    ).exists()
