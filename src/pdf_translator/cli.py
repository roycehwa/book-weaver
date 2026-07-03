from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pdf_translator.config import DEFAULT_TRANSLATION_CONCURRENCY, RunSettings
from pdf_translator.guardrails import (
    DEFAULT_INGEST_TIMEOUT_SECONDS,
    IngestGuardrailError,
    ingest_pdf_guarded,
)
from pdf_translator.jobs import BookJobRunner, JobRepository
from pdf_translator.job_control import format_event_line, format_progress_report, load_progress, load_translation_events
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
    merge_reviewed_chapters_with_resources,
    review_project_from_run,
    restore_review_chapter_apparatus,
    rewrite_review_requests,
    summarize_review_state,
    translated_segments_to_chapters,
    write_versioned_outputs,
)
from pdf_translator.review_migration import migrate_legacy_review_run
from pdf_translator.epub import render_epub_from_book, validate_epub_internal_hrefs
from pdf_translator.glossary import (
    apply_glossary_decision,
    detect_glossary_profile_for_run,
    extract_glossary_candidates,
    glossary_status,
)
from pdf_translator.glossary_suggestions import suggest_glossary_targets
from pdf_translator.workflow import (
    clear_glossary_suggestions,
    glossary_ready_summary,
    mark_glossary_ready,
    reset_glossary_review,
)
from pdf_translator.translate import build_translator
from pdf_translator.validation import run_validation_manifest, write_validation_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="book-weaver",
        description="Ingest books, translate when needed, and prepare reading and knowledge artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    job_parser = subparsers.add_parser(
        "job",
        help="Run and inspect durable resumable book-processing jobs.",
    )
    job_subparsers = job_parser.add_subparsers(dest="job_command", required=True)
    job_run_parser = job_subparsers.add_parser("run", help="Create and run a book job.")
    job_run_parser.add_argument("source", type=Path, help="Source PDF or EPUB.")
    job_run_parser.add_argument(
        "--mode",
        dest="processing_mode",
        default="auto",
        choices=["auto", "translate", "preserve"],
        help="Choose automatic language-based behavior, force translation, or preserve source text.",
    )
    job_run_parser.add_argument("--source-lang", default=None)
    job_run_parser.add_argument("--target-lang", default="zh-CN")
    job_run_parser.add_argument(
        "--translator",
        default="minimax",
        choices=["openai", "mock", "minimax", "compatible", "openai-compatible"],
    )
    job_run_parser.add_argument(
        "--format",
        default="epub",
        choices=["pdf", "epub", "both"],
    )
    job_run_parser.add_argument(
        "--ingest-timeout-seconds",
        type=int,
        default=None,
        help="Hard timeout for the ingest stage. Use 0 to disable. Defaults to pipeline setting.",
    )
    job_run_parser.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    job_run_parser.add_argument("--json", dest="as_json", action="store_true")

    job_create_parser = job_subparsers.add_parser(
        "create",
        help="Create a durable job without executing it.",
    )
    job_create_parser.add_argument("source", type=Path, help="Source PDF or EPUB.")
    job_create_parser.add_argument(
        "--mode",
        dest="processing_mode",
        default="auto",
        choices=["auto", "translate", "preserve"],
    )
    job_create_parser.add_argument("--source-lang", default=None)
    job_create_parser.add_argument("--target-lang", default="zh-CN")
    job_create_parser.add_argument(
        "--translator",
        default="minimax",
        choices=["openai", "mock", "minimax", "compatible", "openai-compatible"],
    )
    job_create_parser.add_argument(
        "--format",
        default="epub",
        choices=["pdf", "epub", "both"],
    )
    job_create_parser.add_argument(
        "--ingest-timeout-seconds",
        type=int,
        default=None,
        help="Hard timeout for the ingest stage. Use 0 to disable. Defaults to pipeline setting.",
    )
    job_create_parser.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    job_create_parser.add_argument("--json", dest="as_json", action="store_true")

    job_execute_parser = job_subparsers.add_parser(
        "execute",
        help="Execute a previously created job.",
    )
    job_execute_parser.add_argument("job_id")
    job_execute_parser.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    job_execute_parser.add_argument("--json", dest="as_json", action="store_true")

    job_status_parser = job_subparsers.add_parser("status", help="Read a durable job snapshot.")
    job_status_parser.add_argument("job_id")
    job_status_parser.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    job_status_parser.add_argument("--json", dest="as_json", action="store_true")

    job_resume_parser = job_subparsers.add_parser("resume", help="Resume a failed job.")
    job_resume_parser.add_argument("job_id")
    job_resume_parser.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    job_resume_parser.add_argument("--json", dest="as_json", action="store_true")
    job_translate_parser = job_subparsers.add_parser(
        "translate",
        help="Run translation phase after glossary is marked ready.",
    )
    job_translate_parser.add_argument("job_id")
    job_translate_parser.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    job_translate_parser.add_argument("--json", dest="as_json", action="store_true")

    job_progress_parser = job_subparsers.add_parser(
        "progress",
        help="Print chunk-level translation progress for a run directory.",
    )
    job_progress_parser.add_argument(
        "run_dir",
        type=Path,
        help="Run directory containing jobs/progress.json.",
    )
    job_progress_parser.add_argument("--json", dest="as_json", action="store_true")

    job_events_parser = job_subparsers.add_parser(
        "events",
        help="Show recent chunk-level translation events for a run directory.",
    )
    job_events_parser.add_argument(
        "run_dir",
        type=Path,
        help="Run directory containing jobs/translation-events.jsonl.",
    )
    job_events_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of recent events to show.",
    )
    job_events_parser.add_argument("--json", dest="as_json", action="store_true")

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
        nargs="?",
        default=None,
        help="Path to source document. Omit when using --run-dir to translate an intake run.",
    )
    translate_parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Translate an existing intake run directory after glossary is marked ready.",
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
        "--resume",
        action="store_true",
        help="Resume translation using valid cached chunks and write explicit job progress.",
    )
    translate_parser.add_argument(
        "--ignore-cache",
        action="store_true",
        help="Delete existing translation cache for this run before translating.",
    )
    translate_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress live translation progress output.",
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
        choices=["pdf", "epub", "both", "none"],
        help="Rendered output format. EPUB is the default reading output. Use none for translation-only runs.",
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

    glossary_parser = subparsers.add_parser(
        "glossary",
        help="Extract, apply, and inspect book glossary artifacts.",
    )
    glossary_sub = glossary_parser.add_subparsers(dest="glossary_command", required=True)
    glossary_extract = glossary_sub.add_parser(
        "extract",
        help="Extract glossary candidates from book.json into glossary artifacts.",
    )
    glossary_extract.add_argument("run_dir", type=Path)
    glossary_extract.add_argument(
        "--profile",
        choices=[
            "humanities_history",
            "social_econ_philosophy",
            "science_tech_engineering",
            "formal_logic_philosophy",
        ],
        default=None,
        help="Glossary extraction profile (default: auto-detect or keep user override).",
    )
    glossary_extract.add_argument(
        "--profile-source",
        choices=["auto", "cli", "user"],
        default=None,
        help="How the profile was chosen (user = manual override in workbench).",
    )
    glossary_detect = glossary_sub.add_parser(
        "detect",
        help="Detect recommended glossary profile without writing artifacts.",
    )
    glossary_detect.add_argument("run_dir", type=Path)
    glossary_suggest = glossary_sub.add_parser(
        "suggest",
        help="Generate Chinese translation suggestions for glossary candidates.",
    )
    glossary_suggest.add_argument("run_dir", type=Path)
    glossary_suggest.add_argument("--target-lang", default="zh-CN")
    glossary_suggest.add_argument(
        "--translator",
        default="minimax",
        choices=["openai", "mock", "minimax", "compatible", "openai-compatible"],
    )
    glossary_apply = glossary_sub.add_parser(
        "apply",
        help="Apply a glossary decision and update active.json.",
    )
    glossary_apply.add_argument("run_dir", type=Path)
    glossary_apply.add_argument("--source", required=True)
    glossary_apply.add_argument("--target", default=None)
    glossary_apply.add_argument("--type", dest="term_type", default="name_or_key_term")
    glossary_apply.add_argument("--status", default="active", choices=["active", "rejected", "candidate"])
    glossary_apply.add_argument("--decided-by", default="user")
    glossary_status_parser = glossary_sub.add_parser(
        "status",
        help="Print glossary candidate and active counts.",
    )
    glossary_status_parser.add_argument("run_dir", type=Path)
    glossary_ready_parser = glossary_sub.add_parser(
        "ready",
        help="Mark glossary finalized so translation may start.",
    )
    glossary_ready_parser.add_argument("run_dir", type=Path)
    glossary_ready_parser.add_argument("--decided-by", default="user")
    glossary_reset_parser = glossary_sub.add_parser(
        "reset-review",
        help="Clear adopted glossary decisions and return to awaiting_glossary.",
    )
    glossary_reset_parser.add_argument("run_dir", type=Path)
    glossary_reset_parser.add_argument(
        "--keep-suggestions",
        action="store_true",
        help="Keep machine-generated target_suggestion fields on candidates.",
    )
    glossary_reset_parser.add_argument(
        "--keep-policy-annotations",
        action="store_true",
        help="Keep sensitive/strategy tags written during prior test rounds.",
    )
    glossary_reset_parser.add_argument("--decided-by", default="system")
    glossary_clear_suggestions_parser = glossary_sub.add_parser(
        "clear-suggestions",
        help="Clear machine-generated Chinese suggestions; keep adopted terms.",
    )
    glossary_clear_suggestions_parser.add_argument("run_dir", type=Path)

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


