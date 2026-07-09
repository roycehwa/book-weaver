# BookWeaver

**Phase A** is this repository: translation engine, workspace API, and web UI — one project named **book-weaver**.

**Phase B** (knowledge extraction) is [`book-knowledge`](https://github.com/roycehwa/book-knowledge). See `docs/PHASE_BOUNDARY.md`.

There is no separate Bookmate repository; older `bookmate-review` checkouts should migrate here.

Instead of translating directly on PDF coordinates, it runs a staged flow:

1. Ingest the PDF into a normalized `Markdown + JSON` representation.
2. Rebuild books into a structured `book.json` plus inspectable Markdown views.
3. Prepare stable chapter, page, asset, and provenance structures for downstream knowledge extraction.
4. Translate book chapters only when a reading edition or bilingual knowledge input is needed.
5. Render a clean EPUB reading edition when the translation branch is selected, with PDF still available as an optional output.

This keeps user intervention low, removes most layout noise from the reading path, and keeps the intermediate structure reusable for later wiki, graph, mindmap, and knowledge-base workflows.

## Why this architecture

- Better reading order than line-by-line extraction.
- Works for text-layer PDFs and EPUBs; OCR-only scans are rejected or treated as weak support.
- Easier to debug because every stage has an inspectable intermediate artifact.
- Easy to swap translator backends without touching parsing.
- Knowledge extraction can reuse the same BookIR instead of rebuilding structure again.

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
  - optionally `MINIMAX_HTTP_TIMEOUT_SECONDS` (defaults to `120`; single-request stall guard)
  - optionally `MINIMAX_MAX_CONCURRENCY` (defaults to `3`)
  - optionally `MINIMAX_RPM` (defaults to `500` for MiniMax-M2.7/highspeed)
  - optionally `MINIMAX_TPM` (defaults to `20000000` for MiniMax-M2.7/highspeed)
- For other OpenAI-compatible domestic APIs:
  - `LLM_API_KEY`
  - `LLM_BASE_URL`
  - `LLM_MODEL`

## Quick start

### Engine CLI

```bash
uv sync --extra dev
uv run book-weaver --help
```

### Workspace (API + UI)

```bash
uv sync --extra dev --extra workspace
cd backend && uv run uvicorn main:app --host 127.0.0.1 --port 8000
# another terminal:
cd frontend && npm install && npm run dev
```

Jobs default to `~/Desktop/文档/Bookmate/Jobs`. No `BOOK_WEAVER_HOME` needed in this unified repo.

### Legacy pip install

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Mainline ingest without translation:

```bash
book-weaver intake /absolute/path/to/file.pdf --profile book
book-weaver finalize runs/<file-stem>

# Phase B (separate repo: book-knowledge)
# book-knowledge build runs/<file-stem>
book-weaver finalize runs/<file-stem>
```

Run translation only when a translated reading edition is needed:

```bash
book-weaver translate /absolute/path/to/file.pdf --profile book --target-lang zh-CN
book-weaver review status runs/<file-stem>
book-weaver review rewrite runs/<file-stem> --translator minimax
book-weaver review export runs/<file-stem> --version reviewed-v1 --approve --format epub
book-weaver finalize runs/<file-stem>
```

The previous `pdf-translator` command remains available as a compatibility alias during the transition.

Outputs are written to `./runs/<pdf-stem>/` by default.

Mainline `intake` outputs:

- `normalized.md`
- `normalized.json`
- `reconstructed.md`
- `book.json`
- `book.md`
- `book-trace.md`
- `chapter-report.json`
- `manifest.json`

Translation branch outputs additionally include:

- `translated.md`
- `<source-stem> (zh-CN).epub`
- `translation-cache/`

`normalized.md` is the raw Docling export. For book profile runs, `book.json` is the source of truth, `book.md` is the cleaned reading view, and `translation-input.md` is the chapter-aware translation source when translation is requested. `translated.md` remains a stable internal intermediate for cache reuse, polish, and diffing.

`book-weaver knowledge build` writes `knowledge/bilingual-input.json` and a readable `knowledge/bilingual-input.md` summary in addition to `semantic-units.json`. The bilingual contract is conservative: unit-level translated text is used only when original and translated blocks align safely; otherwise chapter-level translation is preserved as reading context without fake paragraph pairing.

Phase B is not Chinese-only. `finalize` writes a `phase_a_status_v2` handoff with one of two input modes:

- `source_only`: Chinese, English, or another source language enters Phase B directly from `book.json` and `book.md`.
- `source_plus_translation`: the original BookIR is paired with a translated reading layer.

If translation review has started, the machine translation is not used by Phase B until `book-weaver review export ... --approve` creates an approved reviewed version. While review is pending, the source book remains a valid `source_only` Phase B input.

`book-weaver knowledge extract` is profile-specific. The first implemented extractor is `argument_network`, which writes `knowledge/extracted-nodes.json`, `knowledge/extracted-edges.json`, and `knowledge/extraction-report.md`. Other network models intentionally return an unsupported report until their own algorithms are implemented.

The Phase B user-feedback baseline is defined in [`docs/PHASE_B_FEEDBACK_WORKFLOW.md`](docs/PHASE_B_FEEDBACK_WORKFLOW.md). `book-weaver knowledge brief` writes `knowledge/reader-brief.md`, `knowledge/reader-brief.html`, and `knowledge/feedback-template.md`. `book-weaver knowledge feedback RUN_DIR --input feedback.md` preserves raw reader input under `knowledge/feedback/raw/`, writes deterministic first-pass alignment under `knowledge/feedback/aligned/`, and applies structural feedback such as network-model correction, preserve/skip requests, and external reference priors back to `knowledge/plan.json`.

After QA, `book-weaver finalize runs/<file-stem>` writes `phase_a_status.json` for downstream agents. Use `book-weaver cleanup runs/<file-stem> --dry-run` before deleting temporary ingest/cache files.

Use `book-weaver profile /path/to/file.pdf --profile auto` to classify pages into `accept`, `assist`, `skip_content`, and `reject_structure`. The built-in profiles are `magazine` and `book`.

The project scope is **magazine** and **book** workflows (plus `auto` classification). There is no newspaper or generic article-extraction pipeline in this repository.

Use `book-weaver validate /path/to/manifest.json` to run a reusable batch regression suite. Each manifest case must include `source_pdf` and `mode`; only `mode: "profile"` is supported (book/magazine/auto page gating).

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
book-weaver profile ./sample.pdf --profile magazine --ingest-timeout-seconds 180
book-weaver intake ./book.pdf --profile book --max-file-size-mb 120 --max-page-count 1500
book-weaver translate ./book.pdf --profile book --max-file-size-mb 120 --max-page-count 1500
book-weaver validate ./suite.json --ingest-timeout-seconds 240
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
# Optional; increase only if full completions regularly need longer.
MINIMAX_HTTP_TIMEOUT_SECONDS=120
MINIMAX_MAX_CONCURRENCY=3
MINIMAX_RPM=500
MINIMAX_TPM=20000000
EOF
book-weaver translate ./book.pdf --profile book --target-lang zh-CN --translator minimax --format epub
```

Put these variables once in a local `.env` file at the project root. `.env` is ignored by git.

### `compatible`

Use this for DeepSeek, Moonshot, Qwen, or any provider exposing an OpenAI-compatible `/chat/completions` endpoint.

```bash
export LLM_API_KEY=...
export LLM_BASE_URL=https://provider.example/v1
export LLM_MODEL=provider-model-name
book-weaver translate ./book.pdf --profile book --target-lang zh-CN --translator compatible --format epub
```

### `openai`

Uses the OpenAI Responses API.

```bash
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-4.1-mini
book-weaver translate ./book.pdf --profile book --target-lang zh-CN --translator openai
```

### `mock`

Useful for validating the pipeline without spending tokens.

```bash
book-weaver translate ./book.pdf --profile book --target-lang zh-CN --translator mock
```

## Notes

- The output EPUB/PDF is intentionally reflowed. It is a translated reading edition, not a coordinate-faithful clone of the source PDF.
- Project identity and rename rules are documented in [`docs/PROJECT_IDENTITY.md`](docs/PROJECT_IDENTITY.md).
- Book pipeline invariants are documented in [`docs/BOOK_PIPELINE_RULES.md`](docs/BOOK_PIPELINE_RULES.md).
- Bilingual knowledge handoff rules are documented in [`docs/BILINGUAL_KNOWLEDGE_CONTRACT.md`](docs/BILINGUAL_KNOWLEDGE_CONTRACT.md).
- For PDF books, tables, charts, figures, and covers are preserved as visual assets whenever crops are available. They should not be converted to Markdown tables by default.
- If you need original-layout replacement later, treat that as a separate downstream project.
