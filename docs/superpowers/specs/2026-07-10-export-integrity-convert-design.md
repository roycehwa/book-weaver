# Export Integrity & PDF→EPUB Convert — Design Spec

**Date:** 2026-07-10  
**Status:** Approved (user chose chapter-confirm-before-export, option A)

## Goals

1. **Text conservation:** No loss of prose segments; reading order unchanged. Chapter/layout structure may change.
2. **Export fidelity:** EPUB reflects user-confirmed canonical chapters (page breaks at chapter boundaries); no duplicate images.
3. **Convert mode:** Upload PDF/EPUB → parse → confirm chapters → export EPUB without translation.

## Non-Goals

- Pixel-perfect PDF layout reproduction
- Automatic chapter detection without user confirmation (convert path)

## Architecture

### Text conservation

- Intake writes `segment-order.json` from `chapter-segments.json`.
- Translation records processed `segment_id` list; post-check compares to plan.
- Failures surface in `integrity-ledger.json` → `failures.segment_order`.

### Export formatting

- `normalize_chapter_headings()`: one `#` per chapter; demote body `#` blocks.
- EPUB: `body.bookweaver-chapter-start` forces page break; first chapter exempt.
- Images: SHA256 dedup at render; markdown dedup drops repeated image blocks.

### Convert mode (`processing_mode=convert`)

1. Intake only → `awaiting_glossary` job state + `workflow.stage=awaiting_chapter_confirmation`
2. User confirms `canonical-chapters.json` (`source_artifact=user_confirmation`)
3. `POST /jobs/{id}/export` → `run_export_pipeline` → EPUB → `completed`

EPUB input uses existing `ingest_epub()` spine order.

## Acceptance

- EPUB spine chapter count == canonical chapter count
- Each chapter xhtml has exactly one `h1`; chapter 2+ starts on new page
- Duplicate image bytes appear once in EPUB manifest
- `segment_order` failures empty after successful translation
- Convert job produces EPUB after chapter confirm without glossary/translation/review
