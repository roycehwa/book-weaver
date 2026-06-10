#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
BOOK_WEAVER="$ROOT/.venv/bin/book-weaver"
OUT="$ROOT/tmp/phase_a_review_handoff_demo"

rm -rf "$OUT"
mkdir -p "$OUT"

cd "$ROOT"

"$PYTHON" - <<'PY'
import json
from pathlib import Path

from pdf_translator.review import build_review_artifacts, write_review_artifacts

root = Path("tmp/phase_a_review_handoff_demo").resolve()
book = {
    "metadata": {"schema": "acceptance_demo_book_v1"},
    "chapters": [
        {
            "index": 1,
            "chapter_id": "ch-001-introduction",
            "title": "Introduction",
            "page_start": 1,
            "page_end": 1,
            "source_pages": [1],
            "markdown": "# Introduction\n\nThe book argues that institutions require public trust.\n",
            "toc": True,
        }
    ],
    "assets": [],
}


def write_common(run_dir: Path, *, translated: bool) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "book.json").write_text(
        json.dumps(book, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "book.md").write_text(
        "# Introduction\n\nThe book argues that institutions require public trust.\n",
        encoding="utf-8",
    )
    (run_dir / "chapter-report.json").write_text("{}", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_pdf": "/acceptance/example.epub",
                "source_language": "en",
                "target_language": "zh-CN" if translated else None,
                "translation": {"mode": "translated" if translated else "not_requested"},
                "files": {},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


source_run = root / "english-source"
write_common(source_run, translated=False)

review_run = root / "reviewed-translation"
write_common(review_run, translated=True)
(review_run / "translated.md").write_text(
    "# Introduction\n\n机器首次译文。\n",
    encoding="utf-8",
)
artifacts = build_review_artifacts(
    source_path=Path("/acceptance/example.epub"),
    target_language="zh-CN",
    book=book,
    translated_chapters=[
        {
            "index": 1,
            "chapter_id": "ch-001-introduction",
            "title": "Introduction",
            "page_start": 1,
            "page_end": 1,
            "source_pages": [1],
            "markdown": "# Introduction\n\n机器首次译文。\n",
            "toc": True,
        }
    ],
)
body_segment = next(
    segment
    for segment in artifacts["translated_segments"]["segments"]
    if "机器首次译文" in segment["translated_text"]
)
artifacts["review_state"]["decisions"][body_segment["segment_id"]] = {
    "status": "approved",
    "action": "manual_edit",
    "approved_text": "用户审阅并批准的中文译文。",
    "reviewer_comment": "Use this reviewed wording.",
}
write_review_artifacts(review_run, artifacts)
PY

"$BOOK_WEAVER" finalize "$OUT/english-source"
"$BOOK_WEAVER" knowledge build "$OUT/english-source"

"$BOOK_WEAVER" review status "$OUT/reviewed-translation"
"$BOOK_WEAVER" review export "$OUT/reviewed-translation" \
  --version reviewed-v1 \
  --target-lang zh-CN \
  --format epub \
  --approve
"$BOOK_WEAVER" finalize "$OUT/reviewed-translation"
"$BOOK_WEAVER" knowledge build "$OUT/reviewed-translation"

"$PYTHON" - <<'PY'
import json
from pathlib import Path

root = Path("tmp/phase_a_review_handoff_demo").resolve()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


source = root / "english-source"
reviewed = root / "reviewed-translation"
source_status = read(source / "phase_a_status.json")
source_knowledge = read(source / "knowledge" / "manifest.json")
reviewed_status = read(reviewed / "phase_a_status.json")
reviewed_version = read(reviewed / "versions" / "reviewed-v1" / "version-manifest.json")
reviewed_knowledge = read(reviewed / "knowledge" / "manifest.json")
reviewed_units = read(reviewed / "knowledge" / "semantic-units.json")
reviewed_texts = [item["text_translated"] for item in reviewed_units if item.get("text_translated")]

assert source_status["phase_b_input"]["mode"] == "source_only"
assert source_status["phase_b_input"]["reading_language"] == "en"
assert source_status["phase_b_input"]["translation_markdown"] is None
assert source_knowledge["language"]["mode"] == "monolingual_source"
assert source_knowledge["language"]["reading_language"] == "en"

assert reviewed_version["review"]["status"] == "approved"
assert reviewed_status["phase_b_input"]["mode"] == "source_plus_translation"
assert reviewed_status["phase_b_input"]["content_source"] == "reviewed_translation"
assert reviewed_status["phase_b_input"]["review_version"] == "reviewed-v1"
assert reviewed_knowledge["language"]["mode"] == "bilingual"
assert reviewed_knowledge["language"]["reading_language"] == "zh-CN"
assert "用户审阅并批准的中文译文。" in reviewed_texts
assert "机器首次译文。" not in reviewed_texts

report = f"""# Phase A Review Handoff Acceptance Report

## Result

PASS

## English Source-Only Route

- Phase B mode: `{source_status["phase_b_input"]["mode"]}`
- Content source: `{source_status["phase_b_input"]["content_source"]}`
- Source language: `{source_status["phase_b_input"]["source_language"]}`
- Reading language: `{source_status["phase_b_input"]["reading_language"]}`
- Knowledge mode: `{source_knowledge["language"]["mode"]}`
- Translation Markdown: `{source_status["phase_b_input"]["translation_markdown"]}`

## Approved Reviewed Translation Route

- Phase B mode: `{reviewed_status["phase_b_input"]["mode"]}`
- Content source: `{reviewed_status["phase_b_input"]["content_source"]}`
- Source language: `{reviewed_status["phase_b_input"]["source_language"]}`
- Reading language: `{reviewed_status["phase_b_input"]["reading_language"]}`
- Review status: `{reviewed_status["phase_b_input"]["review_status"]}`
- Review version: `{reviewed_status["phase_b_input"]["review_version"]}`
- Knowledge mode: `{reviewed_knowledge["language"]["mode"]}`
- Phase B translated text: `用户审阅并批准的中文译文。`

## Rejected Behavior

- The unreviewed machine text `机器首次译文。` was not selected for Phase B.
- The English source-only route did not require Chinese translation.
"""
(root / "ACCEPTANCE_REPORT.md").write_text(report, encoding="utf-8")
print(report)
PY

echo "Acceptance artifacts: $OUT"
