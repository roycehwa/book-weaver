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
    NETWORK_MODELS,
    apply_user_review,
    build_knowledge_extraction,
    build_knowledge_package,
    build_knowledge_plan,
    build_metadata_prior,
    build_reader_brief,
    build_suitability_report,
    emit_mindmap_mermaid_from_book,
    emit_wiki_outline_from_book,
    ingest_reader_feedback,
    load_book_json,
)
from pdf_translator.lifecycle import cleanup_run, finalize_run
from pdf_translator.pipeline import (
    run_intake_pipeline,
    run_translation_pipeline,
    safe_delivery_file_stem,
)
from pdf_translator.polish import run_polish
from pdf_translator.profile import build_document_profile
from pdf_translator.render import render_pdf_from_markdown
from pdf_translator.review import (
    apply_review_state,
    review_project_from_run,
    rewrite_review_requests,
    summarize_review_state,
    translated_segments_to_chapters,
    write_versioned_outputs,
)
from pdf_translator.epub import render_epub_from_book, validate_epub_internal_hrefs
from pdf_translator.translate import build_translator
from pdf_translator.validation import run_validation_manifest, write_validation_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="book-weaver",
        description="Ingest books, translate when needed, and prepare reading and knowledge artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    intake_parser = subparsers.add_parser(
        "intake",
        help="Ingest a book and build BookIR without translation.",
    )
    intake_parser.add_argument(
        "source_pdf",
        type=Path,
        help="Path to source document: PDF (Docling) or EPUB (spine XHTML).",
    )
    intake_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs"),
        help="Base directory for intake artifacts.",
    )
    intake_parser.add_argument(
        "--source-lang",
        default=None,
        help="Optional source language. If omitted, language will be auto-detected.",
    )
    intake_parser.add_argument(
        "--max-chunk-chars",
        type=int,
        default=9000,
        help="Chunk-size estimate used in chapter reports.",
    )
    intake_parser.add_argument(
        "--profile",
        default="book",
        choices=["auto", "magazine", "book"],
        help="Profile used for ingest guardrails. Defaults to book for the mainline workflow.",
    )
    intake_parser.add_argument(
        "--ingest-timeout-seconds",
        type=int,
        default=DEFAULT_INGEST_TIMEOUT_SECONDS,
        help="Hard timeout for the ingest stage. Use 0 to disable.",
    )
    intake_parser.add_argument(
        "--max-file-size-mb",
        type=float,
        default=None,
        help="Override the hard input gate for file size in MB.",
    )
    intake_parser.add_argument(
        "--max-page-count",
        type=int,
        default=None,
        help="Override the hard input gate for page count.",
    )

    translate_parser = subparsers.add_parser(
        "translate",
        help="Ingest, optionally translate, and render a clean reading edition.",
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

    polish_parser = subparsers.add_parser(
        "polish",
        help="Post-edit an existing book translation run without retranslating the whole book.",
    )
    polish_parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed translate run containing book.json and translated.md.",
    )
    polish_parser.add_argument(
        "--target-lang",
        default="zh-CN",
        help="Target language, for example zh-CN.",
    )
    polish_parser.add_argument(
        "--translator",
        default="minimax",
        choices=["openai", "mock", "minimax", "compatible", "openai-compatible"],
        help="Translation backend used for polish requests.",
    )
    polish_parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Number of candidate lines per polish request.",
    )
    polish_parser.add_argument(
        "--concurrency",
        type=int,
        default=6,
        help="Number of polish batches to run concurrently.",
    )
    polish_parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=None,
        help="Per-request polish timeout. Defaults to the translator timeout, e.g. MINIMAX_HTTP_TIMEOUT_SECONDS.",
    )

    finalize_parser = subparsers.add_parser(
        "finalize",
        help="Write phase_a_status.json for a completed intake or translate run.",
    )
    finalize_parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed intake or translate run.",
    )

    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="Remove temporary intake/translation artifacts after a run has been accepted.",
    )
    cleanup_parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed intake or translate run.",
    )
    cleanup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only write cleanup-dry-run.json and print what would be removed.",
    )
    cleanup_parser.add_argument(
        "--keep-caches",
        action="store_true",
        help="Keep translation-cache/ and polish-cache/ even during cleanup.",
    )

    review_parser = subparsers.add_parser(
        "review",
        help="Inspect, rewrite, and export reviewed translation versions.",
    )
    review_sub = review_parser.add_subparsers(dest="review_command", required=True)
    review_status = review_sub.add_parser(
        "status",
        help="Show machine pre-review and human review state for a translation run.",
    )
    review_status.add_argument("run_dir", type=Path)
    review_rewrite = review_sub.add_parser(
        "rewrite",
        help="Generate model rewrite candidates requested in review_state.json.",
    )
    review_rewrite.add_argument("run_dir", type=Path)
    review_rewrite.add_argument("--target-lang", default="zh-CN")
    review_rewrite.add_argument("--source-lang", default=None)
    review_rewrite.add_argument(
        "--translator",
        default="minimax",
        choices=["openai", "mock", "minimax", "compatible", "openai-compatible"],
    )
    review_rewrite.add_argument("--segment-id", default=None)
    review_export = review_sub.add_parser(
        "export",
        help="Apply approved segment decisions and export a versioned translation.",
    )
    review_export.add_argument("run_dir", type=Path)
    review_export.add_argument("--version", required=True)
    review_export.add_argument("--parent-version", default=None)
    review_export.add_argument("--target-lang", default="zh-CN")
    review_export.add_argument("--format", default="epub", choices=["pdf", "epub", "both"])
    review_export.add_argument(
        "--approve",
        action="store_true",
        help="Mark this reviewed version approved for Phase B consumption.",
    )

    knowledge_parser = subparsers.add_parser(
        "knowledge",
        help="Build and export Phase B knowledge artifacts.",
    )
    knowledge_sub = knowledge_parser.add_subparsers(dest="knowledge_command", required=True)
    knowledge_build = knowledge_sub.add_parser(
        "build",
        help="Build deterministic chapters, semantic units, assets, and source map from an intake/translate run.",
    )
    knowledge_build.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed intake or translate run containing book.json.",
    )
    knowledge_build.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to RUN_DIR/knowledge.",
    )
    knowledge_suitability = knowledge_sub.add_parser(
        "suitability",
        help="Generate a rule-based profile, risk, and chapter plan before model extraction.",
    )
    knowledge_suitability.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed intake or translate run containing book.json.",
    )
    knowledge_suitability.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to RUN_DIR/knowledge.",
    )
    knowledge_metadata = knowledge_sub.add_parser(
        "metadata",
        help="Search public book metadata and generate a weak network-model prior.",
    )
    knowledge_metadata.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed intake or translate run containing manifest.json.",
    )
    knowledge_metadata.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to RUN_DIR/knowledge.",
    )
    knowledge_metadata.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh metadata lookup even if metadata-prior.json already exists.",
    )
    knowledge_metadata.add_argument(
        "--timeout-seconds",
        type=float,
        default=8.0,
        help="Per-provider metadata lookup timeout.",
    )
    knowledge_plan = knowledge_sub.add_parser(
        "plan",
        help="Generate a network-oriented processing plan before knowledge extraction.",
    )
    knowledge_plan.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed intake or translate run containing book.json.",
    )
    knowledge_plan.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to RUN_DIR/knowledge.",
    )
    knowledge_plan.add_argument(
        "--planner",
        default="rule",
        choices=["rule"],
        help="Planning backend. rule is deterministic; model adjudication will be added behind the same schema.",
    )
    knowledge_plan.add_argument(
        "--metadata-prior",
        default="none",
        choices=["none", "auto"],
        help="Use cached or freshly searched book metadata as a weak planning prior.",
    )
    knowledge_review = knowledge_sub.add_parser(
        "review",
        help="Apply one user-supplied structural review answer file to the knowledge plan.",
    )
    knowledge_review.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed intake or translate run containing book.json.",
    )
    knowledge_review.add_argument(
        "--answers",
        type=Path,
        required=True,
        help="Plain-text user answers covering organization, preserve/skip content types, and optional references.",
    )
    knowledge_review.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to RUN_DIR/knowledge.",
    )
    knowledge_brief = knowledge_sub.add_parser(
        "brief",
        help="Render the reader-facing Phase B brief and feedback template.",
    )
    knowledge_brief.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed intake or translate run containing book.json.",
    )
    knowledge_brief.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to RUN_DIR/knowledge.",
    )
    knowledge_feedback = knowledge_sub.add_parser(
        "feedback",
        help="Preserve reader feedback and write first-pass alignment objects.",
    )
    knowledge_feedback.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed intake or translate run containing a knowledge package.",
    )
    knowledge_feedback.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Markdown or plain-text feedback file based on feedback-template.md.",
    )
    knowledge_feedback.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to RUN_DIR/knowledge.",
    )
    knowledge_extract = knowledge_sub.add_parser(
        "extract",
        help="Run a profile-specific knowledge extractor. The first implemented extractor is argument_network.",
    )
    knowledge_extract.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed intake or translate run containing knowledge plan inputs.",
    )
    knowledge_extract.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output directory. Defaults to RUN_DIR/knowledge.",
    )
    knowledge_extract.add_argument(
        "--network-model",
        default=None,
        choices=sorted(NETWORK_MODELS.keys()),
        help="Override the planned network model. Defaults to knowledge/plan.json final_plan.",
    )
    wiki_outline = knowledge_sub.add_parser(
        "wiki-outline",
        help="Write per-chapter Markdown stubs and index.md under a directory.",
    )
    wiki_outline.add_argument(
        "--book-json",
        type=Path,
        required=True,
        help="Path to book.json from an intake or translate run.",
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
        help="Path to book.json from an intake or translate run.",
    )
    mindmap_cmd.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output path for the .md file containing a fenced mermaid block.",
    )

    return parser


