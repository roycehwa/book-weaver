# Phase A Corpus-Scale Test Runbook

## Entry Criteria

A run is eligible for corpus-scale testing only when `integrity-ledger.json`
reports all four coverage dimensions at `1.0`:

- required source pages;
- translatable semantic spans;
- packaged assets;
- footnote references and backlinks.

The ledger must also have no unresolved OCR, absolute host paths, PDF body-flow
notes, missing assets, broken links, missing translations, or open review
items. A successful render of one book is not an entry criterion.

## Preflight

Run the complete automated suites:

```bash
cd /path/to/book-weaver
uv sync --extra dev --extra workspace
uv run pytest tests/ -q
uv run pytest backend/ -q
cd frontend
npm run test -- --run
npm run build
```

Record the exact commit, Python/Node versions, fixture version, and test counts.
Warnings are recorded separately from failures.

## Run Verification

Verify any number of completed run directories:

```bash
uv run python scripts/verify_scale_readiness.py --json \
  "/path/to/run-a" \
  "/path/to/run-b"
```

Exit code `0` means every supplied run satisfies the ledger contract. Exit code
`1` means at least one run is blocked; the JSON `errors` list is the work queue.

Use at least:

- one footnote-heavy PDF;
- one PDF with a different note/layout structure;
- one EPUB fixture;
- one malformed OCR/layout fixture.

Do not add title, job ID, publisher, or page-number exceptions when an
observation fails.

## Manual Visual Sampling

For each representative PDF:

- inspect a normal body page;
- inspect a page with several short notes;
- inspect a long-note continuation page;
- confirm no note re-enters body flow and no truncation marker exists.

For each EPUB:

- open it in two EPUB 3 readers;
- activate a note reference;
- follow the chapter-end fallback and backlink;
- inspect the ZIP for absolute paths and missing manifest assets.

## Operational Record

For every corpus batch, retain:

- input count and total pages;
- stage elapsed time;
- translation cache hit/miss counts validated against current hashes;
- peak memory when available;
- output sizes;
- blocked runs grouped by integrity failure;
- migration backup IDs;
- retry and rollback actions.

Never mark a batch ready by manually editing the ledger. Resolve the source
model, translation, review, or renderer defect and regenerate it.

## Rollback

Migration writes timestamped data under `migration-backups/`. To investigate a
regression, compare the backup with current `book.json`, page ledger, integrity
ledger, translated semantic content, and review state. Do not delete current or
backup artifacts until the comparison is complete.
