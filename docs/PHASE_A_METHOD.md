# Phase A Method: Translation and Book Splitter

Phase A is the stable book-processing stage before knowledge extraction. It accepts one PDF or EPUB book, builds BookIR, optionally translates it, renders a reading EPUB, and leaves a minimal, reproducible artifact set for Phase B.

New deployments, including Agent-based scheduled runs, should follow this document rather than ad hoc commands from prior experiments.

## Scope

Phase A handles:

- PDF / EPUB ingest.
- Book profile guardrails.
- BookIR generation: `book.json`.
- Chapter splitting with stable `chapter_id`.
- Image, table, cover, apparatus preservation.
- English or other foreign-language to Chinese translation.
- Polish for mixed untranslated English defects.
- EPUB reading output.
- Internal EPUB href validation.
- Artifact retention and cleanup rules.

Phase A does not handle:

- Knowledge extraction.
- Wiki / mindmap / graph generation.
- Newspaper extraction.
- Formula semantics reconstruction.
- Perfect PDF layout preservation.

## Input Rules

Supported input:

- Text-layer PDF.
- EPUB.

Rejected or weak-support input:

- OCR-only scan PDFs.
- Newspaper-like multi-article layouts.
- Formula-heavy books where formula semantics are core.
- Visually fragmented documents that cannot produce coherent BookIR.

## Language Flow

Phase A has two language paths.

### Foreign-Language Source

For English or other non-Chinese books:

1. Ingest source.
2. Build `book.json`.
3. Generate `book.md` and `translation-input.md`.
4. Translate by chapter/chunk.
5. Render named EPUB.
6. Run polish.
7. Use polished output as the final reading artifact.
8. Feed both original and translated text into Phase B later.

Phase B input for foreign-language books:

- `book.json`
- `book.md`
- `translated.polished.md` if present, otherwise `translated.md`
- `book-images/`
- `manifest.json`
- `chapter-report.json`

### Chinese Source

For Chinese books:

1. Ingest source.
2. Build `book.json`.
3. Generate `book.md`.
4. Skip translation.
5. Optionally render a reading EPUB directly from `book.md`.
6. Feed original text into Phase B.

Implementation requirement:

- If detected source language is Chinese and target language is Chinese, do not call any translation API.
- Mark manifest translation mode as `skipped_same_language`.
- Do not create or require `translation-cache/`.
- Use `book.md` as the reading/knowledge text source.

Recommended language test:

```text
source_language in {"zh", "zh-cn", "zh-tw"}
and target_language startswith "zh"
=> skip translation
```

## Core Commands

Current foreign-language command:

```bash
book-weaver translate SOURCE --profile book --target-lang zh-CN --format epub --translator minimax
book-weaver polish RUN_DIR --target-lang zh-CN --translator minimax
```

Future fixed command shape:

```bash
book-weaver translate SOURCE --profile book --target-lang zh-CN --format epub
book-weaver polish RUN_DIR --target-lang zh-CN
book-weaver finalize RUN_DIR
```

`finalize` is not yet implemented. Until then, use the retention rules below manually or in an Agent script.

## Required Artifacts

These files are required for Phase B or auditability and must be retained.

### Always Retain

- `manifest.json`
- `book.json`
- `book.md`
- `book-trace.md`
- `chapter-report.json`
- `profile.json`
- `book-images/`
- final reading EPUB

### Retain For Foreign-Language Books

- `translation-input.md`
- `translated.md`
- `translated.polished.md` if polish has run
- `polish-report.json` if polish has run

### Retain Temporarily For Resume / Debug

- `translation-cache/`
- `polish-cache/`

These caches are useful during a run and shortly after. They can be deleted after the run is finalized and accepted.

## Cleanup Rules

Can usually be removed after Phase A acceptance:

- `normalized.md`
- `normalized.json`
- `reconstructed.md`
- `chapters/*.md`
- `images/` if all required assets are already in `book-images/`
- old unpolished EPUB if polished EPUB exists
- `.DS_Store`

