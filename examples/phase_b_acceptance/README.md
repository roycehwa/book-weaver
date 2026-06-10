# Phase B Acceptance Demo

This demo tests how the currently implemented Phase B works, not only whether
an input is allowed to enter it.

Run from the repository root:

```bash
bash examples/phase_b_acceptance/run_demo.sh
```

The demo executes:

```text
BookIR
  -> knowledge build
  -> knowledge plan
  -> Reader Brief
  -> natural-language user feedback
  -> raw/aligned feedback
  -> updated plan
```

Inspect the result:

```text
tmp/phase_b_acceptance_demo/ACCEPTANCE_REPORT.md
tmp/phase_b_acceptance_demo/run/knowledge/reader-brief.md
tmp/phase_b_acceptance_demo/run/knowledge/reader-brief.html
tmp/phase_b_acceptance_demo/run/knowledge/feedback-template.md
tmp/phase_b_acceptance_demo/run/knowledge/plan-before-feedback.json
tmp/phase_b_acceptance_demo/run/knowledge/plan.json
tmp/phase_b_acceptance_demo/run/knowledge/user-review.json
tmp/phase_b_acceptance_demo/run/knowledge/reference-prior.json
tmp/phase_b_acceptance_demo/run/knowledge/feedback/raw/
tmp/phase_b_acceptance_demo/run/knowledge/feedback/aligned/
```

The script contains assertions and fails when:

- Reader Brief is not generated.
- Feedback is not preserved.
- The requested network correction is not applied.
- The chronology appendix is not preserved.
- The index is not skipped.
- Book-level insight is discarded because it cannot be locally aligned.
- External reference material is not kept as a weak prior.

Current scope:

- This validates Phase B1/B1.1.
- `Joint Draft`, accepted knowledge, and export belong to Phase B2/B3 and are
  intentionally not claimed as complete here.
