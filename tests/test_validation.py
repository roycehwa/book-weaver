from pathlib import Path

import pytest

from pdf_translator.guardrails import IngestTimeoutError, InputGateError, PdfPreflight
from pdf_translator.validation import (
    _profile_metrics,
    load_validation_manifest,
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


def test_load_validation_manifest_accepts_object_wrapper(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        '{"cases":[{"source_pdf":"/tmp/a.pdf","mode":"profile","profile":"book"}]}',
        encoding="utf-8",
    )
    cases = load_validation_manifest(manifest_path)
    assert len(cases) == 1
    assert cases[0]["profile"] == "book"


def test_load_validation_manifest_rejects_articles_mode(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        '{"cases":[{"source_pdf":"/tmp/a.pdf","mode":"articles"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="articles"):
        load_validation_manifest(manifest_path)


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
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """
        {
          "cases": [
            {"name": "book sample", "source_pdf": "/tmp/book-doc.pdf", "mode": "profile", "profile": "book"}
          ]
        }
        """,
        encoding="utf-8",
    )
    report = run_validation_manifest(manifest_path, output_dir=output_dir, reuse_existing=True)
    assert report["total_cases"] == 1
    assert report["passed_cases"] == 1
    assert report["pass_rate"] == 1.0
    assert report["cases"][0]["mode"] == "profile"


def test_write_validation_report_persists_json(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    write_validation_report({"passed_cases": 1}, report_path)
    assert report_path.exists()
    assert '"passed_cases": 1' in report_path.read_text(encoding="utf-8")


def test_run_validation_manifest_captures_timeout_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ingest(*args, **kwargs):
        raise IngestTimeoutError(
            "Ingest timed out after 10s for sample.pdf.",
            preflight=PdfPreflight(
                source_pdf=Path("/tmp/sample.pdf"),
                profile_name="magazine",
                page_count=88,
                file_size_bytes=12 * 1024 * 1024,
                warn_page_count=112,
                max_page_count=220,
                warn_file_size_mb=40.0,
                max_file_size_mb=100.0,
                warnings=[],
            ),
        )

    monkeypatch.setattr("pdf_translator.validation.ingest_pdf_guarded", fake_ingest)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """
        {
          "cases": [
            {"name": "timeout case", "source_pdf": "/tmp/sample.pdf", "mode": "profile", "profile": "magazine"}
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
            "Input gate rejected sample.pdf: page count 1601 exceeds limit 1500 for profile 'book'.",
            preflight=PdfPreflight(
                source_pdf=Path("/tmp/book.pdf"),
                profile_name="book",
                page_count=1601,
                file_size_bytes=32 * 1024 * 1024,
                warn_page_count=800,
                max_page_count=1500,
                warn_file_size_mb=60.0,
                max_file_size_mb=120.0,
                warnings=["Page count 1601 is above the warning threshold 800 for profile 'book'."],
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
    assert report["cases"][0]["preflight"]["max_page_count"] == 1500


def test_run_validation_manifest_captures_scan_like_input_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ingest(*args, **kwargs):
        preflight = PdfPreflight(
            source_pdf=Path("/tmp/scan.pdf"),
            profile_name="magazine",
            page_count=24,
            file_size_bytes=30 * 1024 * 1024,
            warn_page_count=112,
            max_page_count=220,
            warn_file_size_mb=40.0,
            max_file_size_mb=100.0,
            text_layer_chars=0,
            image_marker_count=48,
            warnings=[
                "No usable embedded text layer detected; document appears scan-like and falls outside the non-OCR input policy."
            ],
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
            {"name": "scan gate", "source_pdf": "/tmp/scan.pdf", "mode": "profile", "profile": "magazine"}
          ]
        }
        """,
        encoding="utf-8",
    )

    report = run_validation_manifest(manifest_path, output_dir=tmp_path / "runs", reuse_existing=False)
    assert report["failure_types"] == {"input_gate": 1}
    assert report["cases"][0]["preflight"]["text_layer_chars"] == 0
