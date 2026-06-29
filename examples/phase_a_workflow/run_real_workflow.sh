#!/usr/bin/env bash
# Phase A workflow — real book-inbox end-to-end test (no mock).
# Stages: intake → glossary finalize → translate (minimax) → pre_review evidence
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

BOOK="${BOOK:-/Users/huachunmu/book-inbox/Good Company _ Economic Policy after Shareholder Primacy -- Lenore Palladino -- 2024 -- University of Chicago Press.epub}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BASE_OUT="${BASE_OUT:-$ROOT/tmp/phase-a-workflow-runs}"
RUN_PARENT="$BASE_OUT/run-$STAMP"
REPORT="$BASE_OUT/STAGE_REPORT-$STAMP.md"
CLI=".venv/bin/python -m pdf_translator.cli"
TRANSLATOR="${TRANSLATOR:-minimax}"
TARGET_LANG="${TARGET_LANG:-zh-CN}"
export MINIMAX_BASE_URL="${MINIMAX_BASE_URL:-https://api.minimaxi.com/anthropic/v1/messages}"

mkdir -p "$BASE_OUT"
exec > >(tee -a "$REPORT") 2>&1

section() {
  echo ""
  echo "## $1"
  echo ""
  echo "Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo '```'
}

end_section() {
  echo '```'
}

{
  echo "# Phase A 真实工作流测试报告"
  echo ""
  echo "- 引擎: \`$ROOT\`"
  echo "- 源书: \`$BOOK\`"
  echo "- 译器: \`$TRANSLATOR\`（真实 API，非 mock）"
  echo "- 输出根目录: \`$RUN_PARENT\`"
  echo ""
  echo "顺序: **intake → glossary 定稿 → translate → pre_review**"
} > "$REPORT"

section "阶段 1 — Intake（BookIR + 术语候选）"
$CLI intake "$BOOK" --output-dir "$RUN_PARENT" --profile book
RUN_DIR="$(find "$RUN_PARENT" -mindepth 1 -maxdepth 1 -type d | head -1)"
echo "RUN_DIR=$RUN_DIR"
$CLI glossary status "$RUN_DIR"
ls -la "$RUN_DIR/glossary/" 2>/dev/null || true
ls -la "$RUN_DIR/workflow.json" 2>/dev/null || true
python3 -c "
import json
from pathlib import Path
run = Path('$RUN_DIR')
wf = json.loads((run/'workflow.json').read_text())
c = json.loads((run/'glossary/candidates.json').read_text())
print('workflow_stage:', wf.get('stage'))
print('candidate_count:', len(c.get('candidates', [])))
print('first_5_candidates:', [x['source'] for x in c.get('candidates', [])[:5]])
"
end_section

section "阶段 2 — Glossary 定稿（翻译前）"
# 从候选里挑本书核心术语并写入 active.json
$CLI glossary apply "$RUN_DIR" --source "Shareholder Primacy" --target "股东至上" --type name_or_key_term --status active
$CLI glossary apply "$RUN_DIR" --source "Good Company" --target "好公司" --type name_or_key_term --status active
$CLI glossary apply "$RUN_DIR" --source "Adam Smith" --target "亚当·斯密" --type name_or_key_term --status active
$CLI glossary ready "$RUN_DIR"
$CLI glossary status "$RUN_DIR"
echo "--- active.json ---"
python3 -c "
import json
from pathlib import Path
active = json.loads(Path('$RUN_DIR/glossary/active.json').read_text())
for e in active.get('entries', []):
    print(f\"  {e['source']} -> {e.get('target')}\")
"
echo "--- decisions.jsonl ---"
tail -5 "$RUN_DIR/glossary/decisions.jsonl"
end_section

section "阶段 3 — Translate（真实 ${TRANSLATOR}，术语已注入）"
$CLI translate --run-dir "$RUN_DIR" --target-lang "$TARGET_LANG" --translator "$TRANSLATOR" --format epub
ls -lh "$RUN_DIR"/*.epub 2>/dev/null || true
ls -la "$RUN_DIR/jobs/" 2>/dev/null || true
python3 -c "
import json
from pathlib import Path
run = Path('$RUN_DIR')
progress = json.loads((run/'jobs/progress.json').read_text())
print('translation_status:', progress.get('status'))
print('chunks:', progress.get('completed_chunks'), '/', progress.get('total_chunks'))
print('failed:', progress.get('failed_chunks'))
"
end_section

section "阶段 4 — 机器预审（pre_review + 可疑段）"
$CLI review status "$RUN_DIR"
python3 -c "
import json
from pathlib import Path
run = Path('$RUN_DIR')
pre = json.loads((run/'pre_review.json').read_text())
items = json.loads((run/'review_items.json').read_text())
print('flagged_segments:', pre.get('flagged_segments'))
print('clean_segments:', pre.get('clean_segments'))
print('issue_counts:', pre.get('issue_counts'))
sample = items.get('items', [])[:3]
for item in sample:
    print(' -', item.get('issue_type'), item.get('chapter_title'), item.get('segment_id'))
"
end_section

section "阶段 5 — 译文抽样（验证非英文原文）"
python3 -c "
from pathlib import Path
import re
text = Path('$RUN_DIR/translated.md').read_text(encoding='utf-8')[:2500]
cjk = len(re.findall(r'[\u4e00-\u9fff]', text))
ascii_letters = len(re.findall(r'[A-Za-z]', text))
print('translated.md sample (first 400 chars):')
print(text[:400])
print('---')
print('cjk_chars_in_sample:', cjk)
print('ascii_letters_in_sample:', ascii_letters)
"
end_section

{
  echo ""
  echo "---"
  echo ""
  echo "**完整报告路径:** \`$REPORT\`"
  echo ""
  echo "**运行目录:** \`$RUN_DIR\`"
  echo ""
  echo "打开 Finder:"
  echo "\`open '$RUN_DIR'\`"
} >> "$REPORT"

echo ""
echo "REPORT=$REPORT"
echo "RUN_DIR=$RUN_DIR"
open "$RUN_DIR" 2>/dev/null || true
open "$REPORT" 2>/dev/null || true
