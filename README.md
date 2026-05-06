# PDF Translator

`pdf-translator` is a pragmatic PDF translation pipeline designed to avoid the hardest part of PDF localization: reusing the original layout.

Instead of translating directly on PDF coordinates, it runs a three-stage flow:

1. Ingest the PDF into a normalized `Markdown + JSON` representation.
2. Rebuild books into a structured `book.json` plus inspectable Markdown views.
3. Translate book chapters in chapter-aware chunks while preserving Markdown structure.
4. Render a clean EPUB reading edition by default, with PDF still available as an optional output.

This keeps user intervention low and removes most layout noise from the translation path.

## Why this architecture

- Better reading order than line-by-line extraction.
- Works for native PDFs and OCR-backed scanned PDFs.
- Easier to debug because every stage has an inspectable intermediate artifact.
- Easy to swap translator backends without touching parsing.

## Stack

- Parsing: `Docling`
- Translation: pluggable backends (`minimax`, `compatible`, `openai`, `mock`)
- Rendering: Markdown -> EPUB via the Python standard library; optional Markdown -> PDF via `reportlab`

## Requirements

- macOS / Linux / Windows
- Python `3.11+`
- For real translation with the default `minimax` backend:
  - `MINIMAX_API_KEY`
  - optionally `MINIMAX_BASE_URL` (defaults to `https://api.minimaxi.com/anthropic/v1/messages`)
  - optionally `MINIMAX_MODEL` (defaults to `MiniMax-M2.7-highspeed`)
  - optionally `MINIMAX_MAX_TOKENS` (defaults to `8192`; long zh book chunks often need headroom)
  - optionally `MINIMAX_HTTP_TIMEOUT_SECONDS` (defaults to `600`; single-request wall clock for slow completions)
- For other OpenAI-compatible domestic APIs:
  - `LLM_API_KEY`
  - `LLM_BASE_URL`
  - `LLM_MODEL`

## Quick start

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Run:

```bash
pdf-translator translate /absolute/path/to/file.pdf --profile book --target-lang zh-CN
```

Outputs are written to `./runs/<pdf-stem>/` by default:

- `normalized.md`
- `normalized.json`
- `reconstructed.md`
- `translated.md`
- `translated.epub`
- `manifest.json`

`normalized.md` is the raw Docling export. For book profile runs, `book.json` is the source of truth, `book.md` is the cleaned reading view, `translation-input.md` is the chapter-aware translation source, and `translated.epub` is the default reading output.

Use `pdf-translator profile /path/to/file.pdf --profile auto` to classify pages into `accept`, `assist`, `skip_content`, and `reject_structure`. The built-in profiles are `magazine` and `book`.

The project scope is **magazine** and **book** workflows (plus `auto` classification). There is no newspaper or generic article-extraction pipeline in this repository.

Use `pdf-translator validate /path/to/manifest.json` to run a reusable batch regression suite. Each manifest case must include `source_pdf` and `mode`; only `mode: "profile"` is supported (book/magazine/auto page gating).

## Guardrails

Every command that recomputes ingest now runs a preflight check before Docling starts:

- File size
- Page count
- A hard ingest timeout

The defaults are profile-aware:

- `magazine`: warn above `140` pages or `50MB`; reject above `220` pages or `100MB`
- `book`: warn above `800` pages or `60MB`; reject above `1500` pages or `120MB`
- `auto`: warn above `160` pages or `40MB`; reject above `320` pages or `80MB`

These thresholds are system-protection limits, not content-quality limits. They are meant to stop parser hangs and runaway batch jobs, not to decide whether a page is editorially useful.

All thresholds can be overridden from the CLI:

```bash
pdf-translator profile ./sample.pdf --profile magazine --ingest-timeout-seconds 180
pdf-translator translate ./book.pdf --profile book --max-file-size-mb 120 --max-page-count 1500
pdf-translator validate ./suite.json --ingest-timeout-seconds 240
```

When batch validation hits a protected failure, it now records the branch as one of:

- `input_gate`
- `timeout`
- `ingest_error`
- `unexpected_error`

and continues to the next file instead of hanging the entire batch.

## Translator backends

### `minimax`

Default real translation backend. Uses MiniMax's Anthropic-compatible Messages endpoint.

```bash
cat > .env <<'EOF'
MINIMAX_API_KEY=...
MINIMAX_MODEL=MiniMax-M2.7-highspeed
# Optional; this is the built-in default.
MINIMAX_BASE_URL=https://api.minimaxi.com/anthropic/v1/messages
MINIMAX_MAX_TOKENS=8192
# Optional; increase if the API is slow to return full completions.
MINIMAX_HTTP_TIMEOUT_SECONDS=600
EOF
pdf-translator translate ./book.pdf --profile book --target-lang zh-CN --translator minimax --format epub
```

Put these variables once in a local `.env` file at the project root. `.env` is ignored by git.

### `compatible`

Use this for DeepSeek, Moonshot, Qwen, or any provider exposing an OpenAI-compatible `/chat/completions` endpoint.

```bash
export LLM_API_KEY=...
export LLM_BASE_URL=https://provider.example/v1
export LLM_MODEL=provider-model-name
pdf-translator translate ./book.pdf --profile book --target-lang zh-CN --translator compatible --format epub
```

### `openai`

Uses the OpenAI Responses API.

```bash
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-4.1-mini
pdf-translator translate ./book.pdf --profile book --target-lang zh-CN --translator openai
```

### `mock`

Useful for validating the pipeline without spending tokens.

```bash
pdf-translator translate ./book.pdf --profile book --target-lang zh-CN --translator mock
```

## Notes

- The output EPUB/PDF is intentionally reflowed. It is a translated reading edition, not a coordinate-faithful clone of the source PDF.
- Book pipeline invariants are documented in [`docs/BOOK_PIPELINE_RULES.md`](docs/BOOK_PIPELINE_RULES.md).
- For PDF books, tables, charts, figures, and covers are preserved as visual assets whenever crops are available. They should not be converted to Markdown tables by default.
- If you need original-layout replacement later, treat that as a separate downstream project.