def _review_image_roots(run_dir: Path, manifest: dict[str, object]) -> list[Path]:
    candidates: list[Path] = []
    files = manifest.get("files")
    if isinstance(files, dict):
        configured = files.get("images_dir")
        if isinstance(configured, str) and configured.strip():
            candidates.append(Path(configured).expanduser())
    candidates.extend([run_dir / "book-images", run_dir / "images"])

    roots: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)
    return roots


def _uncovered_book_pages(book: dict[str, object]) -> list[int]:
    raw_pages = book.get("pages")
    raw_chapters = book.get("chapters")
    if not isinstance(raw_pages, list) or not isinstance(raw_chapters, list):
        return []
    covered = {
        int(page_no)
        for chapter in raw_chapters
        if isinstance(chapter, dict)
        for page_no in chapter.get("source_pages", [])
        if isinstance(page_no, int)
    }
    content_pages = {
        int(page["page_no"])
        for page in raw_pages
        if isinstance(page, dict)
        and isinstance(page.get("page_no"), int)
        and (
            page.get("has_content") is True
            or (
                "has_content" not in page
                and (
                    page.get("page_kind") != "visual_only"
                    or int(page.get("figure_count") or 0) > 0
                    or int(page.get("table_count") or 0) > 0
                )
            )
        )
    }
    return sorted(content_pages - covered)


