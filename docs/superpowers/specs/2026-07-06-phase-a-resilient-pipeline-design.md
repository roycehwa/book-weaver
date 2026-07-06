# Phase A Resilient Pipeline Design

## Scope

This design covers Phase A only:

- glossary candidate extraction and enforcement;
- MiniMax request scheduling, rate limiting, and retry behavior;
- job state, progress, failure, and resume control.

It does not change Phase B contracts or implement book-specific exceptions.

## Goals

1. Surface a small, quality-driven glossary instead of filling a fixed quota.
2. Distinguish terminology preferences from terms that may fail translation.
3. Keep MiniMax traffic within documented account limits and react safely to overload.
4. Make job state, progress, errors, and resume decisions internally consistent.
5. Preserve completed translation chunks across process interruption and retry.

## Non-goals

- Automatically determining the user's MiniMax billing tier from the API key.
- Guaranteeing that every preferred term appears with one exact rendering.
- Replacing the existing BookIR, review, or rendering pipeline.
- Adding Phase B behavior.

## Architecture

The implementation is split into three bounded units.

### 1. Glossary policy

Glossary entries gain an enforcement level:

- `hard`: exact target wording is required. This level is reserved for explicit user confirmation or deterministic protected names.
- `preferred`: target wording is supplied to the model and checked for reporting, but drift does not fail a chunk.
- `informational`: context only; it does not participate in validation.

Legacy active entries without an enforcement level remain compatible. Entries explicitly decided by a user are interpreted as `hard`; entries adopted automatically at translation start are interpreted as `preferred`.

Candidate extraction becomes quality-driven:

- remove the candidate floor;
- remove chapter-count expansion from the limit;
- retain a defensive ceiling, but never treat it as a target;
- require evidence from more than one body chapter unless a candidate has strong domain or index evidence;
- reject likely bibliography titles, generic title-case phrases, and malformed OCR phrases;
- correct the person classifier so title-case concepts are not treated as names;
- suppress overlapping candidates using longest-match groups and reject conflicting hard targets before translation.

Only hard entries can raise `missing mandatory glossary terms`. Preferred drift is recorded as a quality warning.

### 2. MiniMax traffic controller

MiniMax uses a provider-specific traffic policy:

- conservative default concurrency: 3;
- configurable maximum concurrency, RPM, and TPM;
- request-start pacing derived from RPM;
- estimated token budget derived from input size and configured output limit;
- provider overload errors use a global cooldown shared by all workers;
- rate-limit/overload retries have a separate budget from content-quality retries;
- overload reduces the active concurrency window; sustained successful calls restore it gradually.

The controller does not infer a billing tier. Explicit environment configuration is authoritative. Defaults target stable behavior on the documented lower-concurrency plans.

Provider errors are classified separately:

- overload/rate/connection growth: retry after global backoff;
- timeout/network failure: transport retry;
- sensitive-content response: existing split behavior;
- glossary or incomplete translation: content-quality retry;
- authentication, invalid parameters, and exhausted quota: terminal failure.

### 3. Job state and progress

`job.json` is the authoritative persisted job snapshot. Other files have narrower roles:

- `workflow.json`: review workflow metadata only;
- `translation-job.json`: one translation invocation;
- `progress.json`: current invocation counters;
- translation events: append-only diagnostic history;
- worker lock: process liveness only.

Derived fields such as `translation_activity` and `translation_resume` are returned by the API but are not persisted in `job.json`. Loading a legacy snapshot removes stale derived fields before evaluation.

State transitions are explicit and stage-aware. Resume uses the failed stage:

- ingest/reconstruction failure resumes the intake path;
- translation failure resumes translation from valid cache;
- validation/pre-review failure resumes from the corresponding downstream checkpoint;
- polish failure resumes polish;
- non-retryable failures cannot resume.

Resume must not emit completed ingest/reconstruction stages again when reusing an existing run directory.

Progress counters describe chunks, not attempts:

- `running_chunks` is the set size of chunks with an active attempt;
- `retrying_chunks` is the set size of chunks awaiting another attempt;
- `failed_chunks` is the set size of terminally failed chunks;
- attempts are counted independently;
- finishing a job clears running and retrying sets;
- a failed job records the terminal chunk and the last structured error.

## Data flow

1. Intake produces BookIR and glossary candidates.
2. User decisions update active glossary entries and enforcement levels.
3. Translation start adopts unresolved machine suggestions as `preferred`.
4. Chunk construction selects matching glossary entries and resolves overlaps.
5. The MiniMax controller admits requests under configured traffic limits.
6. Provider and content failures follow separate retry budgets.
7. Successful chunks are cached atomically.
8. Job progress receives normalized chunk-state updates.
9. A terminal failure writes one structured failure containing stage, category, provider code, chunk index, and retryability.
10. Resume derives its decision from the authoritative failure and available cache.

## Compatibility

- Existing glossary JSON remains readable.
- Existing cached chunks remain valid when their effective prompt constraints have not changed.
- Changing a term from preferred to hard changes the prompt hash and invalidates only affected chunks.
- Existing job directories remain listable; stale derived resume fields are ignored.
- CLI aliases remain unchanged.

## Testing

Tests are required at four levels:

1. Glossary unit tests:
   - no candidate floor;
   - concepts are not classified as people;
   - bibliography/title noise is rejected;
   - automatic suggestions become preferred;
   - only hard terms fail exact validation;
   - overlapping constraints resolve deterministically.
2. Traffic-controller unit tests:
   - RPM pacing;
   - overload global cooldown;
   - multiplicative decrease and gradual recovery;
   - provider retry budget remains independent of quality retries.
3. Job-control unit tests:
   - progress counters cannot remain retrying after success or finish;
   - terminal failures increment failed chunks;
   - stale derived fields are removed;
   - each failed stage maps to one legal resume path;
   - translation resume does not replay intake stages.
4. Regression tests using recorded structures from the recent Phase A run:
   - a chunk with preferred flexible terminology completes;
   - a hard user-confirmed term is still enforced;
   - an overload followed by recovery preserves successful cache;
   - API resume status agrees with persisted failure state.

## Acceptance criteria

- Glossary candidate count is determined by quality and may be below 60.
- Machine-generated suggestions cannot independently cause a terminal glossary failure.
- Default MiniMax concurrency is 3 and can be configured explicitly.
- Overload backoff is global and separate from translation-quality retries.
- `job.json`, API resume output, progress, and events agree on the terminal stage and retryability.
- Resuming a failed translation reuses cached chunks and does not report ingest/reconstruction as newly executed.
- Existing Phase A engine and backend test suites pass.
