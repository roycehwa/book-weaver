from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _slug_title(title: str, index: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:48]
    return slug or f"ch-{index}"


def emit_wiki_outline_from_book(book: dict[str, Any], out_dir: Path) -> None:
    """Emit one Markdown stub per book chapter plus index.md (branch B placeholder until LLM extraction lands)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[str] = ["# Wiki outline", "", "| Chapter | Notes |", "| --- | --- |"]
    for ch in book.get("chapters") or []:
        idx = int(ch.get("index") or 0)
        title = str(ch.get("title") or f"Chapter {idx}")
        fname = f"{idx:03d}-{_slug_title(title, idx)}.md"
        body = (
            f"# {title}\n\n"
            "_(Knowledge extraction stub — wire chapter text + model here.)_\n\n"
            "## Meta\n\n"
            f"- chapter_index: {idx}\n"
        )
        (out_dir / fname).write_text(body, encoding="utf-8")
        rows.append(f"| [{title}]({fname}) | stub |")
    (out_dir / "index.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


def emit_mindmap_mermaid_from_book(book: dict[str, Any], output_path: Path) -> None:
    """Emit a single Mermaid document listing chapters under a root node (branch B placeholder)."""
    lines = ["flowchart TB", "  root[Book]"]
    for ch in book.get("chapters") or []:
        idx = int(ch.get("index") or 0)
        title = str(ch.get("title") or f"Chapter {idx}")
        label = title.replace('"', "'").replace("[", "(").replace("]", ")")[:100]
        lines.append(f'  root --> c{idx}["{label}"]')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("```mermaid\n" + "\n".join(lines) + "\n```\n", encoding="utf-8")


def load_book_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