def _load_complete_review_book(
    run_dir: Path,
    manifest: dict[str, object],
) -> dict[str, object]:
    book: dict[str, object] = {
        "metadata": {"schema": "review_export_fallback"},
        "chapters": [],
    }
    book_path = run_dir / "book.json"
    if book_path.exists():
        book = json.loads(book_path.read_text(encoding="utf-8"))
    semantic_content = book.get("semantic_content")
    needs_contract_migration = (
        not isinstance(semantic_content, dict)
        or semantic_content.get("schema") != "semantic_content_v1"
        or not (run_dir / "integrity-ledger.json").exists()
    )
    normalized_value = (manifest.get("files") or {}).get("normalized_json")
    normalized_path = (
        Path(str(normalized_value)).expanduser().resolve()
        if normalized_value
        else run_dir / "normalized.json"
    )
    can_rebuild = normalized_path.exists() and (run_dir / "review_state.json").exists()
    if _uncovered_book_pages(book) or (needs_contract_migration and can_rebuild):
        migrate_legacy_review_run(run_dir)
        book = json.loads(book_path.read_text(encoding="utf-8"))
    uncovered_pages = _uncovered_book_pages(book)
    if uncovered_pages:
        preview = ", ".join(str(page) for page in uncovered_pages[:12])
        raise ValueError(
            "Review export blocked: source pages are missing from the book model "
            f"({len(uncovered_pages)} pages; first: {preview})."
        )
    return book