def _run_review_export(
    *,
    run_dir: Path,
    version_name: str,
    parent_version: str | None,
    target_language: str,
    output_format: str,
    approve: bool,
) -> dict[str, object]:
    run_dir = run_dir.expanduser().resolve()
    project = review_project_from_run(run_dir)
    applied_segments = apply_review_state(project["translated_segments"], project["review_state"])
    version = write_versioned_outputs(
        run_dir=run_dir,
        version_name=version_name,
        target_language=target_language,
        translated_segments=applied_segments,
        parent_version=parent_version,
        approval_status="approved" if approve else "draft",
    )
    version_dir = Path(version["version_dir"])
    translated_markdown_path = Path(version["translated_markdown_path"])
    translated_markdown = translated_markdown_path.read_text(encoding="utf-8")
    rendered_files: dict[str, object] = {}

    manifest = {}
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_name = Path(str(manifest.get("source_pdf") or run_dir.name)).stem
    book = {
        "metadata": {"schema": "review_export_fallback"},
        "chapters": translated_segments_to_chapters(applied_segments),
    }
    if (run_dir / "book.json").exists():
        book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))

    delivery_stem = safe_delivery_file_stem(Path(source_name), target_language)
    if output_format in {"pdf", "both"}:
        pdf_path = version_dir / f"{delivery_stem}.pdf"
        render_pdf_from_markdown(
            title=f"{source_name} ({target_language})",
            markdown_text=translated_markdown,
            output_path=pdf_path,
        )
        rendered_files["translated_pdf"] = str(pdf_path)
    if output_format in {"epub", "both"}:
        epub_path = version_dir / f"{delivery_stem}.epub"
        render_epub_from_book(
            book=book,
            translated_chapters=translated_segments_to_chapters(applied_segments),
            output_path=epub_path,
            title=f"{source_name} ({target_language})",
            language=target_language,
        )
        rendered_files["translated_epub"] = str(epub_path)
        rendered_files["epub_href_validation"] = validate_epub_internal_hrefs(epub_path)

    version_manifest_path = version_dir / "version-manifest.json"
    version_manifest = json.loads(version_manifest_path.read_text(encoding="utf-8"))
    version_manifest["render"] = {"format": output_format}
    version_manifest["files"].update(
        {
            key: str(Path(value).relative_to(run_dir)) if isinstance(value, str) else value
            for key, value in rendered_files.items()
        }
    )
    version_manifest_path.write_text(
        json.dumps(version_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"version": version, "rendered_files": rendered_files}


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
        if args.command == "intake":
            settings = RunSettings(
                source_pdf=args.source_pdf.expanduser().resolve(),
                output_dir=args.output_dir.expanduser().resolve(),
                target_language=args.source_lang or "source",
                source_language=args.source_lang,
                translator="none",
                max_chunk_chars=args.max_chunk_chars,
                profile_name=args.profile,
                output_format="none",
                ingest_timeout_seconds=args.ingest_timeout_seconds,
                max_file_size_mb=args.max_file_size_mb,
                max_page_count=args.max_page_count,
            )
            artifacts = run_intake_pipeline(settings)
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
            _print_preflight(manifest["preflight"])
            print(f"Intake artifacts written to: {artifacts.output_dir}")
            files = manifest.get("files", {})
            if "book_json" in files:
                print(f"BookIR: {files['book_json']}")
            if "book_markdown" in files:
                print(f"Book Markdown: {files['book_markdown']}")
            if "chapter_report" in files:
                print(f"Chapter report: {files['chapter_report']}")
        elif args.command == "translate":
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
        elif args.command == "polish":
            result = run_polish(
                run_dir=args.run_dir,
                target_language=args.target_lang,
                translator_name=args.translator,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                request_timeout_seconds=args.request_timeout_seconds,
            )
            print(f"Polish report: {result.report_path}")
            print(f"Polished Markdown: {result.polished_markdown_path}")
            print(f"Polished EPUB: {result.polished_epub_path}")
            print(
                "Polish summary: "
                f"candidates={result.candidate_count} "
                f"accepted={result.accepted_count} "
                f"rejected={result.rejected_count}"
            )
        elif args.command == "finalize":
            result = finalize_run(args.run_dir)
            status = result["status"]
            print(f"Phase A status: {result['status_path']}")
            print(
                "Finalize summary: "
                f"status={status['status']} "
                f"ready_for_phase_b={status['ready_for_phase_b']} "
                f"chapters={status['chapter_count']} "
                f"chapter_id_coverage={status['chapter_id_coverage']}"
            )
        elif args.command == "cleanup":
            result = cleanup_run(
                args.run_dir,
                dry_run=args.dry_run,
                include_caches=not args.keep_caches,
            )
            report = result["report"]
            count = len(report["would_remove"] if report["dry_run"] else report["removed"])
            print(f"Cleanup report: {result['report_path']}")
            print(f"Cleanup summary: dry_run={report['dry_run']} count={count}")
        elif args.command == "review":
            if args.review_command == "status":
                project = review_project_from_run(args.run_dir.expanduser().resolve())
                pre_review = project["pre_review"]
                state = project["review_state"]
                summary = summarize_review_state(project["review_items"], state)
                print(
                    "Pre-review: "
                    f"segments={pre_review.get('total_segments', 0)} "
                    f"flagged={pre_review.get('flagged_segments', 0)} "
                    f"clean={pre_review.get('clean_segments', 0)}"
                )
                print(
                    "Human review: "
                    f"mode={(state.get('workflow') or {}).get('human_review_mode')} "
                    f"open={summary.get('open_items', 0)} "
                    f"approved={summary.get('approved_items', 0)} "
                    f"resolved={summary.get('resolved_items', 0)}"
                )
            elif args.review_command == "rewrite":
                result = rewrite_review_requests(
                    run_dir=args.run_dir.expanduser().resolve(),
                    translator=build_translator(args.translator),
                    source_language=args.source_lang,
                    target_language=args.target_lang,
                    segment_id=args.segment_id,
                )
                print(f"Rewrite candidates generated: {result['rewritten_count']}")
                print(f"Review state updated: {result['review_state_path']}")
            elif args.review_command == "export":
                result = _run_review_export(
                    run_dir=args.run_dir,
                    version_name=args.version,
                    parent_version=args.parent_version,
                    target_language=args.target_lang,
                    output_format=args.format,
                    approve=args.approve,
                )
                version = result["version"]
                print(f"Reviewed version: {version['version_dir']}")
                print(f"Reviewed Markdown: {version['translated_markdown_path']}")
                print(f"Approval status: {'approved' if args.approve else 'draft'}")
                rendered_files = result["rendered_files"]
                if "translated_epub" in rendered_files:
                    print(f"Reviewed EPUB: {rendered_files['translated_epub']}")
                if "translated_pdf" in rendered_files:
                    print(f"Reviewed PDF: {rendered_files['translated_pdf']}")
        elif args.command == "knowledge":
            if args.knowledge_command == "build":
                paths = build_knowledge_package(args.run_dir, out_dir=args.out)
                print(f"Knowledge manifest: {paths['manifest']}")
                print(f"Chapters: {paths['chapters']}")
                print(f"Semantic units: {paths['semantic_units']}")
                print(f"Bilingual input: {paths['bilingual_input']}")
                print(f"Bilingual summary: {paths['bilingual_input_markdown']}")
                print(f"Assets: {paths['assets']}")
                print(f"Source map: {paths['source_map']}")
            elif args.knowledge_command == "suitability":
                paths = build_suitability_report(args.run_dir, out_dir=args.out)
                print(f"Suitability report: {paths['report']}")
                print(f"Suitability Markdown: {paths['markdown']}")
            elif args.knowledge_command == "metadata":
                paths = build_metadata_prior(
                    args.run_dir,
                    out_dir=args.out,
                    refresh=args.refresh,
                    timeout_seconds=args.timeout_seconds,
                )
                print(f"Metadata prior: {paths['prior']}")
                print(f"Metadata Markdown: {paths['markdown']}")
            elif args.knowledge_command == "plan":
                paths = build_knowledge_plan(
                    args.run_dir,
                    out_dir=args.out,
                    planner=args.planner,
                    metadata_prior=args.metadata_prior,
                )
                print(f"Plan candidates: {paths['candidates']}")
                print(f"Plan JSON: {paths['plan']}")
                print(f"Plan Markdown: {paths['markdown']}")
            elif args.knowledge_command == "review":
                paths = apply_user_review(args.run_dir, args.answers, out_dir=args.out)
                print(f"User review: {paths['review']}")
                print(f"Reference prior: {paths['reference_prior']}")
                print(f"Plan JSON: {paths['plan']}")
                print(f"Plan Markdown: {paths['markdown']}")
            elif args.knowledge_command == "brief":
                paths = build_reader_brief(args.run_dir, out_dir=args.out)
                print(f"Reader brief: {paths['markdown']}")
                print(f"Reader brief HTML: {paths['html']}")
                print(f"Feedback template: {paths['template']}")
            elif args.knowledge_command == "feedback":
                paths = ingest_reader_feedback(args.run_dir, args.input, out_dir=args.out)
                print(f"Raw feedback Markdown: {paths['raw_markdown']}")
                print(f"Raw feedback JSON: {paths['raw']}")
                print(f"Aligned feedback: {paths['aligned']}")
                if "review" in paths:
                    print(f"User review: {paths['review']}")
                    print(f"Reference prior: {paths['reference_prior']}")
                    print(f"Plan JSON: {paths['plan']}")
                    print(f"Plan Markdown: {paths['plan_markdown']}")
            elif args.knowledge_command == "extract":
                paths = build_knowledge_extraction(
                    args.run_dir,
                    out_dir=args.out,
                    network_model=args.network_model,
                )
                print(f"Extraction manifest: {paths['manifest']}")
                print(f"Extracted nodes: {paths['nodes']}")
                print(f"Extracted edges: {paths['edges']}")
                print(f"Extraction report: {paths['report']}")
            elif args.knowledge_command == "wiki-outline":
                book_path = args.book_json.expanduser().resolve()
                book = load_book_json(book_path)
                out = args.out.expanduser().resolve()
                emit_wiki_outline_from_book(book, out)
                print(f"Wiki outline written under: {out}")
            elif args.knowledge_command == "mindmap":
                book_path = args.book_json.expanduser().resolve()
                book = load_book_json(book_path)
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
