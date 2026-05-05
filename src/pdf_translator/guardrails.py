from __future__ import annotations

import multiprocessing as mp
import json
import tempfile
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pdf_translator.ingest import ingest_document, ingest_pdf, read_epub_spine_length
from pdf_translator.models import NormalizedDocument


DEFAULT_INGEST_TIMEOUT_SECONDS = 240

DEFAULT_WARN_PAGE_COUNT = {
    "auto": 160,
    "magazine": 140,
    "book": 800,
}

DEFAULT_MAX_PAGE_COUNT = {
    "auto": 320,
    "magazine": 220,
    "book": 1500,
}

DEFAULT_WARN_FILE_SIZE_MB = {
    "auto": 40.0,
    "magazine": 50.0,
    "book": 60.0,
}

DEFAULT_MAX_FILE_SIZE_MB = {
    "auto": 80.0,
    "magazine": 100.0,
    "book": 120.0,
}


@dataclass(slots=True)
class PdfPreflight:
    source_pdf: Path
    profile_name: str
    page_count: int
    file_size_bytes: int
    warn_page_count: int | None
    max_page_count: int | None
    warn_file_size_mb: float | None
    max_file_size_mb: float | None
    ingest_page_count: int | None = None
    soft_page_limit: int | None = None
    text_layer_chars: int | None = None
    image_marker_count: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def file_size_mb(self) -> float:
        return self.file_size_bytes / (1024 * 1024)

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["source_pdf"] = str(self.source_pdf)
        payload["file_size_mb"] = round(self.file_size_mb, 2)
        return payload


class IngestGuardrailError(RuntimeError):
    failure_type = "ingest_guardrail"

    def __init__(self, message: str, *, preflight: PdfPreflight | None = None) -> None:
        super().__init__(message)
        self.preflight = preflight


class InputGateError(IngestGuardrailError):
    failure_type = "input_gate"


class IngestTimeoutError(IngestGuardrailError):
    failure_type = "timeout"


class IngestExecutionError(IngestGuardrailError):
    failure_type = "ingest_error"


def _normalize_profile_name(profile_name: str | None) -> str:
    candidate = (profile_name or "auto").strip().lower()
    if candidate not in DEFAULT_MAX_PAGE_COUNT:
        return "auto"
    return candidate


def _effective_warn_threshold(default_value: float | int | None, max_value: float | int | None) -> float | int | None:
    if max_value is None:
        return default_value
    if default_value is None:
        return max_value
    return min(default_value, max_value * 0.8)


def _read_page_count(source_pdf: Path) -> int:
    if source_pdf.suffix.lower() == ".epub":
        return read_epub_spine_length(source_pdf)
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(source_pdf))
    try:
        return len(document)
    finally:
        document.close()


def inspect_pdf_preflight(
    source_pdf: Path,
    *,
    profile_name: str = "auto",
    max_file_size_mb: float | None = None,
    max_page_count: int | None = None,
) -> PdfPreflight:
    profile_key = _normalize_profile_name(profile_name)
    try:
        page_count = _read_page_count(source_pdf)
    except Exception as exc:
        raise IngestExecutionError(f"Unable to inspect page count for {source_pdf.name}: {exc}") from exc

    file_size_bytes = source_pdf.stat().st_size
    resolved_max_page_count = max_page_count if max_page_count is not None else DEFAULT_MAX_PAGE_COUNT[profile_key]
    resolved_max_file_size_mb = (
        max_file_size_mb if max_file_size_mb is not None else DEFAULT_MAX_FILE_SIZE_MB[profile_key]
    )
    warn_page_count = _effective_warn_threshold(DEFAULT_WARN_PAGE_COUNT[profile_key], resolved_max_page_count)
    warn_file_size_mb = _effective_warn_threshold(DEFAULT_WARN_FILE_SIZE_MB[profile_key], resolved_max_file_size_mb)

    warnings: list[str] = []
    file_size_mb = file_size_bytes / (1024 * 1024)
    if warn_page_count is not None and page_count > warn_page_count:
        warnings.append(
            f"Page count {page_count} is above the warning threshold {int(warn_page_count)} for profile '{profile_key}'."
        )
    if warn_file_size_mb is not None and file_size_mb > warn_file_size_mb:
        warnings.append(
            f"File size {file_size_mb:.1f}MB is above the warning threshold {warn_file_size_mb:.1f}MB "
            f"for profile '{profile_key}'."
        )

    return PdfPreflight(
        source_pdf=source_pdf,
        profile_name=profile_key,
        page_count=page_count,
        file_size_bytes=file_size_bytes,
        warn_page_count=int(warn_page_count) if warn_page_count is not None else None,
        max_page_count=int(resolved_max_page_count) if resolved_max_page_count is not None else None,
        warn_file_size_mb=round(float(warn_file_size_mb), 1) if warn_file_size_mb is not None else None,
        max_file_size_mb=round(float(resolved_max_file_size_mb), 1) if resolved_max_file_size_mb is not None else None,
        warnings=warnings,
    )


