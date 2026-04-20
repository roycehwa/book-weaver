from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_translator.config import RunSettings
from pdf_translator.guardrails import DEFAULT_INGEST_TIMEOUT_SECONDS, IngestGuardrailError, ingest_pdf_guarded
from pdf_translator.newspaper import write_newspaper_articles, write_newspaper_reading_markdown
from pdf_translator.newspaper_rebuild import write_rebuilt_outputs
from pdf_translator.pipeline import run_translation_pipeline
from pdf_translator.profile import build_document_profile
from pdf_translator.validation import run_newspaper_directory, run_validation_manifest, write_validation_report


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
    translate_parser.add_argument("source_pdf", type=Path, help="Absolute or relative path to source PDF.")
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
        default="openai",
        choices=["openai", "mock"],
        help="Translation backend.",
    )
    translate_parser.add_argument(
        "--max-chunk-chars",
        type=int,
        default=2800,
        help="Max chunk size for translation requests.",
    )
    translate_parser.add_argument(
        "--profile",
        default="auto",
        choices=["auto", "magazine", "book", "newspaper"],
        help="Profile used for ingest guardrails.",
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
    profile_parser.add_argument("source_pdf", type=Path, help="Absolute or relative path to source PDF.")
    profile_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs"),
        help="Base directory for profile artifacts.",
    )
    profile_parser.add_argument(
        "--profile",
        default="auto",
        choices=["auto", "magazine", "book", "newspaper"],
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

    articles_parser = subparsers.add_parser(
        "articles",
        help="Extract and rank article candidates from a newspaper PDF.",
    )
    articles_parser.add_argument("source_pdf", type=Path, help="Absolute or relative path to source PDF.")
    articles_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs"),
        help="Base directory for article artifacts.",
    )
    articles_parser.add_argument(
        "--ingest-timeout-seconds",
        type=int,
        default=DEFAULT_INGEST_TIMEOUT_SECONDS,
        help="Hard timeout for the ingest stage. Use 0 to disable.",
    )
    articles_parser.add_argument(
        "--max-file-size-mb",
        type=float,
        default=None,
        help="Override the hard input gate for file size in MB.",
    )
    articles_parser.add_argument(
        "--max-page-count",
        type=int,
        default=None,
        help="Override the hard input gate for page count.",
    )
    articles_parser.add_argument(
        "--reading-all",
        action="store_true",
        help="Include all ranked article candidates in articles.md instead of selected top-half only.",
    )
    articles_parser.add_argument(
        "--reading-max-articles",
        type=int,
        default=None,
        help="Optional max number of articles included in articles.md.",
    )

    reading_parser = subparsers.add_parser(
        "reading",
        help="Render a readable Markdown edition from an existing articles.json artifact.",
    )
    reading_parser.add_argument(
        "articles_json",
        type=Path,
        help="Path to an existing articles.json file.",
    )
    reading_parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Output path for the markdown reading edition. Defaults to <articles-json-dir>/articles.md.",
    )
    reading_parser.add_argument(
        "--include-all-articles",
        action="store_true",
        help="Include all ranked article candidates instead of selected top-half only.",
    )
    reading_parser.add_argument(
        "--max-articles",
        type=int,
        default=None,
        help="Optional max number of articles included in output.",
    )

    reading_rebuild_parser = subparsers.add_parser(
        "reading-rebuild",
        help="Rebuild article continuity from an existing articles.json artifact.",
    )
    reading_rebuild_parser.add_argument(
        "articles_json",
        type=Path,
        help="Path to an existing articles.json file.",
    )
    reading_rebuild_parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Output path for rebuilt markdown. Defaults to <articles-json-dir>/articles.rebuilt.md.",
    )
    reading_rebuild_parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional output path for rebuilt JSON. Defaults to <articles-json-dir>/articles.rebuilt.json.",
    )
    reading_rebuild_parser.add_argument(
        "--include-all-articles",
        action="store_true",
        help="Include all ranked article candidates instead of selected top-half only.",
    )
    reading_rebuild_parser.add_argument(
        "--max-articles",
        type=int,
        default=None,
        help="Optional max number of rebuilt articles included in output.",
    )

    validate_parser = subparsers.add_parser(
        "validate",
        help="Run a batch validation manifest and summarize pass rates.",
    )
    validate_parser.add_argument(
        "manifest",
        type=Path,
        help="Path to a JSON manifest containing profile/articles validation cases.",
    )
    validate_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs"),
        help="Base directory for profile/article artifacts and validation reports.",
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

    newspaper_batch_parser = subparsers.add_parser(
        "newspaper-batch",
        help="Discover all newspaper PDFs in a directory and run article extraction on each.",
    )
    newspaper_batch_parser.add_argument(
        "source_dir",
        type=Path,
        help="Directory containing newspaper PDFs.",
    )
    newspaper_batch_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs"),
        help="Base directory for article artifacts and validation reports.",
    )
    newspaper_batch_parser.add_argument(
        "--report-name",
        default="newspaper-batch-report.json",
        help="Report filename written under runs/validation/.",
    )
    newspaper_batch_parser.add_argument(
        "--no-reuse-existing",
        action="store_true",
        help="Recompute article artifacts instead of reusing existing articles.json files.",
    )
    newspaper_batch_parser.add_argument(
        "--ingest-timeout-seconds",
        type=int,
        default=DEFAULT_INGEST_TIMEOUT_SECONDS,
        help="Hard timeout for ingest when a file is recomputed. Use 0 to disable.",
    )
    newspaper_batch_parser.add_argument(
        "--max-file-size-mb",
        type=float,
        default=None,
        help="Hard input gate for file size in MB.",
    )
    newspaper_batch_parser.add_argument(
        "--max-page-count",
        type=int,
        default=None,
        help="Hard input gate for page count.",
    )
    newspaper_batch_parser.add_argument(
        "--selected-pass-min-pct",
        type=float,
        default=0.85,
        help="Minimum selected-pass percentage required for a case to pass.",
    )
    newspaper_batch_parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively discover PDFs under the source directory.",
    )
    return parser


