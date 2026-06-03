# Phase A Method: Book Intake and Optional Translation

Phase A is the stable book intake stage before knowledge extraction. It accepts one PDF or EPUB book, builds BookIR, and leaves a minimal, reproducible artifact set for Phase B. Translation is an optional reading/language-normalization branch, not the mainline.

New deployments, including Agent-based scheduled runs, should follow this document rather than ad hoc commands from prior experiments.

## Scope

Phase A handles:

- PDF / EPUB ingest.
- Book profile guardrails.
- BookIR generation: `book.json`.
- Chapter splitting with stable `chapter_id`.
- Image, table, cover, apparatus preservation.
- Optional English or other foreign-language to Chinese translation.
- Optional polish for mixed untranslated English defects.
- Optional EPUB reading output.
- Internal EPUB href validation when EPUB output is rendered.
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

Phase A has a required intake path and an optional translation path.

### Required Intake Path

For every supported book, regardless of language:

1. Ingest source.
2. Build `book.json`.
3. Generate `book.md`, `book-trace.md`, `translation-input.md`, and `chapter-report.json`.
4. Feed `book.json + book.md + book-images/` into Phase B.

Command:

```bash
book-weaver intake SOURCE --profile book
```

This command must not call any translation API and must not require `translation-cache/`.

### Foreign-Language Source

For English or other non-Chinese books, translation is selected only when the user needs a translated reading edition or bilingual knowledge input:

1. Run the required intake path.
2. Translate by chapter/chunk.
3. Render named EPUB.
4. Run polish when needed.
5. Use polished output as the final reading artifact.
6. Feed both original and translated text into Phase B when bilingual input is needed.

Phase B input for foreign-language books:

- `book.json`
- `book.md`
- `translated.polished.md` if present, otherwise `translated.md`
- `book-images/`
- `manifest.json`
- `chapter-report.json`

### Chinese Source

For Chinese books:

1. Run the required intake path.
2. Skip translation.
3. Feed original text into Phase B.
4. Optionally render a source-language reading EPUB later if needed.

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

Current mainline command:

```bash
book-weaver intake SOURCE --profile book
book-weaver knowledge build RUN_DIR
book-weaver knowledge metadata RUN_DIR
book-weaver knowledge plan RUN_DIR --metadata-prior auto
book-weaver finalize RUN_DIR
```

Current optional foreign-language reading command:

```bash
book-weaver translate SOURCE --profile book --target-lang zh-CN --format epub --translator minimax
book-weaver polish RUN_DIR --target-lang zh-CN --translator minimax
```

Optional reading command shape:

```bash
book-weaver translate SOURCE --profile book --target-lang zh-CN --format epub
book-weaver polish RUN_DIR --target-lang zh-CN
book-weaver finalize RUN_DIR
```

`finalize` writes `phase_a_status.json`, which is the preferred handoff file for Agent execution and Phase B.

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

### Retain For Translated Books

- `translation-input.md`
- `translated.md`
- `translated.polished.md` if polish has run
- `polish-report.json` if polish has run
- final reading EPUB

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

Phase A final text source for downstream use:

```text
translated.polished.md
else translated.md
else book.md
```

Phase A final EPUB when the reading branch ran:

```text
<source-stem> (zh-CN polished).epub
else <source-stem> (zh-CN).epub
else source-language reading EPUB if Chinese source
```

Phase B should never guess this. It should read a future `stage1-final.json`, or until that exists, follow the same priority order.

## Quality Gates

A Phase A intake run is acceptable only if:

- `book.json` exists.
- All chapters have `chapter_id`.
- Images and tables expected in `book-images/` remain present.
- `manifest.json` records source, preflight, render policy, and artifact paths.

A translated reading run additionally requires:

- Named EPUB exists.
- EPUB internal href validation has `resolved_ratio == 1.0`, or unresolved links are explicitly waived.
- Polish high-confidence candidates are zero after final polish, or remaining candidates are manually accepted as names/citations.
- `English（中文）` generated gloss pollution is zero.
- `manifest.json` records target language, render files, and href validation.

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
3. Run `book-weaver intake`.
4. If a translated reading artifact or bilingual Phase B input is required, run `book-weaver translate`.
5. If translation ran, run `book-weaver polish` when QA detects mixed-language defects.
6. Run Phase A QA:
   - check `manifest.json`;
   - check `chapter_id` coverage;
   - check `book-images/`;
   - if translation ran, check final EPUB, href validation, and high-confidence untranslated English.
7. Run `book-weaver finalize RUN_DIR`.
8. Clean temporary artifacts only after successful QA:

```bash
book-weaver cleanup RUN_DIR --dry-run
book-weaver cleanup RUN_DIR
```

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

- Add an Agent batch runner or documented cron/automation template.
- Add stricter `finalize` gates for image/table coverage once image expectations become fully deterministic.