def enforce_pdf_preflight(preflight: PdfPreflight) -> PdfPreflight:
    if preflight.max_page_count is not None and preflight.page_count > preflight.max_page_count:
        raise InputGateError(
            f"Input gate rejected {preflight.source_pdf.name}: page count {preflight.page_count} "
            f"exceeds limit {preflight.max_page_count} for profile '{preflight.profile_name}'.",
            preflight=preflight,
        )
    if preflight.max_file_size_mb is not None and preflight.file_size_mb > preflight.max_file_size_mb:
        raise InputGateError(
            f"Input gate rejected {preflight.source_pdf.name}: file size {preflight.file_size_mb:.1f}MB "
            f"exceeds limit {preflight.max_file_size_mb:.1f}MB for profile '{preflight.profile_name}'.",
            preflight=preflight,
        )
    return preflight


def _visible_text_chars(markdown: str) -> int:
    cleaned = markdown.replace("<!-- image -->", " ")
    cleaned = "".join(char if not char.isspace() else " " for char in cleaned)
    cleaned = " ".join(cleaned.split())
    return len(cleaned)


def _enforce_text_layer(normalized: NormalizedDocument, preflight: PdfPreflight) -> None:
    text_layer_chars = _visible_text_chars(normalized.reconstructed_markdown)
    image_marker_count = normalized.raw_markdown.count("<!-- image -->")
    preflight.text_layer_chars = text_layer_chars
    preflight.image_marker_count = image_marker_count
    effective_page_count = preflight.ingest_page_count or preflight.page_count

    if text_layer_chars >= max(400, effective_page_count * 30):
        return
    if image_marker_count < max(12, effective_page_count):
        return

    preflight.warnings.append(
        "No usable embedded text layer detected; document appears scan-like and falls outside the non-OCR input policy."
    )
    raise InputGateError(
        f"Input gate rejected {preflight.source_pdf.name}: no usable embedded text layer detected; "
        "scan-like PDFs are not supported by the non-OCR pipeline.",
        preflight=preflight,
    )


