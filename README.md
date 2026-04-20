# PDF Translator

`pdf-translator` is a pragmatic PDF translation pipeline designed to avoid the hardest part of PDF localization: reusing the original layout.

Instead of translating directly on PDF coordinates, it runs a three-stage flow:

1. Ingest the PDF into a normalized `Markdown + JSON` representation.
2. Translate content in block-sized chunks while preserving Markdown structure.
3. Render a new clean PDF from the translated Markdown.

This keeps user intervention low and removes most layout noise from the translation path.

## Why this architecture

- Better reading order than line-by-line extraction.
- Works for native PDFs and OCR-backed scanned PDFs.
- Easier to debug because every stage has an inspectable intermediate artifact.
- Easy to swap translator backends without touching parsing.

## Stack

- Parsing: `Docling`
- Translation: pluggable backends (`openai`, `mock`)
- Rendering: Markdown -> HTML -> PDF via `reportlab`

## Requirements

- macOS / Linux / Windows
- Python `3.11+`
- For real translation with `openai` backend:
  - `OPENAI_API_KEY`
  - optionally `OPENAI_BASE_URL`
  - optionally `OPENAI_MODEL`

## Quick start

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Run:

```bash
pdf-translator translate /absolute/path/to/file.pdf --target-lang zh-CN
```

Outputs are written to `./runs/<pdf-stem>/` by default:

- `normalized.md`
- `normalized.json`
- `reconstructed.md`
- `translated.md`
- `translated.pdf`
- `manifest.json`

`normalized.md` is the raw Docling export. `reconstructed.md` is the layout-aware reading edition used for translation.

Use `pdf-translator profile /path/to/file.pdf --profile auto` to classify pages into `accept`, `assist`, `skip_content`, and `reject_structure`. The built-in profiles are `magazine`, `book`, and `newspaper`.

Use `pdf-translator articles /path/to/newspaper.pdf` to extract and rank article candidates for newspaper-style PDFs. The command writes `articles.json`, includes article-level `deck/byline/dateline/quality`, and prints a quality summary to support a "translate the most important half" workflow.

The `articles` command now also writes `articles.md`, a readable edition for direct review. By default it includes the selected top-half articles; use `--reading-all` to include every ranked candidate.

Use `pdf-translator reading /path/to/articles.json` to regenerate a readable `articles.md` from an existing JSON artifact without rerunning ingest.

Use `pdf-translator reading-rebuild /path/to/articles.json` to generate a continuity-focused `articles.rebuilt.md` (plus `articles.rebuilt.json`) from existing extraction output. This post-processes article bodies and trims obvious break fragments without rerunning ingest.

Use `pdf-translator validate /path/to/manifest.json` to run a reusable batch regression suite. Each manifest case must include `source_pdf` and `mode`, where `mode` is `profile` or `articles`. Profile cases can also set `profile`; article cases can set `selected_pass_min_pct`.

## Guardrails

Every command that recomputes ingest now runs a preflight check before Docling starts:

- File size
- Page count
- A hard ingest timeout

The defaults are profile-aware:

- `newspaper`: warn above `96` pages or `35MB`; reject above `160` pages or `80MB`
- `magazine`: warn above `140` pages or `50MB`; reject above `220` pages or `100MB`
- `book`: warn above `320` pages or `60MB`; reject above `600` pages or `120MB`
- `auto`: warn above `160` pages or `40MB`; reject above `320` pages or `80MB`

These thresholds are system-protection limits, not content-quality limits. They are meant to stop parser hangs and runaway batch jobs, not to decide whether a page is editorially useful.

All thresholds can be overridden from the CLI:

```bash
pdf-translator profile ./sample.pdf --profile magazine --ingest-timeout-seconds 180
pdf-translator articles ./paper.pdf --max-file-size-mb 120 --max-page-count 220
pdf-translator validate ./suite.json --ingest-timeout-seconds 240
```

When batch validation hits a protected failure, it now records the branch as one of:

- `input_gate`
- `timeout`
- `ingest_error`
- `unexpected_error`

and continues to the next file instead of hanging the entire batch.

## Translator backends

### `openai`

Uses the OpenAI Python SDK with an OpenAI-compatible endpoint.

```bash
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-4.1-mini
pdf-translator translate ./paper.pdf --target-lang zh-CN --translator openai
```

### `mock`

Useful for validating the pipeline without spending tokens.

```bash
pdf-translator translate ./paper.pdf --target-lang zh-CN --translator mock
```

## Notes

- The output PDF is intentionally reflowed. It is a translated reading edition, not a coordinate-faithful clone of the source PDF.
- Tables and images depend on how well Docling exports them to Markdown.
- If you need original-layout replacement later, treat that as a separate downstream project.
