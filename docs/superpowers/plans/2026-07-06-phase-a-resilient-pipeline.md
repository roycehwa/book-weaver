# Phase A Resilient Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Phase A glossary enforcement, MiniMax traffic handling, and job resume state stable and internally consistent.

**Architecture:** Keep `job.json` authoritative, classify glossary entries by enforcement level, and place provider traffic policy behind a small reusable controller. Preserve existing JSON compatibility while deriving transient API fields at read time.

**Tech Stack:** Python 3.11+, pytest, FastAPI backend, ThreadPoolExecutor, JSON artifacts.

---

### Task 1: Glossary candidate quality and enforcement levels

**Files:**
- Modify: `src/pdf_translator/glossary.py`
- Modify: `src/pdf_translator/glossary_extraction.py`
- Modify: `src/pdf_translator/translate.py`
- Test: `tests/test_glossary_extraction.py`
- Test: `tests/test_translate.py`

- [ ] Add failing tests proving candidate extraction has no minimum quota, generic title-case concepts are not people, automatically adopted suggestions are `preferred`, and preferred drift does not fail translation.
- [ ] Run the focused tests and confirm each fails for the intended reason.
- [ ] Remove chapter-driven candidate expansion and make the defensive ceiling independent of book size.
- [ ] Tighten person classification and reject low-evidence/title-like candidates.
- [ ] Persist `enforcement` on active entries and treat legacy user decisions as `hard`.
- [ ] Validate exact terms only for `hard` entries; retain preferred entries in prompts and reports.
- [ ] Resolve overlapping constraints by longest source match before chunk translation.
- [ ] Run focused glossary and translation tests.

### Task 2: MiniMax traffic and retry policy

**Files:**
- Create: `src/pdf_translator/provider_traffic.py`
- Modify: `src/pdf_translator/config.py`
- Modify: `src/pdf_translator/translate.py`
- Test: `tests/test_provider_traffic.py`
- Test: `tests/test_translate.py`

- [ ] Add failing tests for default concurrency 3, RPM pacing, shared overload cooldown, multiplicative decrease, and gradual recovery.
- [ ] Run the focused tests and confirm expected failures.
- [ ] Add environment-backed MiniMax RPM, TPM, maximum concurrency, and provider retry settings.
- [ ] Implement a thread-safe traffic controller with request admission, cooldown, and adaptive concurrency.
- [ ] Classify MiniMax overload/rate/connection errors separately from content-quality errors.
- [ ] Use provider retries without consuming content-quality retry budget.
- [ ] Run provider and translation tests.

### Task 3: Authoritative job state and progress counters

**Files:**
- Modify: `src/pdf_translator/job_control.py`
- Modify: `src/pdf_translator/jobs.py`
- Modify: `backend/job_service.py`
- Test: `tests/test_job_control.py`
- Test: `tests/test_jobs.py`
- Test: `backend/test_translation_resume.py`

- [ ] Add failing tests showing retries use per-chunk sets, terminal failures remain failed, finish clears transient counters, stale derived fields are removed, and resume routing is stage-specific.
- [ ] Run the focused tests and confirm expected failures.
- [ ] Replace additive running/retrying/failed counters with internal chunk-index sets serialized as counts.
- [ ] Persist structured last failure details and terminal chunk index.
- [ ] Strip persisted `translation_activity` and `translation_resume` before API enrichment.
- [ ] Centralize resume eligibility and route each failed stage to its legal checkpoint.
- [ ] Prevent translation resume from emitting intake/reconstruction work as newly executed.
- [ ] Run job-control and backend resume tests.

### Task 4: Integrated verification and planning alignment

**Files:**
- Modify if required: `docs/ROADMAP.md`
- Modify if required: `docs/IMPLEMENTATION_PLAN.md`
- Modify if required: `docs/PHASE_A_METHOD.md`

- [ ] Run all engine tests.
- [ ] Run all backend tests.
- [ ] Run frontend tests if API response shapes changed.
- [ ] Run formatting/static checks configured by the repository.
- [ ] Compare implemented behavior with the resilient-pipeline design.
- [ ] Compare current state with roadmap, implementation plan, and Phase A method.
- [ ] Document the current Phase A position and any remaining scope drift.