Keep `normalized.json` only when debugging ingest or Docling output.

Keep `translation-cache/` only when:

- translation is still running,
- the run may be resumed,
- the output has not been accepted,
- or a quality failure needs chunk-level diagnosis.

## Final Artifact Selection

Phase A final text source:

```text
translated.polished.md
else translated.md
else book.md
```

Phase A final EPUB:

```text
<source-stem> (zh-CN polished).epub
else <source-stem> (zh-CN).epub
else source-language reading EPUB if Chinese source
```

Phase B should never guess this. It should read a future `stage1-final.json`, or until that exists, follow the same priority order.

## Quality Gates

A Phase A run is acceptable only if:

- `book.json` exists.
- All chapters have `chapter_id`.
- Named EPUB exists.
- EPUB internal href validation has `resolved_ratio == 1.0`, or unresolved links are explicitly waived.
- For translated books, polish high-confidence candidates are zero after final polish, or remaining candidates are manually accepted as names/citations.
- `English（中文）` generated gloss pollution is zero.
- Images and tables expected in `book-images/` remain present.
- `manifest.json` records source, target language, preflight, render files, and href validation.

## Known Translation Failure Mode

MiniMax can occasionally return a chunk that is mostly Chinese but contains long untranslated English phrases. This is not caught by simple “all English” checks.

Required mitigation:

- Apply chunk-level mixed-language quality gate.
- If a cache file fails the gate, delete that cache file and retry translation.
- After translation, run polish on only suspect lines.
- Do not retranslate the whole book unless chunk-level repair fails.

This is now part of the Phase A contract.

## Agent Scheduled Execution

An Agent can run Phase A on a folder of books.

Recommended logic:

1. Scan input directory for `.pdf` and `.epub`.
2. Skip files with an existing accepted run.
3. Run `book-weaver translate`.
4. If source is non-Chinese, run `book-weaver polish`.
5. Run Phase A QA:
   - check `manifest.json`;
   - check final EPUB exists;
   - check `chapter_id` coverage;
   - check href validation;
   - scan final Markdown for high-confidence untranslated English.
6. Write or update a run status file.
7. Clean temporary artifacts only after successful QA.

Recommended status file:

```json
{
  "schema": "phase_a_status_v1",
  "status": "accepted",
  "source_path": "/absolute/path/to/source.pdf",
  "run_dir": "/absolute/path/to/run",
  "source_language": "en",
  "target_language": "zh-CN",
  "translation_mode": "translated",
  "book_json": "book.json",
  "source_markdown": "book.md",
  "final_markdown": "translated.polished.md",
  "final_epub": "Book Title (zh-CN polished).epub",
  "chapter_count": 31,
  "chapter_id_coverage": 1.0,
  "href_resolved_ratio": 1.0,
  "remaining_polish_candidates": 0,
  "ready_for_phase_b": true
}
```

For Chinese source:

```json
{
  "translation_mode": "skipped_same_language",
  "source_markdown": "book.md",
  "final_markdown": "book.md",
  "ready_for_phase_b": true
}
```

## Phase B Contract

Phase B consumes Phase A outputs only. It should not parse raw PDFs/EPUBs again.

Phase B minimum input:

- `phase_a_status.json` or equivalent manifest pointer.
- `book.json`
- final Markdown selected by Phase A.
- `book-images/`
- `chapter-report.json`

For foreign-language books, Phase B should also use `book.md` as original-language evidence.

For Chinese books, `book.md` is both source and final text.

## Open Implementation Items

- Add a formal `--skip-translation-if-same-language` behavior to `translate`.
- Add `book-weaver finalize RUN_DIR`.
- Add `phase_a_status.json`.
- Add an Agent batch runner or documented cron/automation template.
- Add cleanup command with dry-run mode:

```bash
book-weaver cleanup RUN_DIR --phase-a --dry-run
book-weaver cleanup RUN_DIR --phase-a
```