def _print_preflight(preflight: dict[str, object]) -> None:
    print(
        "Preflight: "
        f"pages={preflight['page_count']} "
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
                profile_name=args.profile,
                ingest_timeout_seconds=args.ingest_timeout_seconds,
                max_file_size_mb=args.max_file_size_mb,
                max_page_count=args.max_page_count,
            )
            artifacts = run_translation_pipeline(settings)
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
            _print_preflight(manifest["preflight"])
            print(f"Artifacts written to: {artifacts.output_dir}")
            print(f"Translated PDF: {artifacts.translated_pdf_path}")
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
        elif args.command == "articles":
            source_pdf = args.source_pdf.expanduser().resolve()
            normalized, preflight = ingest_pdf_guarded(
                source_pdf,
                profile_name="newspaper",
                timeout_seconds=args.ingest_timeout_seconds,
                max_file_size_mb=args.max_file_size_mb,
                max_page_count=args.max_page_count,
            )
            output_dir = args.output_dir.expanduser().resolve() / source_pdf.stem
            output_dir.mkdir(parents=True, exist_ok=True)
            articles_path = output_dir / "articles.json"
            result = write_newspaper_articles(normalized.structured, source_pdf, articles_path)
            result["preflight"] = preflight.as_dict()
            articles_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            reading_path = output_dir / "articles.md"
            write_newspaper_reading_markdown(
                result,
                reading_path,
                selected_only=not args.reading_all,
                max_articles=args.reading_max_articles,
            )
            _print_preflight(result["preflight"])
            print(f"Articles written to: {articles_path}")
            print(f"Reading edition written to: {reading_path}")
            print(
                "Articles: "
                f"total={result['article_count']} "
                f"selected_top_half={result['selected_top_half_count']}"
            )
            print(
                "Quality: "
                f"high={result['quality_summary']['high']} "
                f"medium={result['quality_summary']['medium']} "
                f"low={result['quality_summary']['low']}"
            )
        elif args.command == "reading":
            articles_json = args.articles_json.expanduser().resolve()
            result = json.loads(articles_json.read_text(encoding="utf-8"))
            output_path = (
                args.output_path.expanduser().resolve()
                if args.output_path is not None
                else articles_json.with_name("articles.md")
            )
            write_newspaper_reading_markdown(
                result,
                output_path,
                selected_only=not args.include_all_articles,
                max_articles=args.max_articles,
            )
            selected_indexes = result.get("selected_article_indexes")
            total_articles = result.get("articles")
            total_count = len(total_articles) if isinstance(total_articles, list) else 0
            included = total_count
            if not args.include_all_articles:
                if isinstance(selected_indexes, list):
                    included = len([index for index in selected_indexes if isinstance(index, int) and 0 <= index < total_count])
                else:
                    included = min(total_count, int(result.get("selected_top_half_count", 0) or 0))
            if args.max_articles is not None and args.max_articles > 0:
                included = min(included, args.max_articles)
            print(f"Reading edition written to: {output_path}")
            print(f"Included articles: {included}/{total_count}")
        elif args.command == "reading-rebuild":
            articles_json = args.articles_json.expanduser().resolve()
            result = json.loads(articles_json.read_text(encoding="utf-8"))
            output_path = (
                args.output_path.expanduser().resolve()
                if args.output_path is not None
                else articles_json.with_name("articles.rebuilt.md")
            )
            output_json_path = (
                args.output_json.expanduser().resolve()
                if args.output_json is not None
                else articles_json.with_name("articles.rebuilt.json")
            )
            rebuilt = write_rebuilt_outputs(
                result,
                output_markdown_path=output_path,
                output_json_path=output_json_path,
                selected_only=not args.include_all_articles,
                max_articles=args.max_articles,
            )
            summary = rebuilt["summary"]
            print(f"Rebuilt reading edition written to: {rebuilt['markdown_path']}")
            print(f"Rebuilt JSON written to: {rebuilt['json_path']}")
            print(
                "Rebuild summary: "
                f"included={summary['included_articles']}/{summary['total_articles']} "
                f"dropped_paragraphs={summary['dropped_paragraphs']}"
            )
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
        elif args.command == "newspaper-batch":
            source_dir = args.source_dir.expanduser().resolve()
            output_dir = args.output_dir.expanduser().resolve()
            report = run_newspaper_directory(
                source_dir=source_dir,
                output_dir=output_dir,
                reuse_existing=not args.no_reuse_existing,
                ingest_timeout_seconds=args.ingest_timeout_seconds,
                max_file_size_mb=args.max_file_size_mb,
                max_page_count=args.max_page_count,
                selected_pass_min_pct=args.selected_pass_min_pct,
                recursive=args.recursive,
            )
            report_path = output_dir / "validation" / args.report_name
            write_validation_report(report, report_path)
            print(f"Newspaper batch report written to: {report_path}")
            print(
                "Summary: "
                f"passed={report['passed_cases']}/{report['total_cases']} "
                f"pass_rate={report['pass_rate']:.1%}"
            )
            if report["skipped_files"]:
                print(f"Skipped files: {len(report['skipped_files'])}")
            if report["failure_types"]:
                print(f"Failure types: {report['failure_types']}")
            for case in report["cases"]:
                status = "PASS" if case["passed"] else "FAIL"
                if "failure" in case:
                    print(f"{status} {case['profile']} {case['name']} :: {case['failure']['type']}")
                else:
                    metrics = case["metrics"]
                    print(
                        f"{status} {case['profile']} {case['name']} "
                        f":: selected_pass_pct={metrics['selected_pass_pct']:.3f}"
                    )
    except IngestGuardrailError as exc:
        print(str(exc))
        if exc.preflight is not None:
            _print_preflight(exc.preflight.as_dict())
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