def _validate_approved_review_project(
    project: dict[str, object],
    *,
    integrity_ledger: dict[str, object] | None = None,
) -> None:
    translated_segments = apply_review_state(
        project.get("translated_segments", []),
        project.get("review_state", {}),
    )
    missing = [
        str(segment.get("segment_id") or "")
        for segment in translated_segments
        if bool(segment.get("translate", True))
        and not str(segment.get("translated_text") or "").strip()
    ]
    if missing:
        preview = ", ".join(missing[:8])
        raise ValueError(
            "Approved review export blocked: missing translated content "
            f"for {len(missing)} segments (first: {preview})."
        )
    summary = summarize_review_state(
        project.get("review_items", []),
        project.get("review_state", {}),
    )
    open_items = int(summary.get("open_items") or 0)
    if open_items:
        raise ValueError(
            "Approved review export blocked: unresolved review items remain "
            f"({open_items})."
        )
    if integrity_ledger is not None:
        from pdf_translator.integrity import assert_approved_export_ready

        assert_approved_export_ready(integrity_ledger)


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
    if approve:
        integrity_path = run_dir / "integrity-ledger.json"
        integrity_ledger = (
            json.loads(integrity_path.read_text(encoding="utf-8"))
            if integrity_path.exists()
            else None
        )
        if integrity_ledger is not None:
            from pdf_translator.integrity import refresh_review_readiness

            integrity_ledger = refresh_review_readiness(
                integrity_ledger,
                review_items=project.get("review_items", []),
                review_state=project.get("review_state", {}),
            )
        _validate_approved_review_project(
            project,
            integrity_ledger=integrity_ledger,
        )
    applied_segments = apply_review_state(project["translated_segments"], project["review_state"])
    manifest = {}
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_name = Path(str(manifest.get("source_pdf") or run_dir.name)).stem
    reviewed_chapters = translated_segments_to_chapters(applied_segments)
    translated_chapters_path = (manifest.get("files") or {}).get("translated_chapters")
    if isinstance(translated_chapters_path, str) and translated_chapters_path.strip():
        translated_chapters_file = Path(translated_chapters_path).expanduser().resolve()
        if translated_chapters_file.exists():
            base_translated = json.loads(translated_chapters_file.read_text(encoding="utf-8"))
            reviewed_chapters = restore_review_chapter_apparatus(reviewed_chapters, base_translated)
    book = _load_complete_review_book(run_dir, manifest)
    if not book.get("chapters"):
        book = {
            "metadata": {"schema": "review_export_fallback"},
            "chapters": reviewed_chapters,
        }
    delivery_chapters = merge_reviewed_chapters_with_resources(
        reviewed_chapters,
        book,
    )
    delivery_markdown_parts: list[str] = []
    for chapter in delivery_chapters:
        body = str(chapter.get("markdown") or "").strip()
        title = str(chapter.get("title") or "").strip()
        if title and body and not body.startswith("#"):
            body = f"# {title}\n\n{body}"
        if body:
            delivery_markdown_parts.append(body)
    delivery_markdown = "\n\n".join(delivery_markdown_parts).strip() + "\n"
    image_roots = _review_image_roots(run_dir, manifest)

    version = write_versioned_outputs(
        run_dir=run_dir,
        version_name=version_name,
        target_language=target_language,
        translated_segments=applied_segments,
        translated_markdown_override=delivery_markdown,
        parent_version=parent_version,
        approval_status="approved" if approve else "draft",
    )
    version_dir = Path(version["version_dir"])
    translated_markdown_path = Path(version["translated_markdown_path"])
    translated_markdown = translated_markdown_path.read_text(encoding="utf-8")
    rendered_files: dict[str, object] = {}

    delivery_stem = safe_delivery_file_stem(Path(source_name), target_language)
    if output_format in {"pdf", "both"}:
        pdf_path = version_dir / f"{delivery_stem}.pdf"
        render_pdf_from_markdown(
            title=f"{source_name} ({target_language})",
            markdown_text=translated_markdown,
            output_path=pdf_path,
            images_dir=image_roots[0] if image_roots else None,
        )
        rendered_files["translated_pdf"] = str(pdf_path)
    if output_format in {"epub", "both"}:
        epub_path = version_dir / f"{delivery_stem}.epub"
        render_epub_from_book(
            book=book,
            translated_chapters=delivery_chapters,
            output_path=epub_path,
            title=f"{source_name} ({target_language})",
            language=target_language,
            image_roots=image_roots,
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


def run_job_command(args: argparse.Namespace) -> dict[str, object]:
    if args.job_command == "progress":
        progress_path = args.run_dir.expanduser().resolve() / "jobs" / "progress.json"
        if not progress_path.exists():
            raise SystemExit(f"No translation progress found at: {progress_path}")
        run_dir = args.run_dir.expanduser().resolve()
        if args.as_json:
            print(json.dumps(load_progress(run_dir), ensure_ascii=False, indent=2))
        else:
            print(format_progress_report(run_dir))
        return load_progress(run_dir)

    if args.job_command == "events":
        events_path = args.run_dir.expanduser().resolve() / "jobs" / "translation-events.jsonl"
        if not events_path.exists():
            raise SystemExit(f"No translation events found at: {events_path}")
        run_dir = args.run_dir.expanduser().resolve()
        events = load_translation_events(run_dir, limit=args.limit)
        if args.as_json:
            print(json.dumps(events, ensure_ascii=False, indent=2))
        else:
            for event in events:
                print(format_event_line(event))
        return {"events": events}

    repository = JobRepository(args.jobs_dir)
    if args.job_command in {"run", "create"}:
        snapshot = repository.create(
            source_path=args.source,
            processing_mode=args.processing_mode,
            source_language=args.source_lang,
            target_language=args.target_lang,
            translator=args.translator,
            output_format=args.format,
            ingest_timeout_seconds=args.ingest_timeout_seconds,
        )
        print(f"Job ID: {snapshot['job_id']}", flush=True)
        if args.job_command == "run":
            snapshot = BookJobRunner(repository).run(snapshot["job_id"])
    elif args.job_command == "execute":
        snapshot = BookJobRunner(repository).run(args.job_id)
    elif args.job_command == "status":
        snapshot = repository.load(args.job_id)
    elif args.job_command == "resume":
        snapshot = BookJobRunner(repository).resume(args.job_id)
    elif args.job_command == "translate":
        snapshot = BookJobRunner(repository).run_translate_phase(args.job_id)
    else:
        raise ValueError(f"Unsupported job command: {args.job_command!r}.")

    if args.as_json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(f"Job state: {snapshot['state']}")
        print(f"Job revision: {snapshot['revision']}")
    return snapshot


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "job":
            run_job_command(args)
        elif args.command == "intake":
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
            workflow_path = artifacts.output_dir / "workflow.json"
            if workflow_path.exists():
                workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
                print(f"Workflow stage: {workflow.get('stage')}")
            if "glossary_candidates" in files:
                print(f"Glossary candidates: {files['glossary_candidates']}")
        elif args.command == "translate":
            if args.run_dir is None and args.source_pdf is None:
                raise ValueError("Provide SOURCE_PDF or --run-dir.")
            existing_run_dir = args.run_dir.expanduser().resolve() if args.run_dir else None
            source_pdf = args.source_pdf.expanduser().resolve() if args.source_pdf else None
            if existing_run_dir is not None:
                manifest = json.loads((existing_run_dir / "manifest.json").read_text(encoding="utf-8"))
                source_pdf = Path(str(manifest["source_pdf"])).expanduser().resolve()
            if source_pdf is None:
                raise ValueError("Could not resolve source document path.")
            settings = RunSettings(
                source_pdf=source_pdf,
                output_dir=(
                    existing_run_dir.parent
                    if existing_run_dir is not None
                    else args.output_dir.expanduser().resolve()
                ),
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
                resume_translation=args.resume,
                ignore_translation_cache=args.ignore_cache,
                show_translation_progress=not args.quiet,
                existing_run_dir=existing_run_dir,
                require_glossary_ready=existing_run_dir is not None,
            )
            artifacts = run_translation_pipeline(settings)
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
            _print_preflight(manifest["preflight"])
            print(f"Artifacts written to: {artifacts.output_dir}")
            files = manifest.get("files", {})
            if "translation_progress" in files:
                progress_path = Path(files["translation_progress"])
                if progress_path.exists():
                    print(format_progress_report(progress_path.parent.parent))
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
        elif args.command == "glossary":
            run_dir = args.run_dir.expanduser().resolve()
            if args.glossary_command == "extract":
                result = extract_glossary_candidates(
                    run_dir,
                    profile=args.profile,
                    profile_source=args.profile_source or ("user" if args.profile else None),
                )
                status = glossary_status(run_dir)
                policy = result.get("policy", {})
                print(f"Glossary profile: {policy.get('glossary_profile_label')} ({policy.get('glossary_profile')})")
                print(f"Glossary candidates: {len(result['candidates'])}")
                print(f"Glossary active entries: {status['active_count']}")
            elif args.glossary_command == "detect":
                detection = detect_glossary_profile_for_run(run_dir)
                print(f"Recommended profile: {detection['glossary_profile_label']} ({detection['glossary_profile']})")
                print(f"Confidence: {detection['glossary_profile_confidence']}")
                for name, score in sorted(
                    detection.get("glossary_profile_scores", {}).items(),
                    key=lambda item: item[1],
                    reverse=True,
                ):
                    print(f"  {name}: {score}")
                if detection.get("humanities_subhints"):
                    print(f"Sub-hints: {', '.join(detection['humanities_subhints'])}")
            elif args.glossary_command == "suggest":
                result = suggest_glossary_targets(
                    run_dir,
                    target_lang=args.target_lang,
                    translator=args.translator,
                )
                report = result["report"]
                print(f"Glossary suggestions: {report['suggested_count']}/{report['candidate_count']}")
                print(f"Translator: {report['translator']} ({report.get('model')})")
            elif args.glossary_command == "apply":
                entry = apply_glossary_decision(
                    run_dir,
                    source=args.source,
                    target=args.target,
                    term_type=args.term_type,
                    status=args.status,
                    decided_by=args.decided_by,
                )
                print(f"Glossary decision applied: {entry['source']} -> {entry.get('target')}")
            elif args.glossary_command == "status":
                status = glossary_status(run_dir)
                summary = glossary_ready_summary(run_dir)
                print(f"Glossary candidates: {status['candidate_count']}")
                print(f"Glossary active: {status['active_count']}")
                print(f"Glossary entries: {status['entry_count']}")
                if status.get("glossary_profile_label"):
                    print(
                        f"Glossary profile: {status['glossary_profile_label']} "
                        f"({status.get('glossary_profile')}, confidence={status.get('glossary_profile_confidence')})"
                    )
                print(f"Workflow stage: {summary['workflow_stage']}")
                print(f"Glossary ready: {summary['is_ready']}")
            elif args.glossary_command == "ready":
                workflow = mark_glossary_ready(run_dir, decided_by=args.decided_by)
                summary = glossary_ready_summary(run_dir)
                print(f"Glossary ready: {summary['ready_entries']} active terms")
                print(f"Workflow stage: {workflow['stage']}")
            elif args.glossary_command == "reset-review":
                result = reset_glossary_review(
                    run_dir,
                    clear_suggestions=not args.keep_suggestions,
                    clear_policy_annotations=not getattr(args, "keep_policy_annotations", False),
                    decided_by=args.decided_by,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.glossary_command == "clear-suggestions":
                result = clear_glossary_suggestions(run_dir)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                raise ValueError(f"Unsupported glossary command: {args.glossary_command!r}.")
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
