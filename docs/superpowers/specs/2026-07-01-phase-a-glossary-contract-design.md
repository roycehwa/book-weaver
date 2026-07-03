# Phase A Glossary Contract Design

## Goal

Make glossary confirmation an enforceable translation contract instead of a source of user cleanup work.

## Rules

1. A new test round starts without inherited diagnostics. Sensitive-content labels, provider routing decisions, failure classifications, machine suggestions, and review findings are recomputed from current input.
2. Explicit user decisions remain durable business data. Confirmed glossary entries and confirmed chapter boundaries are not diagnostics and may persist unless the user chooses a full reset.
3. Glossary source terms use a canonical comparison key that normalizes whitespace, Unicode apostrophes, and trailing punctuation. Display text remains human-readable.
4. Translation receives only confirmed active terms relevant to the current chunk and records which constraints were injected.
5. Pre-review may report glossary drift only for a constraint recorded as injected into that segment or chunk.
6. A model output that violates an injected glossary constraint is a system repair item. It is not counted as a user translation mistake and should be offered for automatic retranslation.
7. Confirmed glossary and chapter sections collapse by default in the job workspace, while remaining expandable for revisions.

## Existing Job Migration

Existing artifacts are migrated non-destructively:

- duplicate active terms are merged by canonical key;
- explicit user decisions win over machine values;
- review findings are regenerated from current artifacts;
- findings without injection provenance are removed from the user queue;
- the full translation is not restarted automatically.

## Verification

- unit tests for punctuation variants and decision precedence;
- unit tests proving round diagnostics do not survive reset;
- translation tests proving confirmed terms are injected and provenance is recorded;
- review tests proving untracked terms cannot create user-facing drift;
- frontend tests proving completed sections default to collapsed.
