# Implementation Plan

This plan turns the current book translation prototype into a staged book-processing system. The scope is translation, EPUB reading output, post-editing, and downstream knowledge processing. Newspaper extraction remains out of scope.

## Guiding Principles

- `book.json` stays the source of truth. Markdown is an inspection and transport view, not the canonical structure.
- EPUB reading quality comes before original-PDF layout preservation.
- Protected visual or structural objects are not sent to translation models unless explicitly marked translatable.
- Expensive model calls must be resumable, cached, and limited to text that needs the operation.
- Knowledge extraction reuses the same chapter IDs and page ranges as translation.
- Translation is a language-normalization stage, not always the final product. English or other foreign-language books may flow through translation before knowledge extraction; Chinese books can enter knowledge extraction directly.
- Branch A produces reading deliverables. Branch B produces knowledge assets. Both branches consume the same BookIR.

## Unified Book Flow

Input: one PDF or EPUB book.

1. Ingest and normalize into `book.json`.
2. Detect source language and build stable chapter IDs.
3. If the user only wants a reading deliverable, run Branch A and stop.
4. If the user wants knowledge processing:
   - Chinese source: use original BookIR and original text.
   - Non-Chinese source: translate first, then use original + translation as bilingual knowledge input.
5. Branch B writes `knowledge/` artifacts: chapters, semantic units, suitability report, profile-specific candidates, wiki/index/graph outputs.

## Phase 1: Polish As A Formal Stage

Goal: Turn the successful `Mourning` experiment into a repeatable command.

Command:

```bash
book-weaver polish RUN_DIR --target-lang zh-CN
```

Inputs:

- `RUN_DIR/book.json`
- `RUN_DIR/translated.md`
- `RUN_DIR/<source-stem> (target-lang).epub`

Outputs:

- `translated.polished.md`
- `<run-title> (target-lang polished).epub`
- `polish-report.json`
- `polish-cache/`

Implementation steps:

1. Add `polish.py` with candidate scanning for mixed-language defects.
2. Classify candidates into `high_confidence`, `term_or_name`, `citation_or_reference`, and `skip`.
3. Send only high-confidence sentence/line candidates to the configured translator.
4. Add safety gates before accepting model output: length ratio, CJK count, Markdown structure, image count, footnote markers, heading preservation.
5. Re-render EPUB from accepted polished Markdown and original `book.json`.
6. Record every accepted/rejected change in `polish-report.json`.

Acceptance:

- Images and tables count unchanged.
- No accepted polish line loses more than the configured information threshold.
- Known `Mourning` examples improve without deleting paragraphs.
- All accepted changes are reproducible from cache.

Reference projects:

- `PDFMathTranslate`: cache and exception philosophy.
- `zotero-pdf-translate`: sentence-by-sentence operation.

## Phase 2: Translation Run Controls

Goal: Make long book translation controllable and restartable.

Commands:

```bash
book-weaver translate SOURCE --profile book --target-lang zh-CN --format epub
book-weaver translate SOURCE --profile book --pages 1-120 --resume
book-weaver translate SOURCE --profile book --ignore-cache
```

Implementation steps:

1. Add `--pages` for partial translation and testing.
2. Add explicit `--resume` semantics around existing `translation-cache`.
3. Add `--ignore-cache` to force regeneration.
4. Add progress JSON during translation: chunk total, done, failed, retrying, elapsed, estimated remaining.
5. Write a final `quality-report.json` with untranslated, incomplete, and mixed-language signals.

Acceptance:

- A killed translation run resumes without duplicating successful chunks.
- Partial page runs still generate valid `book.json` and EPUB.
- Progress can be read without inspecting cache filenames manually.

Reference projects:

- `PDFMathTranslate`: partial translation, cache controls, HTTP progress model.
- `LLM_PDF_Translator`: skip already translated files in batch runs.

## Phase 3: Protected Object And Apparatus Classification

Goal: Improve structure handling before translation and knowledge export.

Objects:

- `cover`
- `figure`
- `table`
- `formula`
- `caption`
- `footnote`
- `endnote`
- `references`
- `bibliography`
- `index`
- `glossary`
- `appendix`
- `toc`

Implementation steps:

1. Extend BookIR objects with `object_type`, `translate_policy`, `knowledge_policy`, and `source_pages`.
2. Add formula/math-fragment detection and mark as `math_weak_support`.
3. Add LLM-assisted apparatus classifier for ambiguous sections, but only after deterministic rules.
4. Keep apparatus sections in EPUB when useful, but default to `preserve_original`.
5. Add per-section decisions to `book.json.metadata.classification_report`.

