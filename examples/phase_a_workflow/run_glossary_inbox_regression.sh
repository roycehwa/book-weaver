#!/usr/bin/env bash
# Run glossary intake + profile detection on inbox books and print a short report.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INBOX="${BOOK_INBOX:-$HOME/book-inbox}"
OUT="${1:-$ROOT/tmp/glossary-regression-$(date +%Y%m%d-%H%M%S)}"
PY="$ROOT/.venv/bin/python"
CLI=( "$PY" -m pdf_translator.cli )

mkdir -p "$OUT"

if [[ $# -ge 2 ]]; then
  shift
  BOOKS=("$@")
else
  BOOKS=(
    "$INBOX/Good Company _ Economic Policy after Shareholder Primacy -- Lenore Palladino -- 2024 -- University of Chicago Press.epub"
    "$INBOX/China After Mao _ The Rise of a Superpower -- Frank Dikötter -- Bloomsbury USA (Trade), New York, 2022 -- Bloomsbury Publishing USA.epub"
    "$INBOX/Modernising Protestantism_ A Cultural History of the Dutch -- Joke Spaans -- 10_5117_9789048567249, 1, 2025 -- Amsterdam University Press.epub"
  )
fi

REPORT="$OUT/REGRESSION.md"
{
  echo "# Glossary inbox regression"
  echo
  echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
} > "$REPORT"

index=0
for book in "${BOOKS[@]}"; do
  if [[ ! -f "$book" ]]; then
    echo "SKIP missing: $book" | tee -a "$REPORT"
    continue
  fi
  index=$((index + 1))
  book_out="$OUT/book$index"
  echo "=== [$index] $(basename "$book") ==="
  "${CLI[@]}" intake "$book" --output-dir "$book_out" >/dev/null
  run_dir="$(find "$book_out" -mindepth 1 -maxdepth 1 -type d | head -1)"
  "${CLI[@]}" glossary detect "$run_dir" || true
  "$PY" - <<PY
import json
from pathlib import Path
run = Path("$run_dir")
policy = json.loads((run/"glossary/extraction-policy.json").read_text())
cands = json.loads((run/"glossary/candidates.json").read_text())["candidates"]
print("profile:", policy.get("glossary_profile_label"), policy.get("glossary_profile"))
print("confidence:", policy.get("glossary_profile_confidence"))
print("top10:", ", ".join(c["source"] for c in cands[:10]))
PY
  {
    echo "## $(basename "$book")"
    echo
    echo "- Run dir: \`$run_dir\`"
    "$PY" - <<PY
import json
from pathlib import Path
run = Path("$run_dir")
policy = json.loads((run/"glossary/extraction-policy.json").read_text())
cands = json.loads((run/"glossary/candidates.json").read_text())["candidates"]
print(f"- Profile: {policy.get('glossary_profile_label')} ({policy.get('glossary_profile')})")
print(f"- Confidence: {policy.get('glossary_profile_confidence')}")
print("- Top 10:")
for c in cands[:10]:
    print(f"  - {c['source']} (score={c['score']})")
PY
    echo
  } >> "$REPORT"
done

echo "Report: $REPORT"
