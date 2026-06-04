from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_translator.agent_runner import DEFAULT_AGENT_SOURCE_ROOT, AgentLockError, run_agent_once
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
from pdf_translator.epub import render_epub_from_book, validate_epub_internal_hrefs
from pdf_translator.pipeline import safe_delivery_file_stem, run_translation_pipeline
from pdf_translator.polish import run_polish
from pdf_translator.render import render_pdf_from_markdown
from pdf_translator.review import (
    apply_review_state,
    review_project_from_run,
    rewrite_review_requests,
    translated_segments_to_chapters,
    write_versioned_outputs,
)
from pdf_translator.table_translate import run_translate_tables
from pdf_translator.profile import build_document_profile
from pdf_translator.translate import build_translator
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

    table_parser = subparsers.add_parser(
        "translate-tables",
        help="Translate preserved English markdown tables in a completed run and rebuild EPUBs.",
    )
    table_parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed translate run containing translated-chapters.json.",
    )
    table_parser.add_argument(
        "--target-lang",
        default="zh-CN",
        help="Target language, for example zh-CN.",
    )
    table_parser.add_argument(
        "--source-lang",
        default="en",
        help="Source language for table content.",
    )
    table_parser.add_argument(
        "--translator",
        default="minimax",
        choices=["openai", "mock", "minimax", "compatible", "openai-compatible"],
        help="Translation backend used for table requests.",
    )

    review_export_parser = subparsers.add_parser(
        "review-export",
        help="Apply review_state.json and export a versioned reviewed Markdown/PDF/EPUB.",
    )
    review_export_parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed translate run containing review_state.json.",
    )
    review_export_parser.add_argument(
        "--version",
        required=True,
        help="Version name written under versions/, for example v2 or final.",
    )
    review_export_parser.add_argument(
        "--parent-version",
        default=None,
        help="Optional parent version label recorded in the version manifest.",
    )
    review_export_parser.add_argument(
        "--target-lang",
        default="zh-CN",
        help="Target language, for example zh-CN.",
    )
    review_export_parser.add_argument(
        "--format",
        default="epub",
        choices=["pdf", "epub", "both"],
        help="Rendered output format for the reviewed version.",
    )

    review_rewrite_parser = subparsers.add_parser(
        "review-rewrite",
        help="Generate model rewrite candidates for review_state model_rewrite decisions.",
    )
    review_rewrite_parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to a completed translate run containing review_state.json.",
    )
    review_rewrite_parser.add_argument(
        "--target-lang",
        default="zh-CN",
        help="Target language, for example zh-CN.",
    )
    review_rewrite_parser.add_argument(
        "--source-lang",
        default=None,
        help="Optional source language for rewrite prompts.",
    )
    review_rewrite_parser.add_argument(
        "--translator",
        default="minimax",
        choices=["openai", "mock", "minimax", "compatible", "openai-compatible"],
        help="Translation backend used to generate rewrite candidates.",
    )
    review_rewrite_parser.add_argument(
        "--segment-id",
        default=None,
        help="Rewrite only one segment instead of all open model_rewrite requests.",
    )

    agent_parser = subparsers.add_parser(
        "agent-once",
        help="Hermes Agent entry point: process one book from EN/CN and archive it to OK/NG.",
    )
    agent_parser.add_argument(
        "--source-root",
        type=Path,
        default=DEFAULT_AGENT_SOURCE_ROOT,
        help="Root containing EN, CN, OK, and NG directories.",
    )
    agent_parser.add_argument(
        "--target-lang",
        default="zh-CN",
        help="Target language, for example zh-CN.",
    )
    agent_parser.add_argument(
        "--translator",
        default="minimax",
        choices=["openai", "mock", "minimax", "compatible", "openai-compatible"],
        help="Translation backend for EN books.",
    )
    agent_parser.add_argument(
        "--format",
        default="epub",
        choices=["pdf", "epub", "both"],
        help="Rendered output format. EPUB is the default reading output.",
    )
    agent_parser.add_argument(
        "--max-chunk-chars",
        type=int,
        default=9000,
        help="Max chunk size for translation requests.",
    )
    agent_parser.add_argument(
        "--translation-concurrency",
        type=int,
        default=DEFAULT_TRANSLATION_CONCURRENCY,
        help="Number of translation chunks to process concurrently.",
    )
    agent_parser.add_argument(
        "--ingest-timeout-seconds",
        type=int,
        default=DEFAULT_INGEST_TIMEOUT_SECONDS,
        help="Hard timeout for the ingest stage. Use 0 to disable.",
    )
    agent_parser.add_argument(
        "--max-file-size-mb",
        type=float,
        default=None,
        help="Override the hard input gate for file size in MB.",
    )
    agent_parser.add_argument(
        "--max-page-count",
        type=int,
        default=None,
        help="Override the hard input gate for page count.",
    )
    agent_parser.add_argument(
        "--no-polish",
        action="store_true",
        help="Skip the polish pass for EN books.",
    )
    agent_parser.add_argument(
        "--polish-translator",
        default=None,
        choices=["openai", "mock", "minimax", "compatible", "openai-compatible"],
        help="Optional translator backend for the EN polish pass.",
    )
    agent_parser.add_argument(
        "--source-lane",
        dest="source_lanes",
        action="append",
        choices=["EN", "CN"],
        help="Restrict source lanes for this run. Repeat for multiple lanes. Defaults to EN and CN.",
    )
    agent_parser.add_argument(
        "--max-ng-retries",
        type=int,
        default=2,
        help="Maximum automatic retries for one NG book before newer EN/CN sources are allowed to run.",
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


def _run_review_export(
    *,
    run_dir: Path,
    version_name: str,
    parent_version: str | None,
    target_language: str,
    output_format: str,
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
    )
    version_dir = Path(version["version_dir"])
    translated_markdown_path = Path(version["translated_markdown_path"])
    translated_markdown = translated_markdown_path.read_text(encoding="utf-8")
    rendered_files: dict[str, object] = {}

    manifest_path = run_dir / "manifest.json"
    source_name = run_dir.name
    book = {
        "metadata": {"schema": "review_export_fallback"},
        "chapters": translated_segments_to_chapters(applied_segments),
    }
    if (run_dir / "book.json").exists():
        book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_name = Path(str(manifest.get("source_pdf") or source_name)).stem

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
        translated_chapters = translated_segments_to_chapters(applied_segments)
        render_epub_from_book(
            book=book,
            translated_chapters=translated_chapters,
            output_path=epub_path,
            title=f"{source_name} ({target_language})",
            language=target_language,
        )
        rendered_files["translated_epub"] = str(epub_path)
        rendered_files["epub_href_validation"] = validate_epub_internal_hrefs(epub_path)

    version_manifest_path = version_dir / "version-manifest.json"
    version_manifest = json.loads(version_manifest_path.read_text(encoding="utf-8"))
    version_manifest["render"] = {"format": output_format}
    version_manifest["files"].update(rendered_files)
    version_manifest_path.write_text(json.dumps(version_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"version": version, "rendered_files": rendered_files}


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
        elif args.command == "translate-tables":
            result = run_translate_tables(
                run_dir=args.run_dir,
                target_language=args.target_lang,
                translator_name=args.translator,
                source_language=args.source_lang,
            )
            print(f"Table translation report: {result.report_path}")
            print(
                "Table summary: "
                f"translated={result.translated_table_count} "
                f"skipped={result.skipped_table_count}"
            )
            print(f"Translated EPUB: {result.translated_epub_path}")
            print(f"Polished EPUB: {result.polished_epub_path}")
        elif args.command == "review-export":
            result = _run_review_export(
                run_dir=args.run_dir,
                version_name=args.version,
                parent_version=args.parent_version,
                target_language=args.target_lang,
                output_format=args.format,
            )
            version = result["version"]
            print(f"Reviewed version: {version['version_dir']}")
            print(f"Reviewed Markdown: {version['translated_markdown_path']}")
            rendered_files = result["rendered_files"]
            if "translated_epub" in rendered_files:
                print(f"Reviewed EPUB: {rendered_files['translated_epub']}")
            if "translated_pdf" in rendered_files:
                print(f"Reviewed PDF: {rendered_files['translated_pdf']}")
        elif args.command == "review-rewrite":
            result = rewrite_review_requests(
                run_dir=args.run_dir.expanduser().resolve(),
                translator=build_translator(args.translator),
                source_language=args.source_lang,
                target_language=args.target_lang,
                segment_id=args.segment_id,
            )
            print(f"Rewrite candidates generated: {result['rewritten_count']}")
            print(f"Review state updated: {result['review_state_path']}")
        elif args.command == "agent-once":
            try:
                result = run_agent_once(
                    source_root=args.source_root,
                    target_language=args.target_lang,
                    translator=args.translator,
                    output_format=args.format,
                    max_chunk_chars=args.max_chunk_chars,
                    translation_concurrency=args.translation_concurrency,
                    ingest_timeout_seconds=args.ingest_timeout_seconds,
                    max_file_size_mb=args.max_file_size_mb,
                    max_page_count=args.max_page_count,
                    polish_english=not args.no_polish,
                    polish_translator=args.polish_translator,
                    source_lanes=tuple(args.source_lanes or ("EN", "CN")),
                    max_ng_retries=args.max_ng_retries,
                )
            except AgentLockError as exc:
                print(str(exc))
                raise SystemExit(3) from exc
            if result.status == "no_work":
                print(result.message or "No work.")
            else:
                print(f"Agent status: {result.status}")
                print(f"Source: {result.source_path}")
                print(f"Destination: {result.destination_dir}")
            if result.status == "ng":
                raise SystemExit(1)
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