Acceptance:

- References, index, glossary, and notes do not leak into main narrative translation.
- Formula-heavy books generate valid EPUB with explicit weak-support warnings.
- Tables and figures remain visual assets unless no crop exists.

Reference projects:

- `PDFMathTranslate`: formulas/charts/TOC/annotations as protected layout objects.
- `LLM_PDF_Translator`: LLM-based reference checking.

## Phase 4: Knowledge Export Core

Goal: Add a second branch from the same BookIR for knowledge processing.

Command:

```bash
book-weaver knowledge-export RUN_DIR --format json
```

Outputs:

- `knowledge/book.json`
- `knowledge/chapters.jsonl`
- `knowledge/concepts.jsonl`
- `knowledge/quotes.jsonl`
- `knowledge/claims.jsonl`
- `knowledge/summary.json`

Schema:

- `chapter_id`
- `chapter_title`
- `page_start`
- `page_end`
- `source_path`
- `translated_epub_path`
- `summary_short`
- `summary_long`
- `key_concepts`
- `people`
- `places`
- `works`
- `claims`
- `quotes`
- `links`

Implementation steps:

1. Add stable `chapter_id` to BookIR.
2. Generate chapter-level summaries from translated text.
3. Extract concepts, people, places, works, claims, and quotes per chapter.
4. Add cross-chapter concept normalization.
5. Add deterministic JSONL outputs first; defer Notion writing until schemas stabilize.

Acceptance:

- Knowledge export works without Notion.
- Every extracted item points back to `chapter_id` and page range.
- Re-running export is deterministic when using cache.

Reference projects:

- `zotero-pdf-translate`: annotations, notes, metadata as first-class research objects.

## Phase 5: Notion Export

Goal: Use Notion as a knowledge workspace, not as the primary translation engine.

Command:

```bash
book-weaver notion-export RUN_DIR --database-id DATABASE_ID
```

Notion databases:

- `Books`
- `Chapters`
- `Concepts`
- `Quotes`
- `Claims`
- `People`
- `Places`
- `Works`

Implementation steps:

1. Generate `notion-export/manifest.json` before writing to Notion.
2. Map Books and Chapters first.
3. Add Concepts, Quotes, and Claims after schema validation.
4. Store local IDs and Notion page IDs for idempotent updates.
5. Keep Notion AI optional and manual; do not depend on it for pipeline correctness.

Acceptance:

- Re-running does not create duplicate pages.
- Each Notion chapter page links back to source file, EPUB, chapter ID, and page range.
- Notion export can be skipped without affecting EPUB output.

Reference:

- Notion API supports pages, databases, blocks, users, comments, and content operations; it should be treated as a storage and collaboration API, not a guaranteed AI processing API.

## Phase 6: Batch And Profile System

Goal: Make the project usable across a directory of books.

Command:

```bash
book-weaver batch /path/to/books --profile book --format epub --polish --knowledge-export
```

Implementation steps:

1. Add batch manifest with one row per source file.
2. Track states: `pending`, `ingesting`, `translated`, `polished`, `knowledge_exported`, `failed`.
3. Skip already completed stages by default.
4. Add per-file warnings for scanned PDFs, huge files, math-heavy files, and poor chapter structure.
5. Generate batch-level summary HTML or Markdown.

Acceptance:

- Batch can be interrupted and resumed.
- Failed files do not stop the whole batch.
- Summary shows quality metrics and output paths.

Reference projects:

- `PDFMathTranslate`: directory/batch translation.
- `LLM_PDF_Translator`: skip already translated outputs.

## Phase 7: Optional Reader-Oriented Features

Goal: Improve human QA and selective work without bloating the core pipeline.

Possible features:

- Side-by-side chapter HTML for QA.
- Per-chapter retranslate.
- Per-chapter polish.
- Glossary injection.
- User-approved terminology map.
- Zotero-style annotation export.

Reference projects:

- `zotero-pdf-translate`: reader selection, annotation comments, notes.
- `pdf-translator-for-human`: human-in-the-loop local reading workflow.

## Priority Order

1. Phase 1: `polish`
2. Phase 2: translation run controls
3. Phase 3: protected object and apparatus classification
4. Phase 4: knowledge export JSON
5. Phase 5: Notion export
6. Phase 6: batch mode
7. Phase 7: optional reader QA features

## Do Not Do Yet

- Do not reintroduce newspaper extraction.
- Do not make OCR a mainline requirement.
- Do not pursue coordinate-faithful translated PDF output as the primary deliverable.
- Do not depend on Notion AI for automated correctness.
- Do not copy AGPL or non-commercial licensed code from reference projects.
