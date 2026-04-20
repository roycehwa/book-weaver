from pathlib import Path

import pytest

from pdf_translator.guardrails import IngestTimeoutError, InputGateError, PdfPreflight
from pdf_translator.validation import (
    _article_metrics,
    _profile_metrics,
    discover_newspaper_cases,
    load_validation_manifest,
    run_newspaper_directory,
    run_validation_manifest,
    write_validation_report,
)


def test_profile_metrics_summarize_usable_and_reject() -> None:
    metrics = _profile_metrics(
        {
            "total_pages": 10,
            "document_action": "review",
            "actions": {
                "accept": 6,
                "assist": 2,
                "skip_content": 1,
                "reject_structure": 1,
            },
        }
    )
    assert metrics["usable_pct"] == 0.8
    assert metrics["reject_pct"] == 0.1
    assert metrics["document_action"] == "review"


def test_article_metrics_summarize_selected_quality() -> None:
    metrics = _article_metrics(
        {
            "article_count": 4,
            "selected_top_half_count": 2,
            "quality_summary": {"high": 2, "medium": 1, "low": 1},
            "selected_article_indexes": [0, 2],
            "articles": [
                {"quality": {"grade": "high"}},
                {"quality": {"grade": "low"}},
                {"quality": {"grade": "medium"}},
                {"quality": {"grade": "high"}},
            ],
        }
    )
    assert metrics["selected_quality"] == {"high": 1, "medium": 1, "low": 0}
    assert metrics["selected_pass_pct"] == 1.0


def test_load_validation_manifest_accepts_object_wrapper(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        '{"cases":[{"source_pdf":"/tmp/a.pdf","mode":"profile","profile":"book"}]}',
        encoding="utf-8",
    )
    cases = load_validation_manifest(manifest_path)
    assert len(cases) == 1
    assert cases[0]["profile"] == "book"


def test_run_validation_manifest_reuses_existing_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "runs"
    profile_dir = output_dir / "book-doc"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.json").write_text(
        """
        {
          "profile": "book",
          "total_pages": 20,
          "document_action": "accept",
          "actions": {
            "accept": 18,
            "assist": 2,
            "skip_content": 0,
            "reject_structure": 0
          }
        }
        """,
        encoding="utf-8",
    )
    article_dir = output_dir / "news-doc"
    article_dir.mkdir(parents=True)
    (article_dir / "articles.json").write_text(
        """
        {
          "article_count": 6,
          "selected_top_half_count": 3,
          "quality_summary": {"high": 3, "medium": 2, "low": 1},
          "selected_article_indexes": [0, 1, 2],
          "articles": [
            {"quality": {"grade": "high"}},
            {"quality": {"grade": "medium"}},
            {"quality": {"grade": "low"}},
            {"quality": {"grade": "high"}},
            {"quality": {"grade": "medium"}},
            {"quality": {"grade": "low"}}
          ]
        }
        """,
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """
        {
          "cases": [
            {"name": "book sample", "source_pdf": "/tmp/book-doc.pdf", "mode": "profile", "profile": "book"},
            {"name": "news sample", "source_pdf": "/tmp/news-doc.pdf", "mode": "articles", "selected_pass_min_pct": 0.66}
          ]
        }
        """,
        encoding="utf-8",
    )
    report = run_validation_manifest(manifest_path, output_dir=output_dir, reuse_existing=True)
    assert report["total_cases"] == 2
    assert report["passed_cases"] == 2
    assert report["pass_rate"] == 1.0
    news_case = next(case for case in report["cases"] if case["mode"] == "articles")
    reading_path = Path(news_case["reading_artifact_path"])
    assert reading_path.exists()
    assert reading_path.name == "articles.md"


def test_write_validation_report_persists_json(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    write_validation_report({"passed_cases": 1}, report_path)
    assert report_path.exists()
    assert '"passed_cases": 1' in report_path.read_text(encoding="utf-8")


def test_discover_newspaper_cases_skips_ocr(tmp_path: Path) -> None:
    (tmp_path / "FT20260417.pdf").write_text("placeholder", encoding="utf-8")
    (tmp_path / "FT20260417_OCR.pdf").write_text("placeholder", encoding="utf-8")
    (tmp_path / "WSJ20260417.pdf").write_text("placeholder", encoding="utf-8")

    cases, skipped_files = discover_newspaper_cases(tmp_path, selected_pass_min_pct=0.9)

    assert [case["name"] for case in cases] == ["FT20260417", "WSJ20260417"]
    assert cases[0]["selected_pass_min_pct"] == 0.9
    assert len(skipped_files) == 1
    assert skipped_files[0].endswith("FT20260417_OCR.pdf")


def test_run_validation_manifest_captures_timeout_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ingest(*args, **kwargs):
        raise IngestTimeoutError(
            "Ingest timed out after 10s for sample.pdf.",
            preflight=PdfPreflight(
                source_pdf=Path("/tmp/sample.pdf"),
                profile_name="newspaper",
                page_count=88,
                file_size_bytes=12 * 1024 * 1024,
                warn_page_count=96,
                max_page_count=160,
                warn_file_size_mb=35.0,
                max_file_size_mb=80.0,
                warnings=[],
            ),
        )

    monkeypatch.setattr("pdf_translator.validation.ingest_pdf_guarded", fake_ingest)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """
        {
          "cases": [
            {"name": "news timeout", "source_pdf": "/tmp/sample.pdf", "mode": "articles"}
          ]
        }
        """,
        encoding="utf-8",
    )

    report = run_validation_manifest(manifest_path, output_dir=tmp_path / "runs", reuse_existing=False)
    assert report["passed_cases"] == 0
    assert report["failure_types"] == {"timeout": 1}
    assert report["cases"][0]["failure"]["type"] == "timeout"
    assert report["cases"][0]["preflight"]["page_count"] == 88


