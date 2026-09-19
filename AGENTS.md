# BookWeaver Phase A agent rules

## Scope

- Work only on Phase A: ingest, logical reconstruction, source and chapter confirmation, terminology, translation, review, and export.
- Start every task from `docs/PHASE-A-CURRENT.md`.
- Use `docs/PHASE-A-PIPELINE-REDESIGN-2026-09-19.md` for the current reconstruction design.
- Treat `docs/REFLOW-DESIGN-2026-09-18.md` as a future design boundary, not implemented behavior.

## Current workflow contract

```text
format ingest -> logical reconstruction -> user confirmation
-> terminology finalization -> frozen reading units -> translation
-> exception review -> validated export
```

- PDF and EPUB adapters must converge on `bookweaver_reading_units_v1`.
- High confidence reconstruction is automatic. Uncertain boundaries remain separate and auditable.
- Translation requires a user confirmed canonical chapter plan and reading units with `translation_authority: true`.
- Preserve source provenance, reading order, images, anchors, links, and explicit user policies.
- Boundary changes must invalidate affected translations while allowing exact unchanged inputs to resume.

## Evidence

- Use new synthetic fixtures and newly created tasks for acceptance.
- Do not use existing jobs, translations, exports, cache files, or historical reports as a success baseline.
- Never read or copy `.env` files, provider credentials, real books, `output/`, `runs/`, or user task directories.

## Change discipline

- Cursor write tasks must use an isolated Git worktree from the pushed baseline.
- Keep PDF and EPUB implementation work in separate bounded commits until Codex integrates them.
- Do not weaken production gates to make a test pass.
- Do not add compatibility fallbacks for missing current artifacts.
- Keep user facing workflow simple; internal format stages must not become extra user actions.

## Verification

Run the tests relevant to the bounded change, then before integration run:

```bash
uv run pytest -q
cd frontend && pnpm test:run && pnpm build && pnpm lint
git diff --check
```
