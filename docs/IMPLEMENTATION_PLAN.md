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
   - Any source language may enter directly as `source_only`.
   - When a translated reading layer is required, use original BookIR plus the explicitly approved reviewed translation as `source_plus_translation`.
   - A machine translation under active review must not silently become the Phase B reading layer.
5. Branch B writes `knowledge/` artifacts in a user-feedback loop: chapters, semantic units, reader brief, feedback objects, profile-specific machine candidates, joint draft, accepted knowledge, and exports.

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

## Phase 4: Reader Brief And Feedback Intake

Goal: Turn BookIR and planning outputs into a reader-facing entry point, then accept user understanding without forcing users to review machine graph fragments.

Commands:

```bash
book-weaver knowledge brief RUN_DIR
book-weaver knowledge feedback RUN_DIR --input feedback.md
```

Outputs:

- `knowledge/reader-brief.md`
- `knowledge/reader-brief.html`
- `knowledge/feedback-template.md`
- `knowledge/feedback/raw/*.json`
- `knowledge/feedback/aligned/*.json`

Implementation steps:

1. Render a readable book frame from BookIR, metadata prior, network plan, chapter decisions, and light chapter samples.
2. Include part map, chapter cards, high-value sections, apparatus sections, and current network organization judgment.
3. Accept natural-language feedback: book-level frame corrections, reading goals, highlights, chapter notes, concept hints, relation hints, external reference material, and disagreements.
4. Preserve raw feedback before any model rewriting.
5. Align feedback to `chapter_id`, `unit_id`, page ranges, excerpt hashes, and candidate nodes when possible.
6. Keep unaligned whole-book insights as valid feedback objects.

Acceptance:

- Users can give useful feedback from the brief without opening JSON or raw candidate graphs.
- Imported or pasted highlights remain traceable to original user input.
- External reviews and recommendations remain weak priors, not accepted source evidence.
- Chinese original-only runs and bilingual translated runs use the same feedback path.

## Phase 5: Joint Draft And Accepted Knowledge

Goal: Fuse user feedback and machine candidates into an inspectable knowledge draft, then create a clean accepted layer.

Commands:

```bash
book-weaver knowledge extract RUN_DIR --network-model argument_network
book-weaver knowledge draft RUN_DIR
book-weaver knowledge accept RUN_DIR
```

Outputs:

- `knowledge/candidates/`
- `knowledge/joint-draft.md`
- `knowledge/joint-draft.html`
- `knowledge/accepted/nodes.jsonl`
- `knowledge/accepted/edges.jsonl`
- `knowledge/accepted/quality-report.json`

Implementation steps:

1. Standardize candidate provenance across profile-specific extractors.
2. Keep machine candidates separate from user feedback.
3. Render a Joint Draft organized by the selected profile: questions/claims for argumentative books, events/timelines for historical books, procedures/playbooks for practical books, and so on.
4. Mark every draft item as source-derived, machine candidate, user-observed, user-supported, or accepted.
5. Show conflicts, unmatched feedback, uncertain relations, and missing evidence.
6. Promote only policy-compliant items into accepted knowledge.

Acceptance:

- The Joint Draft is readable as a structured book knowledge memo.
- Every accepted item has provenance or an explicit user-origin state.
- User disagreement is not collapsed into the author's claim.
- Candidate extraction can improve without rewriting accepted export contracts.

## Phase 6: Local Knowledge Export

Goal: Export accepted knowledge into a usable local knowledge base before binding to external platforms.

Command:

```bash
book-weaver knowledge export RUN_DIR --format markdown-vault
```

Outputs:

- `knowledge/export/markdown-vault/`
- Book index pages.
- Concept / claim / event / case pages as supported by the profile.
- Source and provenance links.
- Mindmap and graph JSON sidecars where useful.

Implementation steps:

1. Export only accepted knowledge plus explicit user-authored insight objects.
2. Keep a local manifest for idempotent export.
3. Preserve links back to BookIR, source text, translation where available, and feedback sources.
4. Keep local Markdown as the first usable target; add Notion, Obsidian, Neo4j, or other platforms after export contracts stabilize.

Acceptance:

- The export is browsable without a graph database.
- A reader can move from book overview to connected nodes and back to evidence.
- Re-running export does not duplicate nodes.

## Phase 7: External Workspace Export

Goal: Use Notion and other platforms as workspaces or destinations, not as the source of truth.

Possible targets:

- Notion
- Obsidian
- Neo4j
- RAG/search indexes

Notion requirements when implemented:

- Generate a local export manifest before writing remotely.
- Store local IDs and remote IDs for idempotent updates.
- Keep Notion AI optional and manual; do not depend on it for pipeline correctness.

## Phase 8: Batch And Profile System

Goal: Make the project usable across a directory of books.

Command:

```bash
book-weaver batch /path/to/books --profile book --format epub --polish --knowledge-stage brief
```

Implementation steps:

1. Add batch manifest with one row per source file.
2. Track states: `pending`, `ingesting`, `translated`, `polished`, `brief_ready`, `feedback_waiting`, `draft_ready`, `knowledge_exported`, `failed`.
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

## Phase 9: Optional Reader-Oriented Features

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
4. Phase 4: Reader Brief and feedback intake
5. Phase 5: Joint Draft and accepted knowledge
6. Phase 6: local Markdown vault export
7. Phase 7: external workspace exports
8. Phase 8: batch mode
9. Phase 9: optional reader QA features

## Do Not Do Yet

- Do not reintroduce newspaper extraction.
- Do not make OCR a mainline requirement.
- Do not pursue coordinate-faithful translated PDF output as the primary deliverable.
- Do not depend on Notion AI for automated correctness.
- Do not copy AGPL or non-commercial licensed code from reference projects.
