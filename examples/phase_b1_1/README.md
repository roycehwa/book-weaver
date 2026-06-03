# Phase B1.1 Feedback Demo

This demo shows the user-visible Phase B1.1 path:

1. Generate the engineering plan.
2. Render a reader-facing brief.
3. Submit natural-language feedback.
4. Verify that feedback is preserved, aligned, and applied back to `plan.json`.

Run it from the repository root:

```bash
rm -rf tmp/phase_b1_1_demo
mkdir -p tmp/phase_b1_1_demo
cp -R examples/phase_b1_1/run tmp/phase_b1_1_demo/run

.venv/bin/book-weaver knowledge plan tmp/phase_b1_1_demo/run
.venv/bin/book-weaver knowledge brief tmp/phase_b1_1_demo/run
.venv/bin/book-weaver knowledge feedback tmp/phase_b1_1_demo/run --input examples/phase_b1_1/feedback.md
```

Inspect the user-facing files:

```bash
ls tmp/phase_b1_1_demo/run/knowledge
ls tmp/phase_b1_1_demo/run/knowledge/feedback/raw
ls tmp/phase_b1_1_demo/run/knowledge/feedback/aligned
```

Check the structural effect of the feedback:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

root = Path("tmp/phase_b1_1_demo/run/knowledge")
plan = json.loads((root / "plan.json").read_text())
review = json.loads((root / "user-review.json").read_text())
aligned_path = next((root / "feedback" / "aligned").glob("*.json"))
aligned = json.loads(aligned_path.read_text())

print("primary_network_model:", plan["final_plan"]["primary_network_model"])
print("secondary_network_models:", plan["final_plan"]["secondary_network_models"])
print("preserve:", review["preserve_content_types"])
print("skip:", review["skip_content_types"])
print("feedback_summary:", aligned["summary"])
print("chapter_roles:")
for chapter in plan["final_plan"]["chapter_roles"]:
    print(f"- {chapter['title']}: {chapter['role']}")
PY
```

Expected signal:

- `primary_network_model` becomes `event_timeline_network`.
- `concept_network` is retained as a secondary model.
- `Appendix: Chronology` is preserved.
- `Index` is skipped.
- Raw and aligned feedback JSON files are written.
- `reader-brief.md`, `reader-brief.html`, and `feedback-template.md` are generated.
