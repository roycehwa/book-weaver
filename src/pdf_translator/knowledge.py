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


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _nonempty_blocks(markdown: str) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", markdown or "") if block.strip()]
    return blocks


def _unit_kind(block: str) -> str:
    if block.startswith("#"):
        return "heading"
    if re.match(r"!\[[^\]]*\]\([^)]+\)", block):
        return "image"
    if block.startswith("|") and "\n|" in block:
        return "table"
    if re.match(r"^(\-|\*|\d+\.)\s+", block):
        return "list"
    if block.startswith(">"):
        return "quote"
    return "paragraph"


def _chapter_id(chapter: dict[str, Any], fallback_index: int) -> str:
    existing = str(chapter.get("chapter_id") or "").strip()
    if existing:
        return existing
    title = str(chapter.get("title") or f"Chapter {fallback_index}")
    return f"ch-{fallback_index:03d}-{_slug_title(title, fallback_index)}"


def _split_markdown_by_heading_count(markdown: str, expected_count: int) -> list[str]:
    """Best-effort split for final translated markdown. Returns [] when alignment is unsafe."""
    if expected_count <= 0 or not markdown.strip():
        return []
    starts = [match.start() for match in re.finditer(r"(?m)^#\s+\S", markdown)]
    if len(starts) != expected_count:
        return []
    starts.append(len(markdown))
    return [markdown[starts[i] : starts[i + 1]].strip() + "\n" for i in range(expected_count)]


def _find_final_markdown(run_dir: Path) -> Path | None:
    for name in ("translated.polished.md", "translated.md", "book.md"):
        path = run_dir / name
        if path.exists():
            return path
    return None


def _build_chapters(book: dict[str, Any]) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    for fallback_index, chapter in enumerate(book.get("chapters") or [], start=1):
        index = int(chapter.get("index") or fallback_index)
        cid = _chapter_id(chapter, index)
        chapters.append(
            {
                "chapter_id": cid,
                "index": index,
                "title": str(chapter.get("title") or f"Chapter {index}"),
                "page_start": chapter.get("page_start"),
                "page_end": chapter.get("page_end"),
                "source_pages": list(chapter.get("source_pages") or []),
                "toc": bool(chapter.get("toc", True)),
                "translate": bool(chapter.get("translate", True)),
                "preserve_original": bool(chapter.get("preserve_original", False)),
                "source_internal_path": chapter.get("source_internal_path"),
                "unit_count": len(_nonempty_blocks(str(chapter.get("markdown") or ""))),
            }
        )
    return chapters


def _build_semantic_units(
    book: dict[str, Any],
    translated_chapter_markdown: list[str],
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    chapters = book.get("chapters") or []
    for fallback_index, chapter in enumerate(chapters, start=1):
        index = int(chapter.get("index") or fallback_index)
        cid = _chapter_id(chapter, index)
        original_blocks = _nonempty_blocks(str(chapter.get("markdown") or ""))
        translated_blocks: list[str] = []
        if len(translated_chapter_markdown) >= fallback_index:
            translated_blocks = _nonempty_blocks(translated_chapter_markdown[fallback_index - 1])
        align_translated = len(translated_blocks) == len(original_blocks)

        for unit_index, block in enumerate(original_blocks, start=1):
            unit_id = f"{cid}-u{unit_index:04d}"
            source_pages = list(chapter.get("source_pages") or [])
            units.append(
                {
                    "unit_id": unit_id,
                    "chapter_id": cid,
                    "chapter_index": index,
                    "unit_index": unit_index,
                    "kind": _unit_kind(block),
                    "text_original": block,
                    "text_translated": translated_blocks[unit_index - 1] if align_translated else None,
                    "translation_alignment": "block_index" if align_translated else "unavailable",
                    "source_pages": source_pages,
                    "page_start": chapter.get("page_start"),
                    "page_end": chapter.get("page_end"),
                }
            )
    return units


def _build_source_map(chapters: list[dict[str, Any]], units: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "chapters": {
            chapter["chapter_id"]: {
                "index": chapter["index"],
                "title": chapter["title"],
                "page_start": chapter["page_start"],
                "page_end": chapter["page_end"],
                "source_pages": chapter["source_pages"],
            }
            for chapter in chapters
        },
        "semantic_units": {
            unit["unit_id"]: {
                "chapter_id": unit["chapter_id"],
                "chapter_index": unit["chapter_index"],
                "unit_index": unit["unit_index"],
                "source_pages": unit["source_pages"],
                "page_start": unit["page_start"],
                "page_end": unit["page_end"],
            }
            for unit in units
        },
    }


def build_knowledge_package(run_dir: Path, out_dir: Path | None = None) -> dict[str, Path]:
    """Build the deterministic Phase B knowledge package from a completed Phase A run."""
    run_dir = run_dir.expanduser().resolve()
    book_path = run_dir / "book.json"
    if not book_path.exists():
        raise FileNotFoundError(f"Missing book.json: {book_path}")

    book = load_book_json(book_path)
    manifest = _read_json(run_dir / "manifest.json")
    knowledge_dir = (out_dir.expanduser().resolve() if out_dir else run_dir / "knowledge")
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    final_markdown_path = _find_final_markdown(run_dir)
    translated_chapter_markdown: list[str] = []
    translation_alignment = "missing"
    if final_markdown_path is not None and final_markdown_path.name != "book.md":
        translated_text = final_markdown_path.read_text(encoding="utf-8")
        translated_chapter_markdown = _split_markdown_by_heading_count(
            translated_text,
            len(book.get("chapters") or []),
        )
        translation_alignment = "chapter_heading_count" if translated_chapter_markdown else "unavailable"
    elif final_markdown_path is not None:
        translation_alignment = "same_language_or_original_only"

    chapters = _build_chapters(book)
    semantic_units = _build_semantic_units(book, translated_chapter_markdown)
    assets = list(book.get("assets") or [])
    source_map = _build_source_map(chapters, semantic_units)

    package_manifest = {
        "schema": "book_weaver_knowledge_manifest_v1",
        "run_dir": str(run_dir),
        "source": {
            "source_document": manifest.get("source_pdf"),
            "book_json": str(book_path),
            "book_markdown": str(run_dir / "book.md") if (run_dir / "book.md").exists() else None,
            "final_markdown": str(final_markdown_path) if final_markdown_path else None,
        },
        "language": {
            "source_language": manifest.get("source_language"),
            "target_language": manifest.get("target_language"),
            "translation_alignment": translation_alignment,
        },
        "counts": {
            "chapters": len(chapters),
            "semantic_units": len(semantic_units),
            "assets": len(assets),
        },
        "files": {
            "chapters": str(knowledge_dir / "chapters.json"),
            "semantic_units": str(knowledge_dir / "semantic-units.json"),
            "assets": str(knowledge_dir / "assets.json"),
            "source_map": str(knowledge_dir / "source-map.json"),
        },
    }

    paths = {
        "manifest": knowledge_dir / "manifest.json",
        "chapters": knowledge_dir / "chapters.json",
        "semantic_units": knowledge_dir / "semantic-units.json",
        "assets": knowledge_dir / "assets.json",
        "source_map": knowledge_dir / "source-map.json",
    }
    paths["manifest"].write_text(json.dumps(package_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["chapters"].write_text(json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["semantic_units"].write_text(json.dumps(semantic_units, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["assets"].write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["source_map"].write_text(json.dumps(source_map, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths
