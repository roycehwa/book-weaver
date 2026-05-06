from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_translator.config import DEFAULT_TRANSLATION_CONCURRENCY, RunSettings
from pdf_translator.guardrails import (
    DEFAULT_INGEST_TIMEOUT_SECONDS,
    IngestGuardrailError,
    ingest_pdf_guarded,
)
from pdf_translator.knowledge import (
    emit_mindmap_mermaid_from_book,
    emit_wiki_outline_from_book,
    load_book_json,
)
from pdf_translator.pipeline import run_translation_pipeline
from pdf_translator.profile import build_document_profile
from pdf_translator.validation import run_validation_manifest, write_validation_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-translator",
        description="Translate PDFs through a normalized Markdown/JSON pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    translate_parser = subparsers.add_parser(
        "translate",
        help="Ingest, translate, and render a clean translated PDF.",
    )
    translate_parser.add_argument(
        "source_pdf",
        type=Path,
        help="Path to source document: PDF (Docling) or EPUB (spine XHTML).",
    )
    translate_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs"),
        help="Base directory for pipeline artifacts.",
    )
    translate_parser.add_argument(
        "--target-lang",
        required=True,
        help="Target language, for example zh-CN or English.",
    )
    translate_parser.add_argument(
        "--source-lang",
        default=None,
        help="Optional source language. If omitted, language will be auto-detected.",
    )
    translate_parser.add_argument(
        "--translator",
        default="minimax",
        choices=["openai", "mock", "minimax", "compatible", "openai-compatible"],
        help="Translation backend. minimax uses MiniMax Anthropic Messages; compatible uses OpenAI-style chat completions.",
    )
    translate_parser.add_argument(
        "--max-chunk-chars",
        type=int,
        default=9000,
        help="Max chunk size for translation requests.",
    )
    translate_parser.add_argument(
        "--translation-concurrency",
        type=int,
        default=DEFAULT_TRANSLATION_CONCURRENCY,
        help="Number of translation chunks to process concurrently.",
    )
    translate_parser.add_argument(
        "--profile",
        default="auto",
        choices=["auto", "magazine", "book"],
        help="Profile used for ingest guardrails.",
    )
    translate_parser.add_argument(
        "--format",
        default="epub",
        choices=["pdf", "epub", "both"],
        help="Rendered output format. EPUB is the default reading output.",
    )
    translate_parser.add_argument(
        "--ingest-timeout-seconds",
        type=int,
        default=DEFAULT_INGEST_TIMEOUT_SECONDS,
        help="Hard timeout for the ingest stage. Use 0 to disable.",
    )
    translate_parser.add_argument(
        "--max-file-size-mb",
        type=float,
        default=None,
        help="Override the hard input gate for file size in MB.",
    )
    translate_parser.add_argument(
        "--max-page-count",
        type=int,
        default=None,
        help="Override the hard input gate for page count.",
    )

    profile_parser = subparsers.add_parser(
        "profile",
        help="Profile a PDF and classify pages as accept, skip, or reject.",
    )
    profile_parser.add_argument(
        "source_pdf",
        type=Path,
        help="Path to source document: PDF or EPUB.",
    )
    profile_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs"),
        help="Base directory for profile artifacts.",
    )
    profile_parser.add_argument(
        "--profile",
        default="auto",
        choices=["auto", "magazine", "book"],
        help="Document profile used for page gating.",
    )
    profile_parser.add_argument(
        "--ingest-timeout-seconds",
        type=int,
        default=DEFAULT_INGEST_TIMEOUT_SECONDS,
        help="Hard timeout for the ingest stage. Use 0 to disable.",
    )
    profile_parser.add_argument(
        "--max-file-size-mb",
        type=float,
        default=None,
        help="Override the hard input gate for file size in MB.",
    )
    profile_parser.add_argument(
        "--max-page-count",
        type=int,
        default=None,
        help="Override the hard input gate for page count.",
    )

    validate_parser = subparsers.add_parser(
        "validate",
        help="Run a batch validation manifest and summarize pass rates.",
    )
    validate_parser.add_argument(
        "manifest",
        type=Path,
        help="Path to a JSON manifest containing profile validation cases (book/magazine/auto).",
    )
    validate_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs"),
        help="Base directory for profile artifacts and validation reports.",
    )
    validate_parser.add_argument(
        "--report-name",
        default=None,
        help="Optional report filename. Defaults to <manifest-stem>-report.json.",
    )
    validate_parser.add_argument(
        "--no-reuse-existing",
        action="store_true",
        help="Recompute artifacts instead of reusing existing profile/articles JSON files.",
    )
    validate_parser.add_argument(
        "--ingest-timeout-seconds",
        type=int,
        default=DEFAULT_INGEST_TIMEOUT_SECONDS,
        help="Default hard timeout for ingest when a case is recomputed. Use 0 to disable.",
    )
    validate_parser.add_argument(
        "--max-file-size-mb",
        type=float,
        default=None,
        help="Default hard input gate for file size in MB when a case is recomputed.",
    )
    validate_parser.add_argument(
        "--max-page-count",
        type=int,
        default=None,
        help="Default hard input gate for page count when a case is recomputed.",
    )

    knowledge_parser = subparsers.add_parser(
        "knowledge",
        help="Branch B stubs: wiki outline and Mermaid mindmap from book.json.",
    )
    knowledge_sub = knowledge_parser.add_subparsers(dest="knowledge_command", required=True)
    wiki_outline = knowledge_sub.add_parser(
        "wiki-outline",
        help="Write per-chapter Markdown stubs and index.md under a directory.",
    )
    wiki_outline.add_argument(
        "--book-json",
        type=Path,
        required=True,
        help="Path to book.json from a translate run.",
    )
    wiki_outline.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory for wiki Markdown files.",
    )
    mindmap_cmd = knowledge_sub.add_parser(
        "mindmap",
        help="Write a Mermaid mindmap file listing chapter titles.",
    )
    mindmap_cmd.add_argument(
        "--book-json",
        type=Path,
        required=True,
        help="Path to book.json from a translate run.",
    )
    mindmap_cmd.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output path for the .md file containing a fenced mermaid block.",
    )

    return parser


def _print_preflight(preflight: dict[str, object]) -> None:
    processed_page_count = preflight.get("ingest_page_count")
    page_count = preflight["page_count"]
    page_part = f"pages={page_count}"
    if isinstance(processed_page_count, int) and processed_page_count > 0 and processed_page_count != page_count:
        page_part += f" processed_pages={processed_page_count}"

    print(
        "Preflight: "
        f"{page_part} "
        f"size_mb={preflight['file_size_mb']} "
        f"profile={preflight['profile_name']}"
    )
    warnings = preflight.get("warnings") or []
    for warning in warnings:
        print(f"Warning: {warning}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "translate":
            settings = RunSettings(
                source_pdf=args.source_pdf.expanduser().resolve(),
                output_dir=args.output_dir.expanduser().resolve(),
                target_language=args.target_lang,
                source_language=args.source_lang,
                translator=args.translator,
                max_chunk_chars=args.max_chunk_chars,
                translation_concurrency=args.translation_concurrency,
                profile_name=args.profile,
                output_format=args.format,
                ingest_timeout_seconds=args.ingest_timeout_seconds,
                max_file_size_mb=args.max_file_size_mb,
                max_page_count=args.max_page_count,
            )
            artifacts = run_translation_pipeline(settings)
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
            _print_preflight(manifest["preflight"])
            print(f"Artifacts written to: {artifacts.output_dir}")
            files = manifest.get("files", {})
            if "translated_epub" in files:
                print(f"Translated EPUB: {files['translated_epub']}")
            if "translated_pdf" in files:
                print(f"Translated PDF: {files['translated_pdf']}")
        elif args.command == "profile":
            source_pdf = args.source_pdf.expanduser().resolve()
            normalized, preflight = ingest_pdf_guarded(
                source_pdf,
                profile_name=args.profile,
                timeout_seconds=args.ingest_timeout_seconds,
                max_file_size_mb=args.max_file_size_mb,
                max_page_count=args.max_page_count,
            )
            profile = build_document_profile(source_pdf, normalized.structured, profile_name=args.profile)
            profile["preflight"] = preflight.as_dict()
            output_dir = args.output_dir.expanduser().resolve() / source_pdf.stem
            output_dir.mkdir(parents=True, exist_ok=True)
            profile_path = output_dir / "profile.json"
            profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Profile written to: {profile_path}")
            _print_preflight(profile["preflight"])
            print(
                "Actions: "
                f"accept={profile['actions']['accept']} "
                f"assist={profile['actions']['assist']} "
                f"skip_content={profile['actions']['skip_content']} "
                f"reject_structure={profile['actions']['reject_structure']}"
            )
            print(f"Profile: {profile['profile']}")
            print(f"Document action: {profile['document_action']}")
        elif args.command == "validate":
            manifest_path = args.manifest.expanduser().resolve()
            output_dir = args.output_dir.expanduser().resolve()
            report = run_validation_manifest(
                manifest_path=manifest_path,
                output_dir=output_dir,
                reuse_existing=not args.no_reuse_existing,
                ingest_timeout_seconds=args.ingest_timeout_seconds,
                max_file_size_mb=args.max_file_size_mb,
                max_page_count=args.max_page_count,
            )
            report_name = args.report_name or f"{manifest_path.stem}-report.json"
            report_path = output_dir / "validation" / report_name
            write_validation_report(report, report_path)
            print(f"Validation report written to: {report_path}")
            print(
                "Summary: "
                f"passed={report['passed_cases']}/{report['total_cases']} "
                f"pass_rate={report['pass_rate']:.1%}"
            )
            if report["failure_types"]:
                print(f"Failure types: {report['failure_types']}")
            for case in report["cases"]:
                status = "PASS" if case["passed"] else "FAIL"
                if "failure" in case:
                    print(f"{status} {case['profile']} {case['name']} :: {case['failure']['type']}")
                else:
                    print(f"{status} {case['profile']} {case['name']}")
        elif args.command == "knowledge":
            book_path = args.book_json.expanduser().resolve()
            book = load_book_json(book_path)
            if args.knowledge_command == "wiki-outline":
                out = args.out.expanduser().resolve()
                emit_wiki_outline_from_book(book, out)
                print(f"Wiki outline written under: {out}")
            elif args.knowledge_command == "mindmap":
                out = args.out.expanduser().resolve()
                emit_mindmap_mermaid_from_book(book, out)
                print(f"Mermaid mindmap written to: {out}")
    except IngestGuardrailError as exc:
        print(str(exc))
        if exc.preflight is not None:
            _print_preflight(exc.preflight.as_dict())
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
