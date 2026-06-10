# Phase A Review Handoff Acceptance Demo

This demo makes the Phase A -> Phase B routing visible and repeatable.

Run from the repository root:

```bash
bash examples/phase_a_review_handoff/run_demo.sh
```

The script verifies two supported routes:

1. English source enters Phase B directly as `source_only`.
2. English source plus an explicitly approved reviewed Chinese translation enters as `source_plus_translation`.

The command fails if either route violates the handoff contract.

After it succeeds, inspect:

```text
tmp/phase_a_review_handoff_demo/ACCEPTANCE_REPORT.md
tmp/phase_a_review_handoff_demo/english-source/phase_a_status.json
tmp/phase_a_review_handoff_demo/english-source/knowledge/manifest.json
tmp/phase_a_review_handoff_demo/reviewed-translation/phase_a_status.json
tmp/phase_a_review_handoff_demo/reviewed-translation/versions/reviewed-v1/version-manifest.json
tmp/phase_a_review_handoff_demo/reviewed-translation/knowledge/manifest.json
tmp/phase_a_review_handoff_demo/reviewed-translation/knowledge/semantic-units.json
```

Expected decisions:

| Route | Phase B mode | Reading language | Translation source |
| --- | --- | --- | --- |
| English source only | `source_only` | `en` | none |
| Approved reviewed translation | `source_plus_translation` | `zh-CN` | `reviewed_translation` |

The original `book.json` remains unchanged in both routes.
