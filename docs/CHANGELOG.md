# Changelog

All notable changes to BookWeaver (Phase A) are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — 2026-07-07 (revised: P2 hard caps rolled back)

### Added
- **Translation supervisor (P0)**: independent CLI process
  `scripts/translation_supervisor.py` that polls translating jobs and
  auto-resumes them when `translation_activity.status` reports stalled
  or progress is older than `--stuck-threshold` (default 90s).
  Includes rate limiting (default 6 resumes/hour/job), JSON metrics
  output, and a graceful SIGTERM handler.
- **Embedded supervisor safety net (P0)**: the in-lifespan supervisor
  in `backend/translation_supervisor.py` is now enabled by default
  (set `BOOKMATE_DISABLE_EMBEDDED_SUPERVISOR=1` to disable) and runs
  on a 30s tick.
- **SSE job event stream (P0)**: new endpoint
  `GET /api/jobs/{id}/events/stream` that streams new entries from
  `events.jsonl` to the client. The connection closes automatically
  when the job leaves `translating` / `failed`.
- **BookIR chapter kinds (P1)**: new `chapter_kind` module with 8
  chapter kinds (`cover`, `toc`, `front_matter`, `narrative`,
  `apparatus`, `bibliography`, `index`, `appendix`) and 7 block kinds
  (`text`, `table`, `figure`, `caption`, `note`, `heading`, `list`).
  Every chapter in `book.json` is annotated with `kind` and the
  translator skips non-narrative kinds.
- **Chapter segment plan (P1)**: new `chapter-segments.json` artifact
  generated after user-confirmed canonical chapters are applied. It
  records stable chapter-internal semantic segments with `segment_id`,
  `chapter_id`, `section_title`, `translate`, and
  `knowledge_eligible`; translation progress and review alignment now
  consume this same plan instead of independently chunking by length.
- **Translation input filter (P1)**: `book_views.render_translation_input_markdown`
  rewrites the build to skip non-translatable chapters and any
  `table` / `figure` blocks inside translatable chapters, removing the
  need to rely on prompt-level instructions.
- **Glossary stop-phrase filtering (P2)**: expanded the curated
  `GENERIC_STOP_PHRASES` list and policy counters without adding a
  fixed book-wide candidate cap.
- **Extended metadata exclusions (P2)**: `glossary_extraction._metadata_exclusions`
  now excludes every chapter title (not just the first 3) and splits
  on subtitle separators to catch book-name bleed-through.
- **Review exemptions (P2)**: new `review_exemptions` module with 5
  rule classes — apparatus chapters, blockquote / code segments,
  foreign-script passages, glossary terms, and citation-year-only
  segments. Integrated into `review.detect_review_items` so legitimate
  English in those contexts no longer raises `mixed_english`.
- **Phase A landing route (P3)**: the root route now redirects to the
  Phase A upload/workspace screen. `Layout.tsx` navigation is trimmed
  to Phase A scope.

### Changed
- `book_rebuild.build_book_reconstruction` annotates `kind` on every
  chapter and forces `translate=False` for non-translatable kinds.
- Frontend layout: header brand updated from BookMate to
  BookWeaver · Phase A; the obsolete "旧书库" entry removed.

### Fixed
- Translation worker no longer dies silently with the uvicorn
  process; supervisor auto-resumes within ~90s.
- `GET /api/jobs/{id}/translation-events` now reads the translator's
  actual artifact run directory (`artifacts/*/jobs/translation-events.jsonl`)
  and returns the consumed byte offset when `limit` truncates a poll,
  preventing skipped chunk events.
- `/` no longer renders an empty shell after the old Home route was
  removed; it redirects to `/upload`.
- Apparatus chapters (e.g. "Notes on Transcription and Dates") are
  no longer sent to the translator.
- Block-level tables and figures inside narrative chapters are
  preserved as images, not sent as Markdown to the translator.
- Review no longer raises `mixed_english` for footnote proper nouns
  / book titles / Arabic transliterations / code blocks.

### Rolled back (P2 glossary hard cap)
- The hard ``MIN_OCCURRENCES=8`` / ``MIN_CHAPTERS=4`` /
  ``MAX_CANDIDATES=60`` thresholds were reverted. A single fixed
  surface cap is unrelated to book length, domain density, or the
  underlying distribution of proper nouns in the source text; using
  the same 60 for a 100-page children's book and a 600-page
  monograph is unjustified. The genuine fix is to rewrite the
  extraction algorithm (TF-IDF / mutual information / elbow
  detection) — that work is intentionally deferred to a separate
  plan.
- ``extract_glossary_candidates`` no longer accepts the
  ``min_occurrences`` / ``min_chapters`` / ``min_score`` overrides.
- The generic stop-phrase list and the extended metadata exclusion
  (every chapter title, not just the first three) remain in place.

### Tests
- New tests for the supervisor + SSE + translation-event polling path.
- 43 new tests for chapter/block classification + filtered
  translation input.
- New tests for glossary stop phrases + review exemptions.
- New frontend route test ensuring `/` reaches the Phase A workspace.
- Current verification: Python/backend/core test matrix `515 passed`;
  frontend Vitest `23 passed`; frontend lint and production build pass.
