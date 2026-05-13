# Hermes Agent Operation

Phase A exposes a single-run command for Hermes:

```bash
pdf-translator agent-once --source-root "$HOME/Desktop/文档" --translator minimax
```

Hermes should run this command every two hours. Each invocation processes at most one source file.

## Directory contract

Default root:

```text
$HOME/Desktop/文档/
  EN/  English PDF/EPUB sources
  CN/  Chinese PDF/EPUB sources
  OK/  completed book directories
  NG/  failed book directories
```

The command creates missing directories automatically.

## Processing rules

- Failed `NG` books are retried before new `EN`/`CN` sources while their retry budget remains, using the existing failed book directory so translation cache files are reused.
- After the retry budget is exhausted, the book remains in `NG` for inspection but no longer blocks newer `EN`/`CN` sources.
- The oldest PDF/EPUB under `EN` or `CN` is selected.
- Active work uses a stable directory under `.hermes-working/<book-title>/`; if Hermes times out or the process is killed before NG archiving, the next run reuses that directory and continues from cached chunks.
- `EN` books run the book pipeline with translation to `zh-CN`, then run the polish pass by default.
- `CN` books use `source_language=zh-CN`; when target is also Chinese, translation is skipped and the manifest records `translation_mode=skipped_same_language`.
- A lock file under the source root prevents two overlapping runs from taking the same book.

## Output rules

On success:

```text
OK/<book-title>/
  <original source file>
  manifest.json
  book.json
  book.md
  book-trace.md
  chapter-report.json
  translated.md
  *.epub
  book-images/
  phase-a-status.json
```

On failure:

```text
NG/<book-title>/
  <original source file>
  <any partial pipeline artifacts>
  phase-a-status.json
```

`phase-a-status.json` is the agent-facing status record. It includes source lane, destination, timestamps, retry counters, and error details for NG runs.

On retry success, the recovered book is moved from `NG/<book-title>/` to `OK/<book-title>/`.

## Optional flags

```bash
pdf-translator agent-once \
  --source-root "$HOME/Desktop/文档" \
  --target-lang zh-CN \
  --translator minimax \
  --format epub \
  --ingest-timeout-seconds 240
```

Use `--no-polish` to skip the EN polish pass during smoke tests.