def test_run_validation_manifest_captures_input_gate_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ingest(*args, **kwargs):
        raise InputGateError(
            "Input gate rejected sample.pdf: page count 900 exceeds limit 600 for profile 'book'.",
            preflight=PdfPreflight(
                source_pdf=Path("/tmp/book.pdf"),
                profile_name="book",
                page_count=900,
                file_size_bytes=32 * 1024 * 1024,
                warn_page_count=320,
                max_page_count=600,
                warn_file_size_mb=60.0,
                max_file_size_mb=120.0,
                warnings=["Page count 900 is above the warning threshold 320 for profile 'book'."],
            ),
        )

    monkeypatch.setattr("pdf_translator.validation.ingest_pdf_guarded", fake_ingest)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """
        {
          "cases": [
            {"name": "book gate", "source_pdf": "/tmp/book.pdf", "mode": "profile", "profile": "book"}
          ]
        }
        """,
        encoding="utf-8",
    )

    report = run_validation_manifest(manifest_path, output_dir=tmp_path / "runs", reuse_existing=False)
    assert report["passed_cases"] == 0
    assert report["failure_types"] == {"input_gate": 1}
    assert report["cases"][0]["failure"]["type"] == "input_gate"
    assert report["cases"][0]["preflight"]["max_page_count"] == 600


def test_run_validation_manifest_captures_scan_like_input_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ingest(*args, **kwargs):
        preflight = PdfPreflight(
            source_pdf=Path("/tmp/scan.pdf"),
            profile_name="newspaper",
            page_count=24,
            file_size_bytes=30 * 1024 * 1024,
            warn_page_count=96,
            max_page_count=160,
            warn_file_size_mb=35.0,
            max_file_size_mb=80.0,
            text_layer_chars=0,
            image_marker_count=48,
            warnings=["No usable embedded text layer detected; document appears scan-like and falls outside the non-OCR input policy."],
        )
        raise InputGateError(
            "Input gate rejected scan.pdf: no usable embedded text layer detected; scan-like PDFs are not supported by the non-OCR pipeline.",
            preflight=preflight,
        )

    monkeypatch.setattr("pdf_translator.validation.ingest_pdf_guarded", fake_ingest)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """
        {
          "cases": [
            {"name": "scan gate", "source_pdf": "/tmp/scan.pdf", "mode": "articles"}
          ]
        }
        """,
        encoding="utf-8",
    )

    report = run_validation_manifest(manifest_path, output_dir=tmp_path / "runs", reuse_existing=False)
    assert report["failure_types"] == {"input_gate": 1}
    assert report["cases"][0]["preflight"]["text_layer_chars"] == 0


def test_run_newspaper_directory_summarizes_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_dir = tmp_path / "papers"
    source_dir.mkdir()
    (source_dir / "FT20260417.pdf").write_text("placeholder", encoding="utf-8")
    (source_dir / "WSJ20260417.pdf").write_text("placeholder", encoding="utf-8")

    def fake_run_validation_case(case, output_dir, reuse_existing=True, **kwargs):
        if case["name"] == "FT20260417":
            return {
                "name": case["name"],
                "mode": "articles",
                "profile": "newspaper",
                "source_pdf": case["source_pdf"],
                "artifact_path": str(output_dir / case["name"] / "articles.json"),
                "passed": True,
                "metrics": {
                    "article_count": 10,
                    "selected_top_half_count": 5,
                    "quality_summary": {"high": 6, "medium": 3, "low": 1},
                    "selected_quality": {"high": 4, "medium": 1, "low": 0},
                    "selected_pass_pct": 1.0,
                },
            }
        return {
            "name": case["name"],
            "mode": "articles",
            "profile": "newspaper",
            "source_pdf": case["source_pdf"],
            "artifact_path": str(output_dir / case["name"] / "articles.json"),
            "passed": False,
            "failure": {"type": "timeout", "message": "timed out"},
        }

    monkeypatch.setattr("pdf_translator.validation.run_validation_case", fake_run_validation_case)
    report = run_newspaper_directory(
        source_dir,
        output_dir=tmp_path / "runs",
        reuse_existing=False,
        selected_pass_min_pct=0.9,
    )

    assert report["total_cases"] == 2
    assert report["passed_cases"] == 1
    assert report["failure_types"] == {"timeout": 1}
    assert report["selected_pass_min_pct"] == 0.9
