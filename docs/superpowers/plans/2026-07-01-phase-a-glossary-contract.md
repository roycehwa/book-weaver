# Phase A Glossary Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce confirmed glossary terms during translation, eliminate punctuation duplicates, isolate test rounds, and reduce completed workspace sections.

**Architecture:** Canonicalize glossary identity at extraction and decision boundaries. Persist per-chunk glossary constraints as translation provenance, then limit pre-review drift checks to those constraints. Keep UI folding based on completed workflow state.

**Tech Stack:** Python, pytest, React, TypeScript, Vitest.

---

### Task 1: Test-round isolation

**Files:**
- Modify: `src/pdf_translator/glossary.py`
- Test: `tests/test_glossary_reset_review.py`

- [x] Add a failing test proving reset removes all machine diagnostics and routing annotations while preserving only explicit user decisions.
- [x] Run `pytest tests/test_glossary_reset_review.py -q` and confirm the new assertion fails.
- [x] Implement one reset helper that deletes derived review/repair/job diagnosis files and strips round annotations.
- [x] Run the focused test and confirm it passes.

### Task 2: Canonical glossary identity

**Files:**
- Modify: `src/pdf_translator/glossary_extraction.py`
- Modify: `src/pdf_translator/glossary.py`
- Test: `tests/test_glossary_extraction.py`
- Test: `tests/test_glossary.py`

- [x] Add failing tests for `Soviet Union`, `Soviet Union'`, and Unicode apostrophe variants collapsing to one candidate and one active decision.
- [x] Run the focused tests and confirm failures.
- [x] Add a canonical key that normalizes Unicode apostrophes, whitespace, and trailing punctuation; merge evidence and occurrence data by that key.
- [x] Preserve the strongest explicit user decision when active entries merge.
- [x] Run focused glossary tests.

### Task 3: Translation constraint provenance and review ownership

**Files:**
- Modify: `src/pdf_translator/translate.py`
- Modify: `src/pdf_translator/review.py`
- Test: `tests/test_translate.py`
- Test: `tests/test_review.py`

- [x] Add a failing translation test proving relevant active terms are injected and recorded per chunk.
- [x] Add a failing review test proving glossary drift is ignored without injection provenance and classified as system repair when provenance exists.
- [x] Run focused tests and confirm failures.
- [x] Persist injected source/target pairs beside translation job artifacts.
- [x] Restrict drift detection to persisted constraints and mark violations `system_repair`.
- [x] Run focused translation and review tests.

### Task 4: Completed-section folding

**Files:**
- Modify: `/Users/huachunmu/WorkBench/bookmate-review/frontend/src/components/JobDetail.tsx`
- Create: `/Users/huachunmu/WorkBench/bookmate-review/frontend/src/components/JobDetail.test.tsx`

- [x] Add failing UI tests proving confirmed glossary and chapter sections are collapsed and incomplete sections remain open.
- [x] Run the focused Vitest file and confirm failures.
- [x] Wrap each section in a controlled `details` element whose initial state follows workflow completion.
- [x] Run focused frontend tests and build.

### Task 5: Existing Making Mao's Steelworks migration

**Files:**
- Modify artifacts only through the production migration/reset APIs.

- [x] Back up the job's glossary and review metadata inside its job directory.
- [x] Run canonical migration and regenerate pre-review without inheriting prior diagnostics.
- [x] Verify duplicate variants are merged and unproven glossary drift is absent from the user queue.
- [x] Open the job workspace and review console to verify navigation and folding.

### Task 6: Full verification

- [x] Run all focused Phase A glossary, translation, review, and reset tests.
- [x] Run BookMate backend contract tests.
- [x] Run BookMate frontend tests and production build.
- [x] Inspect both git diffs and confirm no secrets, caches, generated builds, or `.superpowers/` state are included.
