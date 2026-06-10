#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOK_WEAVER="$ROOT/.venv/bin/book-weaver"
PYTHON="$ROOT/.venv/bin/python"
OUT="$ROOT/tmp/phase_b_acceptance_demo"
RUN="$OUT/run"
FEEDBACK="$ROOT/examples/phase_b1_1/feedback.md"

rm -rf "$OUT"
mkdir -p "$OUT"
cp -R "$ROOT/examples/phase_b1_1/run" "$RUN"

cd "$ROOT"

"$BOOK_WEAVER" knowledge build "$RUN"
"$BOOK_WEAVER" knowledge plan "$RUN" --metadata-prior none
cp "$RUN/knowledge/plan.json" "$RUN/knowledge/plan-before-feedback.json"
"$BOOK_WEAVER" knowledge brief "$RUN"
"$BOOK_WEAVER" knowledge feedback "$RUN" --input "$FEEDBACK"

"$PYTHON" - <<'PY'
import json
from pathlib import Path

root = Path("tmp/phase_b_acceptance_demo").resolve()
run = root / "run"
knowledge = run / "knowledge"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


before = read(knowledge / "plan-before-feedback.json")
after = read(knowledge / "plan.json")
review = read(knowledge / "user-review.json")
reference = read(knowledge / "reference-prior.json")
raw_path = next((knowledge / "feedback" / "raw").glob("*.json"))
aligned_path = next((knowledge / "feedback" / "aligned").glob("*.json"))
raw = read(raw_path)
aligned = read(aligned_path)

before_model = before["final_plan"]["primary_network_model"]
after_model = after["final_plan"]["primary_network_model"]
roles_before = {
    item["title"]: item["role"]
    for item in before["final_plan"]["chapter_roles"]
}
roles_after = {
    item["title"]: item["role"]
    for item in after["final_plan"]["chapter_roles"]
}
book_insights = [
    item
    for item in aligned["objects"]
    if item["kind"] == "book_level_user_insight"
]

assert (knowledge / "reader-brief.md").is_file()
assert (knowledge / "reader-brief.html").is_file()
assert (knowledge / "feedback-template.md").is_file()
assert raw["objects"]
assert aligned["summary"]["total"] == len(aligned["objects"])
assert before_model == "playbook_network"
assert after_model == "event_timeline_network"
assert "concept_network" in after["final_plan"]["secondary_network_models"]
assert roles_after["Appendix: Chronology"] == "preserve"
assert roles_after["Index"] == "skip"
assert "chronology" in review["preserve_content_types"]
assert "index" in review["skip_content_types"]
assert reference["references"]
assert book_insights
assert all(item["alignment"]["status"] == "unaligned" for item in book_insights)

before_rows = "\n".join(
    f"- `{title}`: `{role}`"
    for title, role in roles_before.items()
)
after_rows = "\n".join(
    f"- `{title}`: `{role}`"
    for title, role in roles_after.items()
)
insight_text = book_insights[0]["content"]
insight_status = book_insights[0]["alignment"]["status"]

report = f"""# Phase B Acceptance Report

## Result

PASS

## What The System Did Before User Feedback

- Built deterministic chapters and semantic units.
- Selected initial network model: `{before_model}`.
- Generated `reader-brief.md`, `reader-brief.html`, and `feedback-template.md`.

Chapter roles before feedback:

{before_rows}

## What The User Supplied

- Reading goal: historical change rather than a procedural checklist.
- Frame correction: `event_timeline_network + concept_network`.
- Preserve request: chronology appendix.
- Skip request: index and publisher pages.
- Chapter note and book-level insight.
- External reference material.

## What Phase B Changed

- Final network model: `{after_model}`.
- Secondary network models: `{after["final_plan"]["secondary_network_models"]}`.
- Parsed preserve types: `{review["preserve_content_types"]}`.
- Parsed skip types: `{review["skip_content_types"]}`.
- Feedback objects: `{aligned["summary"]["total"]}`.
- Locally aligned: `{aligned["summary"]["aligned"]}`.
- Unaligned but retained: `{aligned["summary"]["unaligned"]}`.

Chapter roles after feedback:

{after_rows}

## Book-Level Insight Retention

- Insight: `{insight_text}`
- Alignment status: `{insight_status}`
- Result: retained even without a paragraph-level match.

## External Reference Policy

- References retained: `{len(reference["references"])}`
- Policy: `{reference["policy"]}`

## Current Boundary

This proves Phase B1/B1.1 behavior:

`BookIR -> Plan -> Reader Brief -> User Feedback -> Alignment -> Updated Plan`

It does not claim that Phase B2 `Joint Draft` or Phase B3 accepted knowledge
and export are implemented.
"""
(root / "ACCEPTANCE_REPORT.md").write_text(report, encoding="utf-8")
print(report)
PY

echo "Acceptance artifacts: $OUT"
