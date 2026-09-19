# BookWeaver

BookWeaver is a local first application for reconstructing, translating, reviewing, and exporting books. This repository contains **Phase A only**.

The current specification and continuation point is [docs/PHASE-A-CURRENT.md](docs/PHASE-A-CURRENT.md). Implementation work must begin there.

## User workflow

1. Import a text based PDF or EPUB.
2. Inspect the logically reconstructed book and confirm the source text, chapters, and content policies.
3. Confirm terminology extracted from the approved translation scope.
4. Review exceptions and export the completed book.

PDF and EPUB use different reconstruction evidence internally, but both produce the same `reading-units.json` contract. Format specific reconstruction does not create extra user steps.

## Current pipeline

```text
format ingest
  -> logical reconstruction
  -> user confirmation
  -> terminology finalization
  -> frozen reading units
  -> transport chunks
  -> translation and constrained polish
  -> exception review
  -> validated export
```

Translation cannot start from an unconfirmed chapter plan. Current runs require a manifest, user confirmed reading units, chapter segments derived from those units, and the required quality ledgers.

## Development setup

Requirements:

- Python 3.11 or newer
- `uv`
- Node.js and `pnpm`

```bash
uv sync --extra dev --extra workspace
cd frontend
pnpm install --frozen-lockfile
```

No provider credentials are required for the automated test suite. Keep `.env` files and real books outside Git.

## Verification

From the repository root:

```bash
uv run pytest -q
```

From `frontend/`:

```bash
pnpm test:run
pnpm build
pnpm lint
```

Then run:

```bash
git diff --check
```

Current counts and remaining acceptance work are recorded in [docs/PHASE-A-CURRENT.md](docs/PHASE-A-CURRENT.md).

## Agent work

All coding agents must follow [AGENTS.md](AGENTS.md). Cursor write tasks run in isolated worktrees from a pushed baseline. Existing task directories, translated books, local output, and historical reports are not acceptance evidence.