def _ingest_worker(source_pdf: str, output_dir_str: str | None, profile: str, queue: mp.Queue) -> None:
    try:
        source_path = Path(source_pdf)
        if source_path.suffix.lower() == ".epub":
            normalized = ingest_document(source_path)
        else:
            output_dir = Path(output_dir_str) if output_dir_str else None
            normalized = ingest_pdf(source_path, output_dir=output_dir, profile=profile)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="pdf-translator-ingest-",
            delete=False,
        ) as handle:
            json.dump(
                {
                    "source_pdf": str(normalized.source_pdf),
                    "raw_markdown": normalized.raw_markdown,
                    "reconstructed_markdown": normalized.reconstructed_markdown,
                    "structured": normalized.structured,
                    "detected_language": normalized.detected_language,
                    "images_dir": str(normalized.images_dir) if normalized.images_dir else None,
                },
                handle,
                ensure_ascii=False,
            )
            result_path = handle.name
        queue.put({"ok": True, "result_path": result_path})
    except Exception as exc:
        queue.put(
            {
                "ok": False,
                "error_type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        )


def _terminate_process(process: mp.Process) -> None:
    if not process.is_alive():
        return
    process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)


def _build_pdf_subset(source_pdf: Path, *, page_limit: int) -> tuple[Path, int]:
    import pypdfium2 as pdfium

    if page_limit <= 0:
        raise IngestExecutionError(f"Invalid soft page limit {page_limit} for {source_pdf.name}.")

    source_document = pdfium.PdfDocument(str(source_pdf))
    target_document = pdfium.PdfDocument.new()
    subset_path = Path(
        tempfile.NamedTemporaryFile(
            suffix=".pdf",
            prefix="pdf-translator-subset-",
            delete=False,
        ).name
    )
    try:
        page_count = len(source_document)
        selected_count = min(page_limit, page_count)
        target_document.import_pages(source_document, pages=list(range(selected_count)))
        target_document.save(str(subset_path))
        return subset_path, selected_count
    except Exception as exc:
        subset_path.unlink(missing_ok=True)
        raise IngestExecutionError(f"Unable to build page-limited PDF for {source_pdf.name}: {exc}") from exc
    finally:
        target_document.close()
        source_document.close()


def ingest_pdf_guarded(
    source_pdf: Path,
    *,
    profile_name: str = "auto",
    timeout_seconds: int | None = DEFAULT_INGEST_TIMEOUT_SECONDS,
    max_file_size_mb: float | None = None,
    max_page_count: int | None = None,
    soft_input_gate: bool = False,
    soft_page_limit: int | None = None,
    output_dir: Path | None = None,
) -> tuple[NormalizedDocument, PdfPreflight]:
    preflight = inspect_pdf_preflight(
        source_pdf,
        profile_name=profile_name,
        max_file_size_mb=max_file_size_mb,
        max_page_count=max_page_count,
    )
    ingest_source = source_pdf
    ingest_source_is_temp = False

    if soft_page_limit is not None and soft_page_limit > 0 and preflight.page_count > soft_page_limit:
        subset_path, selected_count = _build_pdf_subset(source_pdf, page_limit=soft_page_limit)
        ingest_source = subset_path
        ingest_source_is_temp = True
        preflight.soft_page_limit = soft_page_limit
        preflight.ingest_page_count = selected_count
        preflight.warnings.append(
            f"Soft gate applied: processing first {selected_count} pages out of {preflight.page_count} total pages."
        )
    else:
        preflight.ingest_page_count = preflight.page_count

    if soft_input_gate:
        preflight.warnings.append(
            "Soft input gate enabled: oversize or high-page PDFs are accepted when page-limited ingest is applied."
        )
    else:
        preflight = enforce_pdf_preflight(preflight)

    if timeout_seconds is None or timeout_seconds <= 0:
        try:
            if ingest_source.suffix.lower() == ".epub":
                normalized = ingest_document(ingest_source)
            else:
                normalized = ingest_pdf(ingest_source, output_dir=output_dir, profile=profile_name)
            normalized.source_pdf = source_pdf
            _enforce_text_layer(normalized, preflight)
            return normalized, preflight
        finally:
            if ingest_source_is_temp:
                ingest_source.unlink(missing_ok=True)

    context = mp.get_context("spawn")
    queue: mp.Queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_ingest_worker,
        args=(str(ingest_source), str(output_dir) if output_dir else None, profile_name, queue),
    )
    process.start()
    process.join(timeout_seconds)

    try:
        if process.is_alive():
            _terminate_process(process)
            queue.cancel_join_thread()
            raise IngestTimeoutError(
                f"Ingest timed out after {timeout_seconds}s for {source_pdf.name}.",
                preflight=preflight,
            )

        if queue.empty():
            queue.cancel_join_thread()
            raise IngestExecutionError(
                f"Ingest exited without returning a result for {source_pdf.name} (exit code {process.exitcode}).",
                preflight=preflight,
            )

        payload = queue.get()
    finally:
        queue.close()
        process.close()
        if ingest_source_is_temp:
            ingest_source.unlink(missing_ok=True)

    if not payload.get("ok"):
        message = payload.get("message") or "Unknown ingest failure."
        error_type = payload.get("error_type") or "UnknownError"
        raise IngestExecutionError(
            f"Ingest failed for {source_pdf.name} with {error_type}: {message}",
            preflight=preflight,
        )

    result_path = Path(payload["result_path"])
    payload_data = json.loads(result_path.read_text(encoding="utf-8"))
    result_path.unlink(missing_ok=True)
    images_dir_str = payload_data.get("images_dir")
    normalized = NormalizedDocument(
        source_pdf=Path(payload_data["source_pdf"]),
        raw_markdown=payload_data["raw_markdown"],
        reconstructed_markdown=payload_data["reconstructed_markdown"],
        structured=payload_data["structured"],
        detected_language=payload_data["detected_language"],
        images_dir=Path(images_dir_str) if images_dir_str else None,
    )
    normalized.source_pdf = source_pdf
    _enforce_text_layer(normalized, preflight)
    return normalized, preflight
