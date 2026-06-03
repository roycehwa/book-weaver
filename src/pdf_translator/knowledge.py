from __future__ import annotations

import json
import hashlib
import html
import re
from pathlib import Path
from typing import Any, Callable

import requests


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


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _nonempty_blocks(markdown: str) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", markdown or "") if block.strip()]
    return blocks


def _text_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


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
    *,
    has_chapter_translation: bool,
    package_mode: str,
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
        if align_translated:
            alignment = "block_index"
        elif translated_blocks:
            alignment = "chapter_only"
        elif has_chapter_translation:
            alignment = "unavailable"
        else:
            alignment = "original_only"

        for unit_index, block in enumerate(original_blocks, start=1):
            unit_id = f"{cid}-u{unit_index:04d}"
            source_pages = list(chapter.get("source_pages") or [])
            translated_block = translated_blocks[unit_index - 1] if align_translated else None
            units.append(
                {
                    "unit_id": unit_id,
                    "schema": "book_weaver_semantic_unit_v2",
                    "chapter_id": cid,
                    "chapter_index": index,
                    "unit_index": unit_index,
                    "kind": _unit_kind(block),
                    "language_mode": package_mode,
                    "text_original": block,
                    "text_original_hash": _text_hash(block),
                    "text_translated": translated_block,
                    "text_translated_hash": _text_hash(translated_block) if translated_block else None,
                    "translation_alignment": alignment,
                    "translation_chapter_available": bool(translated_blocks),
                    "source_pages": source_pages,
                    "page_start": chapter.get("page_start"),
                    "page_end": chapter.get("page_end"),
                }
            )
    return units


def _build_bilingual_input(
    book: dict[str, Any],
    translated_chapter_markdown: list[str],
    *,
    run_dir: Path,
    final_markdown_path: Path | None,
    source_language: str | None,
    target_language: str | None,
    package_mode: str,
    chapter_split_alignment: str,
) -> dict[str, Any]:
    chapters: list[dict[str, Any]] = []
    for fallback_index, chapter in enumerate(book.get("chapters") or [], start=1):
        index = int(chapter.get("index") or fallback_index)
        cid = _chapter_id(chapter, index)
        original_markdown = str(chapter.get("markdown") or "").strip()
        translated_markdown = ""
        if len(translated_chapter_markdown) >= fallback_index:
            translated_markdown = translated_chapter_markdown[fallback_index - 1].strip()
        original_blocks = _nonempty_blocks(original_markdown)
        translated_blocks = _nonempty_blocks(translated_markdown)
        if translated_blocks and len(translated_blocks) == len(original_blocks):
            unit_alignment = "block_index"
        elif translated_blocks:
            unit_alignment = "chapter_only"
        elif package_mode == "monolingual_original":
            unit_alignment = "original_only"
        else:
            unit_alignment = "unavailable"
        chapters.append(
            {
                "chapter_id": cid,
                "chapter_index": index,
                "title": str(chapter.get("title") or f"Chapter {index}"),
                "source_pages": list(chapter.get("source_pages") or []),
                "page_start": chapter.get("page_start"),
                "page_end": chapter.get("page_end"),
                "source_internal_path": chapter.get("source_internal_path"),
                "original_markdown": original_markdown,
                "original_hash": _text_hash(original_markdown),
                "translated_markdown": translated_markdown or None,
                "translated_hash": _text_hash(translated_markdown) if translated_markdown else None,
                "unit_alignment": unit_alignment,
                "original_block_count": len(original_blocks),
                "translated_block_count": len(translated_blocks),
            }
        )

    return {
        "schema": "book_weaver_bilingual_input_v1",
        "contract": {
            "source_of_truth": "book.json",
            "chapter_anchor": "chapter_id",
            "unit_alignment_rule": (
                "Only set semantic_units[].text_translated when original and translated block counts match inside "
                "the same chapter. Otherwise keep chapter-level translated_markdown but do not fabricate unit pairs."
            ),
            "provenance_rule": "Every downstream knowledge node must cite chapter_id and preferably unit_id/source_pages.",
        },
        "run_dir": str(run_dir),
        "mode": package_mode,
        "source_language": source_language,
        "target_language": target_language,
        "paths": {
            "book_json": str(run_dir / "book.json"),
            "book_markdown": str(run_dir / "book.md") if (run_dir / "book.md").exists() else None,
            "final_markdown": str(final_markdown_path) if final_markdown_path else None,
        },
        "alignment": {
            "chapter_split": chapter_split_alignment,
            "unit_levels": {
                "block_index": sum(1 for chapter in chapters if chapter["unit_alignment"] == "block_index"),
                "chapter_only": sum(1 for chapter in chapters if chapter["unit_alignment"] == "chapter_only"),
                "original_only": sum(1 for chapter in chapters if chapter["unit_alignment"] == "original_only"),
                "unavailable": sum(1 for chapter in chapters if chapter["unit_alignment"] == "unavailable"),
            },
        },
        "chapters": chapters,
    }


def _sample_readable_blocks(markdown: str, *, max_blocks: int = 2, max_chars: int = 260) -> str:
    samples: list[str] = []
    for block in _nonempty_blocks(markdown):
        if block.startswith("#") or block.startswith("!["):
            continue
        compact = _compact_space(block)
        if len(compact) < 6:
            continue
        samples.append(compact[:max_chars] + ("..." if len(compact) > max_chars else ""))
        if len(samples) >= max_blocks:
            break
    return " / ".join(samples)


def _escape_md_table_cell(text: object) -> str:
    return str(text or "").replace("|", "/").replace("\n", " ").strip()


def _render_bilingual_input_markdown(bilingual_input: dict[str, Any]) -> str:
    alignment = bilingual_input.get("alignment", {})
    unit_levels = alignment.get("unit_levels", {}) if isinstance(alignment, dict) else {}
    lines = [
        "# Bilingual Knowledge Input",
        "",
        f"- mode: `{bilingual_input.get('mode')}`",
        f"- source_language: `{bilingual_input.get('source_language')}`",
        f"- target_language: `{bilingual_input.get('target_language')}`",
        f"- chapter_split: `{alignment.get('chapter_split') if isinstance(alignment, dict) else None}`",
        f"- block_index chapters: `{unit_levels.get('block_index', 0)}`",
        f"- chapter_only chapters: `{unit_levels.get('chapter_only', 0)}`",
        f"- original_only chapters: `{unit_levels.get('original_only', 0)}`",
        f"- unavailable chapters: `{unit_levels.get('unavailable', 0)}`",
        "",
        "| # | Chapter | Pages | Alignment | Original Blocks | Translated Blocks |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for chapter in bilingual_input.get("chapters") or []:
        pages = chapter.get("source_pages") or []
        page_text = ", ".join(str(page) for page in pages[:8])
        if len(pages) > 8:
            page_text += ", ..."
        lines.append(
            "| "
            f"{chapter.get('chapter_index')} | "
            f"{str(chapter.get('title') or '').replace('|', '/')} | "
            f"{page_text} | "
            f"`{chapter.get('unit_alignment')}` | "
            f"{chapter.get('original_block_count')} | "
            f"{chapter.get('translated_block_count')} |"
        )
    lines.append("")
    lines.append("## Content Samples")
    lines.append("")
    lines.append("| # | Chapter | Alignment | Original sample | Translated sample |")
    lines.append("| --- | --- | --- | --- | --- |")
    for chapter in bilingual_input.get("chapters") or []:
        original_sample = _sample_readable_blocks(str(chapter.get("original_markdown") or ""))
        translated_sample = _sample_readable_blocks(str(chapter.get("translated_markdown") or ""))
        if not translated_sample:
            translated_sample = "(none)"
        lines.append(
            "| "
            f"{chapter.get('chapter_index')} | "
            f"{_escape_md_table_cell(chapter.get('title'))} | "
            f"`{chapter.get('unit_alignment')}` | "
            f"{_escape_md_table_cell(original_sample)} | "
            f"{_escape_md_table_cell(translated_sample)} |"
        )
    lines.append("")
    lines.append("## Rule")
    lines.append("")
    lines.append(
        "Unit-level translation is accepted only when original and translated block counts match inside the same chapter. "
        "When alignment is `chapter_only`, downstream extraction may use translated chapter text as reading context, "
        "but must cite original units as evidence."
    )
    return "\n".join(lines) + "\n"


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
    """Build the deterministic Phase B knowledge package from a completed intake/translate run."""
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
    translation_mode = str(manifest.get("translation", {}).get("mode") or "")
    package_mode = "monolingual_original"
    translation_alignment = "missing"
    if (
        final_markdown_path is not None
        and final_markdown_path.name != "book.md"
        and translation_mode != "skipped_same_language"
    ):
        package_mode = "bilingual"
        translated_text = final_markdown_path.read_text(encoding="utf-8")
        translated_chapter_markdown = _split_markdown_by_heading_count(
            translated_text,
            len(book.get("chapters") or []),
        )
        translation_alignment = "chapter_heading_count" if translated_chapter_markdown else "unavailable"
    elif final_markdown_path is not None:
        translation_alignment = "original_only"

    chapters = _build_chapters(book)
    semantic_units = _build_semantic_units(
        book,
        translated_chapter_markdown,
        has_chapter_translation=package_mode == "bilingual",
        package_mode=package_mode,
    )
    bilingual_input = _build_bilingual_input(
        book,
        translated_chapter_markdown,
        run_dir=run_dir,
        final_markdown_path=final_markdown_path,
        source_language=manifest.get("source_language"),
        target_language=manifest.get("target_language"),
        package_mode=package_mode,
        chapter_split_alignment=translation_alignment,
    )
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
            "mode": package_mode,
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
            "bilingual_input": str(knowledge_dir / "bilingual-input.json"),
            "bilingual_input_markdown": str(knowledge_dir / "bilingual-input.md"),
            "assets": str(knowledge_dir / "assets.json"),
            "source_map": str(knowledge_dir / "source-map.json"),
        },
    }

    paths = {
        "manifest": knowledge_dir / "manifest.json",
        "chapters": knowledge_dir / "chapters.json",
        "semantic_units": knowledge_dir / "semantic-units.json",
        "bilingual_input": knowledge_dir / "bilingual-input.json",
        "bilingual_input_markdown": knowledge_dir / "bilingual-input.md",
        "assets": knowledge_dir / "assets.json",
        "source_map": knowledge_dir / "source-map.json",
    }
    paths["manifest"].write_text(json.dumps(package_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["chapters"].write_text(json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["semantic_units"].write_text(json.dumps(semantic_units, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["bilingual_input"].write_text(json.dumps(bilingual_input, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["bilingual_input_markdown"].write_text(_render_bilingual_input_markdown(bilingual_input), encoding="utf-8")
    paths["assets"].write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["source_map"].write_text(json.dumps(source_map, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths


PROFILE_DEFINITIONS: dict[str, dict[str, list[str]]] = {
    "argumentative": {
        "keywords": [
            "argument",
            "claim",
            "concept",
            "theory",
            "critique",
            "evidence",
            "debate",
            "interpretation",
            "analysis",
            "framework",
            "therefore",
            "because",
            "however",
            "论点",
            "概念",
            "理论",
            "批判",
            "证据",
            "解释",
            "分析",
            "框架",
            "因此",
            "然而",
        ],
        "outputs": ["argument_map", "concept_wiki", "claim_evidence_index", "theory_relation_index"],
        "objects": ["concept", "claim", "evidence", "counterclaim", "theory_relation"],
        "do_not_extract": ["timeline_as_primary_output", "formula_graph", "character_network_as_primary_output"],
    },
    "textbook": {
        "keywords": [
            "chapter objectives",
            "learning objective",
            "exercise",
            "review",
            "definition",
            "example",
            "key terms",
            "summary",
            "practice",
            "problem",
            "学习目标",
            "练习",
            "复习",
            "定义",
            "例题",
            "术语",
            "总结",
        ],
        "outputs": ["learning_path", "term_glossary", "concept_map", "review_cards"],
        "objects": ["learning_objective", "term", "concept", "prerequisite", "example", "procedure"],
        "do_not_extract": ["author_argument_network", "open_relation_graph"],
    },
    "historical": {
        "keywords": [
            "history",
            "war",
            "empire",
            "revolution",
            "century",
            "campaign",
            "treaty",
            "government",
            "political",
            "biography",
            "historical",
            "历史",
            "战争",
            "帝国",
            "革命",
            "世纪",
            "条约",
            "政府",
            "政治",
            "传记",
        ],
        "outputs": ["timeline", "actor_index", "event_graph", "place_index"],
        "objects": ["actor", "event", "time", "place", "causal_link", "temporal_link"],
        "do_not_extract": ["abstract_concept_graph_as_primary_output"],
    },
    "practical": {
        "keywords": [
            "how to",
            "strategy",
            "practice",
            "principle",
            "step",
            "checklist",
            "playbook",
            "case study",
            "guide",
            "method",
            "tool",
            "步骤",
            "原则",
            "清单",
            "案例",
            "指南",
            "方法",
            "工具",
        ],
        "outputs": ["playbook", "checklist", "workflow", "case_library", "decision_tree"],
        "objects": ["framework", "principle", "step", "rule", "case", "action", "anti_pattern"],
        "do_not_extract": ["large_open_relation_graph"],
    },
    "narrative": {
        "keywords": [
            "novel",
            "story",
            "scene",
            "character",
            "dialogue",
            "narrator",
            "plot",
            "theme",
            "fiction",
            "memoir",
            "小说",
            "故事",
            "场景",
            "人物",
            "对话",
            "叙述",
            "情节",
            "主题",
        ],
        "outputs": ["plot_flow", "character_network", "scene_index", "theme_index"],
        "objects": ["character", "scene", "event", "relationship_state", "conflict", "theme", "motif"],
        "do_not_extract": ["claim_evidence_argument_model"],
    },
    "technical_lite": {
        "keywords": [
            "formula",
            "theorem",
            "proof",
            "algorithm",
            "equation",
            "table",
            "figure",
            "symbol",
            "model",
            "data",
            "公式",
            "定理",
            "证明",
            "算法",
            "方程",
            "图表",
            "符号",
            "模型",
            "数据",
        ],
        "outputs": ["chapter_index", "definition_index", "formula_table_figure_index", "manual_review_queue"],
        "objects": ["definition", "theorem", "formula_placeholder", "table", "figure", "procedure", "symbol"],
        "do_not_extract": ["automatic_formula_semantics", "proof_dependency_graph"],
    },
}


NETWORK_MODELS: dict[str, dict[str, Any]] = {
    "argument_network": {
        "label": "Argument Network",
        "description": "organize the book around questions, positions, claims, evidence, objections, and responses",
        "top_level_node_kinds": ["question", "position", "major_claim"],
        "second_level_branch_kinds": ["evidence", "counterclaim", "response", "concept"],
        "recommended_extractors": ["concept", "claim", "evidence", "counterclaim", "theory_relation"],
    },
    "concept_network": {
        "label": "Concept Network",
        "description": "organize the book around focus questions, core concepts, sub-concepts, and typed propositions",
        "top_level_node_kinds": ["focus_question", "core_concept"],
        "second_level_branch_kinds": ["definition", "sub_concept", "contrast", "application"],
        "recommended_extractors": ["concept", "term", "definition", "proposition", "concept_relation"],
    },
    "event_timeline_network": {
        "label": "Event Timeline Network",
        "description": "organize the book around events, time, actors, places, and causal or temporal links",
        "top_level_node_kinds": ["period", "event_cluster", "actor"],
        "second_level_branch_kinds": ["event", "cause", "effect", "place", "interpretation"],
        "recommended_extractors": ["actor", "event", "time", "place", "causal_link", "temporal_link"],
    },
    "playbook_network": {
        "label": "Playbook Network",
        "description": "organize the book around goals, principles, frameworks, steps, cases, and actions",
        "top_level_node_kinds": ["goal", "framework", "domain_area"],
        "second_level_branch_kinds": ["principle", "step", "case", "action", "warning"],
        "recommended_extractors": ["framework", "principle", "step", "rule", "case", "action", "anti_pattern"],
    },
    "narrative_network": {
        "label": "Narrative Network",
        "description": "organize the book around characters, scenes, events, relationship changes, conflicts, and themes",
        "top_level_node_kinds": ["character", "plot_arc", "theme"],
        "second_level_branch_kinds": ["scene", "event", "relationship_change", "conflict", "motif"],
        "recommended_extractors": ["character", "scene", "event", "relationship_state", "conflict", "theme", "motif"],
    },
    "faceted_index_network": {
        "label": "Faceted Index Network",
        "description": "organize the book through multiple entry dimensions such as topic, person, place, time, method, and case",
        "top_level_node_kinds": ["topic", "person", "place", "time", "method", "case"],
        "second_level_branch_kinds": ["chapter_reference", "definition", "example", "related_topic"],
        "recommended_extractors": ["topic", "entity", "term", "case", "cross_reference", "source_pointer"],
    },
}


NETWORK_TO_PROFILE: dict[str, str] = {
    "argument_network": "argumentative",
    "concept_network": "textbook",
    "event_timeline_network": "historical",
    "playbook_network": "practical",
    "narrative_network": "narrative",
    "faceted_index_network": "technical_lite",
}


METADATA_PRIOR_KEYWORDS: dict[str, list[str]] = {
    "argument_network": [
        "philosophy",
        "political science",
        "law",
        "ethics",
        "theory",
        "argument",
        "conservatism",
        "liberalism",
        "humanism",
        "critique",
        "思想",
        "哲学",
        "政治理论",
    ],
    "concept_network": [
        "concept",
        "decision",
        "framework",
        "heritage studies",
        "critical heritage",
        "identity",
        "culture",
        "religion",
        "sociology",
        "anthropology",
        "知识",
        "概念",
        "文化",
        "宗教",
        "社会学",
        "人类学",
    ],
    "event_timeline_network": [
        "history",
        "historical",
        "archaeology",
        "biography",
        "war",
        "military",
        "empire",
        "ancestor",
        "dynasty",
        "chronology",
        "历史",
        "考古",
        "传记",
        "战争",
        "祖先",
    ],
    "playbook_network": [
        "guardrails",
        "human decisions",
        "decision",
        "age of ai",
        "business",
        "management",
        "self-help",
        "personal finance",
        "guide",
        "strategy",
        "how to",
        "use cases",
        "artificial intelligence",
        "technology",
        "diplomacy",
        "practice",
        "管理",
        "商业",
        "指南",
        "策略",
        "人工智能",
        "实践",
    ],
    "narrative_network": [
        "fiction",
        "novel",
        "literary",
        "memoir",
        "story",
        "narrative",
        "小说",
        "文学",
        "回忆录",
        "叙事",
    ],
    "faceted_index_network": [
        "reference",
        "handbook",
        "encyclopedia",
        "dictionary",
        "catalog",
        "glossary",
        "collection",
        "参考",
        "手册",
        "百科",
        "词典",
    ],
}


MetadataFetcher = Callable[[str, float], list[dict[str, Any]]]


APPARATUS_TITLE_RE = re.compile(
    r"\b("
    r"contents|copyright|dedication|acknowledg|preface|notes?|references|bibliography|index|glossary|appendix|"
    r"目录|版权|致谢|前言|注释|参考文献|书目|索引|术语表|附录"
    r")\b",
    re.IGNORECASE,
)
CHINESE_APPARATUS_TITLE_RE = re.compile(
    r"(?:书\s*名\s*页|扉\s*页|版\s*权(?:\s*页)?|目\s*录|致\s*谢|前\s*言|"
    r"注\s*释|参\s*考\s*文\s*献|书\s*目|索\s*引|术\s*语\s*表|附\s*录|文\s*前)"
)
CHINESE_NAVIGATION_TITLES = {"书名页", "扉页", "版权", "版权页", "目录", "索引", "文前"}


def _count_keyword_hits(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(keyword.lower()) for keyword in keywords)


def _compact_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _compact_title_key(text: str) -> str:
    return re.sub(r"[\s:_：\-]+", "", text).strip().lower()


def _is_apparatus_title(title: str) -> bool:
    return bool(APPARATUS_TITLE_RE.search(title) or CHINESE_APPARATUS_TITLE_RE.search(title))


def _source_name_from_run(run_dir: Path) -> str:
    manifest = _read_json(run_dir / "manifest.json")
    source = str(manifest.get("source_pdf") or "")
    if source:
        return Path(source).stem
    return run_dir.name


def _metadata_query_from_run(run_dir: Path) -> dict[str, Any]:
    stem = _source_name_from_run(run_dir)
    cleaned = re.sub(r"\.(pdf|epub)$", "", stem, flags=re.IGNORECASE)
    cleaned = cleaned.replace("_", " ")
    chunks = [_compact_space(chunk) for chunk in re.split(r"\s+--\s+", cleaned) if _compact_space(chunk)]
    title = chunks[0] if chunks else _compact_space(cleaned)
    author = chunks[1] if len(chunks) > 1 else None
    title = re.sub(r"\s+-\s+Wei Zhi$", "", title).strip()
    query = f"{title} {author or ''}".strip()
    return {
        "source_name": stem,
        "title_hint": title,
        "author_hint": author,
        "query": query,
    }


def _google_books_records(query: str, timeout_seconds: float) -> list[dict[str, Any]]:
    response = requests.get(
        "https://www.googleapis.com/books/v1/volumes",
        params={"q": query, "maxResults": 5, "printType": "books"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    records: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        info = item.get("volumeInfo") or {}
        records.append(
            {
                "source": "google_books",
                "title": info.get("title"),
                "subtitle": info.get("subtitle"),
                "authors": info.get("authors") or [],
                "publisher": info.get("publisher"),
                "published_date": info.get("publishedDate"),
                "description": info.get("description"),
                "categories": info.get("categories") or [],
                "page_count": info.get("pageCount"),
                "language": info.get("language"),
                "url": info.get("infoLink"),
            }
        )
    return records


def _open_library_records(query: str, timeout_seconds: float) -> list[dict[str, Any]]:
    response = requests.get(
        "https://openlibrary.org/search.json",
        params={"q": query, "limit": 5},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    records: list[dict[str, Any]] = []
    for doc in payload.get("docs") or []:
        records.append(
            {
                "source": "open_library",
                "title": doc.get("title"),
                "subtitle": doc.get("subtitle"),
                "authors": doc.get("author_name") or [],
                "publisher": (doc.get("publisher") or [None])[0],
                "published_date": str(doc.get("first_publish_year") or "") or None,
                "description": None,
                "categories": doc.get("subject") or [],
                "page_count": None,
                "language": doc.get("language") or [],
                "url": f"https://openlibrary.org{doc.get('key')}" if doc.get("key") else None,
            }
        )
    return records


def _fetch_metadata_records(query: str, timeout_seconds: float) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for provider, fetcher in (("google_books", _google_books_records), ("open_library", _open_library_records)):
        try:
            records.extend(fetcher(query, timeout_seconds))
        except Exception as exc:  # Network metadata is helpful but not required for planning.
            errors.append({"provider": provider, "error": str(exc)})
    if errors and not records:
        return [{"source": "metadata_error", "errors": errors}]
    return records


def _metadata_record_text(record: dict[str, Any]) -> str:
    fields: list[str] = []
    for key in ("title", "subtitle", "publisher", "description"):
        value = record.get(key)
        if value:
            fields.append(str(value))
    for key in ("authors", "categories", "language"):
        value = record.get(key)
        if isinstance(value, list):
            fields.extend(str(item) for item in value[:40])
        elif value:
            fields.append(str(value))
    return "\n".join(fields)


def _metadata_network_scores(records: list[dict[str, Any]], query_info: dict[str, Any]) -> dict[str, int]:
    text = "\n".join([query_info.get("source_name", ""), query_info.get("title_hint", ""), query_info.get("author_hint") or ""])
    text += "\n" + "\n".join(_metadata_record_text(record) for record in records if record.get("source") != "metadata_error")
    scores = {model: 0 for model in NETWORK_MODELS}
    for model, keywords in METADATA_PRIOR_KEYWORDS.items():
        scores[model] += _count_keyword_hits(text, keywords) * 12
    title_hint = str(query_info.get("title_hint") or "").lower()
    if "use case" in title_hint:
        scores["playbook_network"] += 55
    if "conservatism" in title_hint or "liberal political economy" in title_hint:
        scores["argument_network"] += 45
    if "ancestor" in title_hint or "heritage" in title_hint:
        scores["event_timeline_network"] += 28
        scores["concept_network"] += 26
    if "war" in title_hint or "underground" in title_hint:
        scores["event_timeline_network"] += 45
    if "wealth" in title_hint or "strategies" in title_hint:
        scores["playbook_network"] += 45
    return scores


def _metadata_confidence(scores: dict[str, int], records: list[dict[str, Any]]) -> float:
    valid_records = [record for record in records if record.get("source") != "metadata_error"]
    top_score = max(scores.values()) if scores else 0
    if not valid_records:
        return 0.25 if top_score > 0 else 0.0
    ordered = sorted(scores.values(), reverse=True)
    if not ordered or ordered[0] <= 0:
        return 0.35
    top = ordered[0]
    second = ordered[1] if len(ordered) > 1 else 0
    return round(min(0.88, 0.38 + ((top - second) / max(top, 1)) * 0.30 + min(len(valid_records), 5) * 0.04), 2)


def _render_metadata_markdown(prior: dict[str, Any]) -> str:
    lines = [
        "# Metadata Prior",
        "",
        f"- Query: `{prior['query']['query']}`",
        f"- Title hint: {prior['query']['title_hint']}",
        f"- Author hint: {prior['query'].get('author_hint') or 'unknown'}",
        f"- Primary network prior: `{prior['primary_network_model']}`",
        f"- Confidence: `{prior['confidence']}`",
        "",
        "## Network Prior Scores",
        "",
    ]
    for name, score in sorted(prior["network_scores"].items(), key=lambda item: item[1], reverse=True):
        lines.append(f"- `{name}`: {score}")
    lines.extend(["", "## Sources", ""])
    for record in prior["records"][:8]:
        if record.get("source") == "metadata_error":
            lines.append(f"- metadata lookup failed: {record.get('errors')}")
            continue
        title = record.get("title") or "Untitled"
        authors = ", ".join(record.get("authors") or [])
        categories = ", ".join(str(item) for item in (record.get("categories") or [])[:6])
        url = record.get("url") or ""
        lines.append(f"- `{record.get('source')}` {title} | {authors} | {categories} | {url}")
    lines.append("")
    return "\n".join(lines)


def build_metadata_prior(
    run_dir: Path,
    out_dir: Path | None = None,
    *,
    refresh: bool = False,
    timeout_seconds: float = 8.0,
    fetcher: MetadataFetcher | None = None,
) -> dict[str, Path]:
    """Search public book metadata and convert it into a weak network-model prior."""
    run_dir = run_dir.expanduser().resolve()
    knowledge_dir = (out_dir.expanduser().resolve() if out_dir else run_dir / "knowledge")
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    prior_path = knowledge_dir / "metadata-prior.json"
    markdown_path = knowledge_dir / "metadata-prior.md"
    if prior_path.exists() and not refresh:
        return {"prior": prior_path, "markdown": markdown_path}

    query_info = _metadata_query_from_run(run_dir)
    records = (fetcher or _fetch_metadata_records)(str(query_info["query"]), timeout_seconds)
    scores = _metadata_network_scores(records, query_info)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    primary = ranked[0][0] if ranked and ranked[0][1] > 0 else "faceted_index_network"
    confidence = _metadata_confidence(scores, records)
    prior = {
        "schema": "book_weaver_metadata_prior_v1",
        "run_dir": str(run_dir),
        "query": query_info,
        "primary_network_model": primary,
        "secondary_network_models": [name for name, score in ranked[1:3] if score > 0],
        "confidence": confidence,
        "network_scores": scores,
        "records": records,
        "policy": "metadata prior is a weak external signal; it may add weight to local plan scores but must not override local structural evidence by itself",
    }
    prior_path.write_text(json.dumps(prior, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_metadata_markdown(prior), encoding="utf-8")
    return {"prior": prior_path, "markdown": markdown_path}


def _score_profiles(chapters: list[dict[str, Any]], units: list[dict[str, Any]]) -> dict[str, int]:
    title_text = "\n".join(str(ch.get("title") or "") for ch in chapters)
    sample_units = [str(unit.get("text_translated") or unit.get("text_original") or "") for unit in units[:240]]
    body_text = "\n".join(sample_units)
    combined = f"{title_text}\n{body_text}"
    scores: dict[str, int] = {}
    for profile, definition in PROFILE_DEFINITIONS.items():
        title_hits = _count_keyword_hits(title_text, definition["keywords"])
        body_hits = _count_keyword_hits(body_text, definition["keywords"])
        scores[profile] = title_hits * 4 + body_hits

    table_count = sum(1 for unit in units if unit.get("kind") == "table")
    image_count = sum(1 for unit in units if unit.get("kind") == "image")
    list_count = sum(1 for unit in units if unit.get("kind") == "list")
    if table_count + image_count > max(3, len(units) * 0.08):
        scores["technical_lite"] += 8
    if list_count > max(4, len(units) * 0.10):
        scores["textbook"] += 4
        scores["practical"] += 3
    if re.search(r"\b(19|20)\d{2}\b|\b\d{1,2}(st|nd|rd|th)\s+century\b", combined, re.IGNORECASE):
        scores["historical"] += 5
    if re.search(r"\bI\s+argue\b|\bthis book argues\b|\bwe argue\b", combined, re.IGNORECASE):
        scores["argumentative"] += 8
    return scores


def _chapter_text_sample(chapter_id: str, units: list[dict[str, Any]], max_units: int = 8) -> str:
    blocks: list[str] = []
    for unit in units:
        if unit.get("chapter_id") != chapter_id:
            continue
        text = str(unit.get("text_translated") or unit.get("text_original") or "").strip()
        if text:
            blocks.append(text)
        if len(blocks) >= max_units:
            break
    return "\n".join(blocks)


def _global_text(chapters: list[dict[str, Any]], units: list[dict[str, Any]], max_units: int = 360) -> str:
    title_text = "\n".join(str(chapter.get("title") or "") for chapter in chapters)
    body_text = "\n".join(
        str(unit.get("text_translated") or unit.get("text_original") or "") for unit in units[:max_units]
    )
    return f"{title_text}\n{body_text}"


def _score_network_models(
    chapters: list[dict[str, Any]],
    units: list[dict[str, Any]],
    run_dir: Path,
    metadata_prior: dict[str, Any] | None = None,
) -> dict[str, int]:
    text = _global_text(chapters, units)
    lowered = f"{run_dir.name}\n{text}".lower()
    scores = {model: 0 for model in NETWORK_MODELS}

    argument_terms = [
        "argue",
        "argument",
        "claim",
        "conservative",
        "liberal",
        "theory",
        "critique",
        "reason",
        "humanity",
        "justice",
        "democracy",
        "freedom",
        "ethics",
        "philosophy",
        "concept",
        "论证",
        "主张",
        "概念",
        "理论",
        "批判",
        "民主",
        "自由",
    ]
    concept_terms = [
        "concept",
        "definition",
        "framework",
        "taxonomy",
        "model",
        "knowledge",
        "heritage",
        "identity",
        "culture",
        "religious",
        "discourse",
        "术语",
        "定义",
        "框架",
        "模型",
        "身份",
        "文化",
    ]
    event_terms = [
        "history",
        "historical",
        "war",
        "empire",
        "revolution",
        "century",
        "dynasty",
        "ceremony",
        "revival",
        "ancestor",
        "place",
        "migration",
        "timeline",
        "历史",
        "战争",
        "世纪",
        "仪式",
        "祖先",
    ]
    playbook_terms = [
        "use case",
        "case study",
        "how to",
        "guide",
        "applying",
        "tool",
        "platform",
        "strategy",
        "step",
        "checklist",
        "practice",
        "capacity",
        "leadership",
        "management",
        "operations",
        "ai use cases",
        "用例",
        "指南",
        "步骤",
        "工具",
        "实践",
    ]
    narrative_terms = [
        "novel",
        "story",
        "character",
        "scene",
        "plot",
        "fiction",
        "narrator",
        "dialogue",
        "memoir",
        "小说",
        "人物",
        "场景",
        "情节",
    ]
    faceted_terms = [
        "glossary",
        "appendix",
        "index",
        "reference",
        "handbook",
        "encyclopedia",
        "dictionary",
        "catalog",
        "术语表",
        "附录",
        "索引",
    ]

    scores["argument_network"] += _count_keyword_hits(lowered, argument_terms)
    scores["concept_network"] += _count_keyword_hits(lowered, concept_terms)
    scores["event_timeline_network"] += _count_keyword_hits(lowered, event_terms)
    scores["playbook_network"] += _count_keyword_hits(lowered, playbook_terms)
    scores["narrative_network"] += _count_keyword_hits(lowered, narrative_terms)
    scores["faceted_index_network"] += _count_keyword_hits(lowered, faceted_terms)

    chapter_titles = "\n".join(str(ch.get("title") or "") for ch in chapters).lower()
    if re.search(r"\bchapter\s+\d+.*\b(ai|tool|use|case|management|operations|security|capacity)\b", chapter_titles):
        scores["playbook_network"] += 35
    if re.search(r"\b(our|the)\s+(prejudices|sovereignty|sufficiency|reason|excellence)\b", chapter_titles):
        scores["argument_network"] += 35
    if re.search(r"\b(ceremon|ancestor|heritage|historical narratives|revivals?)\b", chapter_titles):
        scores["event_timeline_network"] += 28
        scores["concept_network"] += 12
    if "100 ai use cases" in chapter_titles:
        scores["playbook_network"] += 40
        scores["faceted_index_network"] += 15

    list_count = sum(1 for unit in units if unit.get("kind") == "list")
    table_image_count = sum(1 for unit in units if unit.get("kind") in {"table", "image"})
    total_units = max(len(units), 1)
    if list_count / total_units > 0.10:
        scores["playbook_network"] += 12
        scores["concept_network"] += 6
    if table_image_count / total_units > 0.15:
        scores["faceted_index_network"] += 10

    if metadata_prior:
        prior_scores = metadata_prior.get("network_scores") or {}
        prior_confidence = float(metadata_prior.get("confidence") or 0.0)
        for model in NETWORK_MODELS:
            prior_score = int(prior_scores.get(model) or 0)
            if prior_score > 0:
                scores[model] += int(prior_score * min(max(prior_confidence, 0.0), 0.9) * 0.55)

    return scores


def _confidence_from_network_scores(scores: dict[str, int]) -> float:
    ordered = sorted(scores.values(), reverse=True)
    if not ordered or ordered[0] <= 0:
        return 0.35
    top = ordered[0]
    second = ordered[1] if len(ordered) > 1 else 0
    return round(min(0.94, 0.46 + ((top - second) / max(top, 1)) * 0.34 + min(top, 60) / 300), 2)


def _network_action_for_chapter(chapter: dict[str, Any], units: list[dict[str, Any]]) -> tuple[str, list[str]]:
    action, reasons = _chapter_action(chapter, units)
    title = str(chapter.get("title") or "")
    lower_title = title.lower()
    title_key = _compact_title_key(title)
    chapter_units = [unit for unit in units if unit.get("chapter_id") == chapter.get("chapter_id")]
    paragraph_count = sum(1 for unit in chapter_units if unit.get("kind") == "paragraph")
    unit_count = len(chapter_units)
    chapter_text = "\n".join(str(unit.get("text_original") or "") for unit in chapter_units)
    chapter_text_lower = chapter_text.lower()
    chapter_text_chars = len(re.sub(r"\s+", "", chapter_text))

    hard_skip = re.search(
        r"\b(cover|copyright|dedication|contents|table of contents|title page|imprints? page|index|references|bibliography)\b",
        lower_title,
    )
    if hard_skip or title_key in CHINESE_NAVIGATION_TITLES:
        return "skip", ["front_back_matter_or_navigation"]
    if chapter_text_chars < 500 and (
        re.search(r"\b(?:dedicated to|in memory of)\b", chapter_text_lower)
        or re.search(r"谨以.{0,16}(?:纪念|献给)", chapter_text)
    ):
        return "skip", ["dedication_or_epigraph"]
    if re.search(r"\b(notes?|acknowledg(e)?ments?)\b", lower_title):
        return "preserve", ["apparatus_preserve_for_reference"]
    if re.search(r"\b(glossary|appendix)\b", lower_title):
        if paragraph_count >= 3 or "use case" in lower_title or "terms" in lower_title:
            return "preserve", ["structured_reference_material"]
        return "summarize", ["appendix_short_or_low_body_text"]
    if paragraph_count >= 4:
        return "extract", ["main_body_chapter"]
    if paragraph_count >= 2:
        return "summarize", ["short_body_section"]
    if unit_count and action != "skip":
        return "summarize", ["limited_text_section"]
    return action, reasons


def _planned_chapters(chapters: list[dict[str, Any]], units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for chapter in chapters:
        action, reasons = _network_action_for_chapter(chapter, units)
        chapter_units = [unit for unit in units if unit.get("chapter_id") == chapter.get("chapter_id")]
        planned.append(
            {
                "chapter_id": chapter.get("chapter_id"),
                "index": chapter.get("index"),
                "title": chapter.get("title"),
                "role": action,
                "reasons": reasons,
                "unit_count": len(chapter_units),
                "paragraph_count": sum(1 for unit in chapter_units if unit.get("kind") == "paragraph"),
                "visual_or_table_count": sum(1 for unit in chapter_units if unit.get("kind") in {"image", "table"}),
                "page_start": chapter.get("page_start"),
                "page_end": chapter.get("page_end"),
            }
        )
    return planned


def _candidate_top_level_nodes(
    primary_network_model: str,
    chapters: list[dict[str, Any]],
    units: list[dict[str, Any]],
    planned_chapters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    eligible = [chapter for chapter in planned_chapters if chapter["role"] == "extract"]
    nodes: list[dict[str, Any]] = []
    model = primary_network_model

    if model == "playbook_network":
        for chapter in eligible[:12]:
            title = str(chapter["title"])
            label = re.sub(r"^chapter\s+\d+\s*", "", title, flags=re.IGNORECASE)
            nodes.append(
                {
                    "node_id": f"n-{int(chapter['index']):03d}",
                    "label": label,
                    "kind": "domain_area",
                    "source_chapters": [chapter["chapter_id"]],
                    "second_level_branch_template": ["principles", "steps", "cases", "actions", "risks"],
                }
            )
    elif model == "event_timeline_network":
        for chapter in eligible[:12]:
            title = str(chapter["title"])
            nodes.append(
                {
                    "node_id": f"n-{int(chapter['index']):03d}",
                    "label": title,
                    "kind": "event_cluster",
                    "source_chapters": [chapter["chapter_id"]],
                    "second_level_branch_template": ["events", "actors", "places", "causes", "interpretations"],
                }
            )
    elif model == "argument_network":
        for chapter in eligible[:12]:
            title = str(chapter["title"])
            sample = _chapter_text_sample(str(chapter["chapter_id"]), units, max_units=4)
            kind = "question" if "?" in title else "major_claim"
            label = title if title else sample[:80]
            nodes.append(
                {
                    "node_id": f"n-{int(chapter['index']):03d}",
                    "label": label,
                    "kind": kind,
                    "source_chapters": [chapter["chapter_id"]],
                    "second_level_branch_template": ["claims", "evidence", "objections", "responses", "concepts"],
                }
            )
    elif model == "concept_network":
        concept_titles = [chapter for chapter in eligible if len(str(chapter["title"])) <= 90] or eligible
        for chapter in concept_titles[:12]:
            nodes.append(
                {
                    "node_id": f"n-{int(chapter['index']):03d}",
                    "label": str(chapter["title"]),
                    "kind": "core_concept",
                    "source_chapters": [chapter["chapter_id"]],
                    "second_level_branch_template": ["definitions", "sub_concepts", "contrasts", "applications"],
                }
            )
    elif model == "narrative_network":
        for chapter in eligible[:12]:
            nodes.append(
                {
                    "node_id": f"n-{int(chapter['index']):03d}",
                    "label": str(chapter["title"]),
                    "kind": "plot_arc",
                    "source_chapters": [chapter["chapter_id"]],
                    "second_level_branch_template": ["scenes", "events", "characters", "relationship_changes", "themes"],
                }
            )
    else:
        facets = [
            ("topic", "Topic Index"),
            ("person", "People / Organizations"),
            ("place", "Places"),
            ("time", "Time / Periods"),
            ("method", "Methods / Frameworks"),
            ("case", "Cases / Examples"),
        ]
        for idx, (kind, label) in enumerate(facets, start=1):
            nodes.append(
                {
                    "node_id": f"n-{idx:03d}",
                    "label": label,
                    "kind": kind,
                    "source_chapters": [chapter["chapter_id"] for chapter in eligible],
                    "second_level_branch_template": ["entries", "aliases", "source_chapters", "related_entries"],
                }
            )
    return nodes


def _confidence_from_scores(scores: dict[str, int]) -> float:
    if not scores:
        return 0.0
    ordered = sorted(scores.values(), reverse=True)
    top = ordered[0]
    second = ordered[1] if len(ordered) > 1 else 0
    if top <= 0:
        return 0.35
    margin = max(top - second, 0)
    return round(min(0.92, 0.42 + (margin / max(top, 1)) * 0.35 + min(top, 30) / 200), 2)


def _network_suitability(profile: str, confidence: float, risks: list[dict[str, Any]]) -> str:
    high_severity_count = sum(1 for risk in risks if risk.get("severity") == "high")
    if high_severity_count >= 2:
        return "low"
    if profile == "technical_lite" and high_severity_count:
        return "low"
    if confidence >= 0.65 and high_severity_count == 0:
        return "high"
    return "medium"


def _chapter_action(chapter: dict[str, Any], units: list[dict[str, Any]]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    title = str(chapter.get("title") or "")
    chapter_units = [unit for unit in units if unit.get("chapter_id") == chapter.get("chapter_id")]
    unit_count = len(chapter_units)
    image_table_count = sum(1 for unit in chapter_units if unit.get("kind") in {"image", "table"})
    paragraph_count = sum(1 for unit in chapter_units if unit.get("kind") == "paragraph")
    if bool(chapter.get("preserve_original")) or bool(chapter.get("translate")) is False:
        reasons.append("preserved_or_non_translated_resource")
    if _is_apparatus_title(title):
        reasons.append("apparatus_title")
    if unit_count == 0:
        reasons.append("empty_chapter")
    if unit_count and image_table_count / unit_count > 0.55:
        reasons.append("mostly_visual_or_tabular")
    if paragraph_count >= 4 and not reasons:
        return "extract", ["substantial_body_text"]
    if paragraph_count >= 2 and "apparatus_title" not in reasons:
        return "summarize", reasons or ["short_body_text"]
    if reasons:
        return "skip", reasons
    return "summarize", ["limited_body_text"]


def _chapter_assessments(chapters: list[dict[str, Any]], units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assessments: list[dict[str, Any]] = []
    for chapter in chapters:
        action, reasons = _chapter_action(chapter, units)
        chapter_units = [unit for unit in units if unit.get("chapter_id") == chapter.get("chapter_id")]
        assessments.append(
            {
                "chapter_id": chapter.get("chapter_id"),
                "index": chapter.get("index"),
                "title": chapter.get("title"),
                "action": action,
                "reasons": reasons,
                "unit_count": len(chapter_units),
                "paragraph_count": sum(1 for unit in chapter_units if unit.get("kind") == "paragraph"),
                "visual_or_table_count": sum(1 for unit in chapter_units if unit.get("kind") in {"image", "table"}),
                "page_start": chapter.get("page_start"),
                "page_end": chapter.get("page_end"),
            }
        )
    return assessments


def _detect_risks(
    chapters: list[dict[str, Any]],
    units: list[dict[str, Any]],
    chapter_assessments: list[dict[str, Any]],
    profile: str,
) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    total_units = max(len(units), 1)
    table_count = sum(1 for unit in units if unit.get("kind") == "table")
    image_count = sum(1 for unit in units if unit.get("kind") == "image")
    skipped = sum(1 for chapter in chapter_assessments if chapter["action"] == "skip")
    apparatus = sum(1 for chapter in chapter_assessments if "apparatus_title" in chapter["reasons"])
    no_translation_alignment = sum(1 for unit in units if unit.get("translation_alignment") == "unavailable")
    visual_ratio = (table_count + image_count) / total_units
    skip_ratio = skipped / max(len(chapters), 1)

    if visual_ratio > 0.20:
        risks.append(
            {
                "risk": "visual_or_table_heavy",
                "severity": "high" if visual_ratio > 0.35 else "medium",
                "detail": f"image/table units are {visual_ratio:.1%} of semantic units",
            }
        )
    if skip_ratio > 0.35:
        risks.append(
            {
                "risk": "many_chapters_not_suitable_for_extraction",
                "severity": "high" if skip_ratio > 0.55 else "medium",
                "detail": f"skip chapters are {skip_ratio:.1%} of chapters",
            }
        )
    if apparatus >= 3:
        risks.append(
            {
                "risk": "heavy_apparatus",
                "severity": "medium",
                "detail": "front/back matter, notes, references, glossary, or index are prominent",
            }
        )
    if no_translation_alignment > total_units * 0.60:
        risks.append(
            {
                "risk": "translation_not_block_aligned",
                "severity": "medium",
                "detail": "translated text cannot be safely matched to most semantic units",
            }
        )
    if profile == "technical_lite":
        risks.append(
            {
                "risk": "technical_semantics_weak_support",
                "severity": "medium",
                "detail": "formula/proof/table semantics should be indexed conservatively, not reconstructed automatically",
            }
        )
    if not risks:
        risks.append({"risk": "no_major_structural_risk_detected", "severity": "low", "detail": "rule-based check"})
    return risks


def _render_suitability_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Knowledge Suitability Report",
        "",
        f"- Profile: `{report['profile']}`",
        f"- Confidence: `{report['confidence']}`",
        f"- Network suitability: `{report['network_suitability']}`",
        f"- Secondary profiles: {', '.join(f'`{p}`' for p in report['secondary_profiles']) or 'none'}",
        "",
        "## Recommended Outputs",
        "",
    ]
    lines.extend(f"- `{item}`" for item in report["recommended_outputs"])
    lines.extend(["", "## Extractable Objects", ""])
    lines.extend(f"- `{item}`" for item in report["extractable_objects"])
    lines.extend(["", "## Do Not Extract", ""])
    lines.extend(f"- `{item}`" for item in report["do_not_extract"])
    lines.extend(["", "## Risks", ""])
    for risk in report["risks"]:
        lines.append(f"- `{risk['severity']}` `{risk['risk']}`: {risk['detail']}")
    lines.extend(["", "## Chapter Plan", "", "| # | Title | Action | Reason | Units | Pages |", "| --- | --- | --- | --- | --- | --- |"])
    for chapter in report["chapters"]:
        title = str(chapter["title"]).replace("|", "\\|")
        pages = ""
        if chapter.get("page_start") is not None:
            pages = str(chapter["page_start"])
            if chapter.get("page_end") and chapter["page_end"] != chapter["page_start"]:
                pages += f"-{chapter['page_end']}"
        lines.append(
            "| "
            f"{chapter['index']} | {title} | `{chapter['action']}` | "
            f"{', '.join(chapter['reasons'])} | {chapter['unit_count']} | {pages} |"
        )
    lines.extend(["", "## Next Command", "", f"```bash\n{report['next_command']}\n```", ""])
    return "\n".join(lines)


def build_suitability_report(run_dir: Path, out_dir: Path | None = None) -> dict[str, Path]:
    """Generate a rule-based Phase B suitability report for a knowledge package."""
    run_dir = run_dir.expanduser().resolve()
    knowledge_dir = (out_dir.expanduser().resolve() if out_dir else run_dir / "knowledge")
    if not (knowledge_dir / "semantic-units.json").exists() or not (knowledge_dir / "chapters.json").exists():
        build_knowledge_package(run_dir, out_dir=knowledge_dir)

    chapters = _read_json_list(knowledge_dir / "chapters.json")
    units = _read_json_list(knowledge_dir / "semantic-units.json")
    scores = _score_profiles(chapters, units)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    profile = ranked[0][0] if ranked and ranked[0][1] > 0 else "argumentative"
    confidence = _confidence_from_scores(scores)
    secondary_profiles = [name for name, score in ranked[1:3] if score > 0]
    chapter_assessments = _chapter_assessments(chapters, units)
    risks = _detect_risks(chapters, units, chapter_assessments, profile)
    network_suitability = _network_suitability(profile, confidence, risks)
    definition = PROFILE_DEFINITIONS[profile]
    report = {
        "schema": "book_weaver_suitability_v1",
        "run_dir": str(run_dir),
        "profile": profile,
        "confidence": confidence,
        "profile_scores": scores,
        "secondary_profiles": secondary_profiles,
        "network_suitability": network_suitability,
        "recommended_outputs": definition["outputs"],
        "extractable_objects": definition["objects"],
        "do_not_extract": definition["do_not_extract"],
        "risks": risks,
        "chapters": chapter_assessments,
        "next_command": f"book-weaver knowledge extract {run_dir} --profile {profile}",
    }

    paths = {
        "report": knowledge_dir / "suitability-report.json",
        "markdown": knowledge_dir / "suitability.md",
    }
    paths["report"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["markdown"].write_text(_render_suitability_markdown(report), encoding="utf-8")
    return paths


def _render_plan_markdown(plan: dict[str, Any]) -> str:
    candidate = plan["algorithm_candidate"]
    final = plan["final_plan"]
    model = NETWORK_MODELS[final["primary_network_model"]]
    lines = [
        "# Knowledge Plan",
        "",
        "## Decision",
        "",
        f"- Primary network model: `{final['primary_network_model']}`",
        f"- Model label: {model['label']}",
        f"- Confidence: `{final['confidence']}`",
        f"- Secondary network models: {', '.join(f'`{m}`' for m in final['secondary_network_models']) or 'none'}",
        f"- Planner mode: `{plan['planner']['mode']}`",
        "",
        "## Why This Network",
        "",
        final["rationale"],
        "",
        "## Network Shape",
        "",
        f"- Description: {model['description']}",
        f"- Top-level node kinds: {', '.join(f'`{item}`' for item in model['top_level_node_kinds'])}",
        f"- Second-level branch kinds: {', '.join(f'`{item}`' for item in model['second_level_branch_kinds'])}",
        "",
    ]
    metadata_prior = plan.get("metadata_prior")
    if metadata_prior:
        lines.extend(
            [
                "## Metadata Prior",
                "",
                f"- Primary prior: `{metadata_prior.get('primary_network_model')}`",
                f"- Confidence: `{metadata_prior.get('confidence')}`",
                f"- Query: `{(metadata_prior.get('query') or {}).get('query')}`",
                "",
            ]
        )
        for name, score in sorted((metadata_prior.get("network_scores") or {}).items(), key=lambda item: item[1], reverse=True):
            lines.append(f"- `{name}`: {score}")
        lines.append("")
    user_review = plan.get("user_review")
    if user_review:
        lines.extend(
            [
                "## User Review",
                "",
                f"- Applied: `{user_review.get('applied', False)}`",
                f"- Network override: `{user_review.get('network_override') or 'none'}`",
                f"- Preserve content types: {', '.join(f'`{item}`' for item in user_review.get('preserve_content_types', [])) or 'none'}",
                f"- Skip content types: {', '.join(f'`{item}`' for item in user_review.get('skip_content_types', [])) or 'none'}",
                f"- References supplied: `{len(user_review.get('references', []))}`",
                "",
            ]
        )
    lines.extend(
        [
        "## Candidate Top-Level Nodes",
        "",
        "| Node | Kind | Source Chapters | Branch Template |",
        "| --- | --- | --- | --- |",
        ]
    )
    for node in final["top_level_nodes"]:
        chapters = ", ".join(str(ch) for ch in node.get("source_chapters", []))
        branches = ", ".join(str(branch) for branch in node.get("second_level_branch_template", []))
        label = str(node["label"]).replace("|", "\\|")
        lines.append(f"| {label} | `{node['kind']}` | {chapters} | {branches} |")

    lines.extend(
        [
            "",
            "## Chapter Roles",
            "",
            "| # | Title | Role | Reason | Units | Pages |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for chapter in final["chapter_roles"]:
        title = str(chapter["title"]).replace("|", "\\|")
        pages = ""
        if chapter.get("page_start") is not None:
            pages = str(chapter["page_start"])
            if chapter.get("page_end") and chapter["page_end"] != chapter["page_start"]:
                pages += f"-{chapter['page_end']}"
        lines.append(
            "| "
            f"{chapter['index']} | {title} | `{chapter['role']}` | "
            f"{', '.join(chapter['reasons'])} | {chapter['unit_count']} | {pages} |"
        )

    lines.extend(["", "## Extraction Objects", ""])
    for item in final["recommended_extractors"]:
        lines.append(f"- `{item}`")

    lines.extend(["", "## Quality Gates", ""])
    for gate in plan["quality_gates"]:
        lines.append(f"- `{gate['gate']}`: {gate['expectation']}")

    lines.extend(["", "## Algorithm Scores", ""])
    for name, score in sorted(candidate["network_scores"].items(), key=lambda item: item[1], reverse=True):
        lines.append(f"- `{name}`: {score}")

    lines.extend(["", "## Next Command", "", f"```bash\n{final['next_command']}\n```", ""])
    return "\n".join(lines)


def _chapter_page_label(chapter: dict[str, Any]) -> str:
    if chapter.get("page_start") is None:
        return ""
    start = str(chapter["page_start"])
    if chapter.get("page_end") and chapter["page_end"] != chapter["page_start"]:
        return f"{start}-{chapter['page_end']}"
    return start


def _brief_chapter_sample(chapter_id: str, units: list[dict[str, Any]], *, max_chars: int = 320) -> str:
    samples: list[str] = []
    for unit in units:
        if unit.get("chapter_id") != chapter_id or unit.get("kind") not in {"paragraph", "quote", "list"}:
            continue
        text = _compact_space(str(unit.get("text_translated") or unit.get("text_original") or ""))
        if len(text) < 20:
            continue
        samples.append(text)
        if sum(len(sample) for sample in samples) >= max_chars:
            break
    sample = " ".join(samples)
    if len(sample) > max_chars:
        return sample[: max_chars - 3].rstrip() + "..."
    return sample


def _render_feedback_template(plan: dict[str, Any]) -> str:
    final = plan["final_plan"]
    return "\n".join(
        [
            "# Reader Feedback",
            "",
            "## Reading Goals",
            "",
            "- ",
            "",
            "## Frame Corrections",
            "",
            f"- Proposed network model: {final['primary_network_model']}",
            "- ",
            "",
            "## Preserve",
            "",
            "- ",
            "",
            "## Skip",
            "",
            "- ",
            "",
            "## Highlights",
            "",
            "- Chapter or page:",
            "  Excerpt:",
            "  Note:",
            "",
            "## Chapter Notes",
            "",
            "- Chapter:",
            "  Note:",
            "",
            "## Concepts Or Relations",
            "",
            "- ",
            "",
            "## Disagreements Or Missing Context",
            "",
            "- ",
            "",
            "## Book-Level Insights",
            "",
            "- ",
            "",
            "## External References",
            "",
            "- ",
            "",
        ]
    )


def _render_reader_brief_markdown(plan: dict[str, Any], units: list[dict[str, Any]]) -> str:
    final = plan["final_plan"]
    model = NETWORK_MODELS[final["primary_network_model"]]
    extract_chapters = [chapter for chapter in final["chapter_roles"] if chapter["role"] == "extract"]
    preserve_chapters = [chapter for chapter in final["chapter_roles"] if chapter["role"] == "preserve"]
    skip_chapters = [chapter for chapter in final["chapter_roles"] if chapter["role"] == "skip"]
    lines = [
        "# Reader Brief",
        "",
        "## Book Frame",
        "",
        f"- Network judgment: `{final['primary_network_model']}` ({model['label']})",
        f"- Confidence: `{final['confidence']}`",
        f"- Secondary models: {', '.join(f'`{item}`' for item in final.get('secondary_network_models', [])) or 'none'}",
        f"- Main sections: `{len(extract_chapters)}` extract, `{len(preserve_chapters)}` preserve, `{len(skip_chapters)}` skip",
        "",
        "## How To Read This Book",
        "",
        final["rationale"],
        "",
        "## Proposed Knowledge Shape",
        "",
        f"- Description: {model['description']}",
        f"- Top-level kinds: {', '.join(f'`{item}`' for item in model['top_level_node_kinds'])}",
        f"- Branch kinds: {', '.join(f'`{item}`' for item in model['second_level_branch_kinds'])}",
        "",
        "## Candidate Entry Points",
        "",
        "| Entry | Kind | Source chapters |",
        "| --- | --- | --- |",
    ]
    for node in final["top_level_nodes"][:12]:
        label = _escape_md_table_cell(node.get("label"))
        chapters = ", ".join(str(chapter) for chapter in node.get("source_chapters", []))
        lines.append(f"| {label} | `{node.get('kind')}` | {chapters} |")

    lines.extend(
        [
            "",
            "## Chapter Cards",
            "",
            "| # | Chapter ID | Chapter | Role | Pages | Sample |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for chapter in final["chapter_roles"]:
        sample = _brief_chapter_sample(str(chapter.get("chapter_id")), units)
        if not sample:
            sample = ", ".join(str(reason) for reason in chapter.get("reasons", []))
        lines.append(
            "| "
            f"{chapter.get('index')} | "
            f"`{chapter.get('chapter_id')}` | "
            f"{_escape_md_table_cell(chapter.get('title'))} | "
            f"`{chapter.get('role')}` | "
            f"{_chapter_page_label(chapter)} | "
            f"{_escape_md_table_cell(sample)} |"
        )

    lines.extend(["", "## Feedback Prompts", ""])
    lines.extend(
        [
            "- Which reading goal should this knowledge pass optimize for?",
            "- Is the selected network model right, or should it be mixed with another model?",
            "- Which chapters, appendices, tables, illustrations, or notes must be preserved?",
            "- What highlights, chapter observations, concept hints, relation hints, or disagreements should shape the joint draft?",
            "- What external reviews or references should be treated as weak context?",
            "",
        ]
    )
    return "\n".join(lines)


def _render_reader_brief_html(markdown_text: str) -> str:
    try:
        import markdown

        body = markdown.markdown(markdown_text, extensions=["tables"])
    except Exception:
        body = f"<pre>{html.escape(markdown_text)}</pre>"
    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head><meta charset=\"utf-8\"><title>Reader Brief</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.55;"
        "max-width:960px;margin:40px auto;padding:0 24px;color:#1f2933}"
        "table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #d8dee4;"
        "padding:6px 8px;vertical-align:top}code{background:#f6f8fa;padding:1px 4px;border-radius:4px}"
        "h1,h2{line-height:1.2}</style></head>\n"
        f"<body>{body}</body>\n"
        "</html>\n"
    )


def build_reader_brief(run_dir: Path, out_dir: Path | None = None) -> dict[str, Path]:
    """Render the first reader-facing Phase B artifact from the current plan."""
    run_dir = run_dir.expanduser().resolve()
    knowledge_dir = (out_dir.expanduser().resolve() if out_dir else run_dir / "knowledge")
    plan_path = knowledge_dir / "plan.json"
    if not plan_path.exists():
        build_knowledge_plan(run_dir, out_dir=knowledge_dir, metadata_prior="auto")
    if not (knowledge_dir / "semantic-units.json").exists():
        build_knowledge_package(run_dir, out_dir=knowledge_dir)

    plan = _read_json(plan_path)
    units = _read_json_list(knowledge_dir / "semantic-units.json")
    markdown_text = _render_reader_brief_markdown(plan, units)
    paths = {
        "markdown": knowledge_dir / "reader-brief.md",
        "html": knowledge_dir / "reader-brief.html",
        "template": knowledge_dir / "feedback-template.md",
    }
    paths["markdown"].write_text(markdown_text, encoding="utf-8")
    paths["html"].write_text(_render_reader_brief_html(markdown_text), encoding="utf-8")
    paths["template"].write_text(_render_feedback_template(plan), encoding="utf-8")
    return paths


def _parse_network_override(text: str) -> tuple[str | None, list[str]]:
    lowered = text.lower()
    explicit_hits = sorted(
        [model for model in NETWORK_MODELS if model in lowered],
        key=lambda model: lowered.find(model),
    )
    if explicit_hits:
        return explicit_hits[0], explicit_hits[1:]
    aliases = {
        "argument_network": ["argument_network", "argument", "argumentative", "论证"],
        "concept_network": ["concept_network", "concept", "概念"],
        "event_timeline_network": ["event_timeline_network", "timeline", "historical", "history", "event", "时间", "事件", "历史"],
        "playbook_network": ["playbook_network", "playbook", "practical", "operation", "guide", "操作", "实践", "手册"],
        "narrative_network": ["narrative_network", "narrative", "story", "fiction", "叙事", "小说"],
        "faceted_index_network": ["faceted_index_network", "faceted", "index", "reference", "多维", "索引"],
    }
    hits: list[str] = []
    for model, terms in aliases.items():
        if any(term in lowered for term in terms):
            hits.append(model)
    if "hybrid" in lowered or "mixed" in lowered or "混合" in lowered:
        return hits[0] if hits else None, hits[1:]
    return hits[0] if hits else None, hits[1:]


def _extract_answer_section(text: str, labels: list[str]) -> str:
    pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?im)^\s*(?:{pattern})\s*[:：]\s*(.*)$", text)
    if not match:
        return ""
    start = match.end(1)
    first_line = match.group(1).strip()
    rest = text[start:]
    known_headers = [
        "organization",
        "组织方式",
        "network",
        "网络",
        "preserve",
        "必须保留",
        "保留",
        "skip",
        "可以跳过",
        "跳过",
        "references",
        "reference",
        "参考材料",
        "参考",
    ]
    next_header = re.search(
        rf"(?im)^[ \t]*(?:{'|'.join(re.escape(label) for label in known_headers)})\s*[:：]\s*",
        rest,
    )
    if next_header:
        rest = rest[: next_header.start()]
    return _compact_space(f"{first_line}\n{rest}")


def _parse_content_types(section: str) -> list[str]:
    allowed = {
        "appendix": ["appendix", "appendices", "附录"],
        "glossary": ["glossary", "术语"],
        "chronology": ["chronology", "timeline", "年表", "时间表"],
        "case_list": ["case list", "use case", "use cases", "案例", "用例"],
        "illustrations": ["illustration", "illustrations", "figure", "figures", "image", "images", "插图", "图片"],
        "tables": ["table", "tables", "表格"],
        "references": ["reference", "references", "bibliography", "参考文献", "书目"],
        "notes": ["note", "notes", "endnote", "footnote", "注释"],
        "index": ["index", "索引"],
        "copyright": ["copyright", "版权"],
        "publisher_pages": ["publisher", "imprint", "title page", "出版社", "扉页"],
        "blank_pages": ["blank", "空白"],
        "contents": ["contents", "table of contents", "目录"],
    }
    lowered = section.lower()
    found: list[str] = []
    for canonical, aliases in allowed.items():
        if any(alias in lowered for alias in aliases):
            found.append(canonical)
    return found


def _parse_references(section: str) -> list[dict[str, Any]]:
    if not section.strip():
        return []
    urls = re.findall(r"https?://\S+", section)
    cleaned = section
    for url in urls:
        cleaned = cleaned.replace(url, "")
    references = [{"type": "url", "source": "user_supplied_reference", "content": url.strip()} for url in urls]
    text = cleaned.strip()
    if text:
        references.append({"type": "text", "source": "user_supplied_reference", "content": text[:4000]})
    return references


def parse_user_review_answers(text: str) -> dict[str, Any]:
    network_section = _extract_answer_section(text, ["organization", "组织方式", "network", "网络"])
    preserve_section = _extract_answer_section(text, ["preserve", "必须保留", "保留"])
    skip_section = _extract_answer_section(text, ["skip", "可以跳过", "跳过"])
    reference_section = _extract_answer_section(text, ["references", "reference", "参考材料", "参考"])
    network_override, secondary = _parse_network_override(network_section)
    return {
        "schema": "book_weaver_user_review_v1",
        "network_override": network_override,
        "secondary_network_models": secondary,
        "preserve_content_types": _parse_content_types(preserve_section),
        "skip_content_types": _parse_content_types(skip_section),
        "references": _parse_references(reference_section),
        "raw_answers": text,
        "policy": "user review may adjust structure and boundaries once; it must not create accepted knowledge without source evidence",
    }


def _extract_feedback_section(text: str, labels: list[str]) -> str:
    pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?im)^\s*##\s*(?:{pattern})\s*$", text)
    if not match:
        return ""
    rest = text[match.end() :]
    next_header = re.search(r"(?m)^\s*##\s+", rest)
    if next_header:
        rest = rest[: next_header.start()]
    return rest.strip()


def _bullet_items(section: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    for line in section.splitlines():
        if re.match(r"^\s*[-*]\s+", line):
            if current:
                item = "\n".join(current).strip()
                if item and item != "-":
                    items.append(item)
            current = [re.sub(r"^\s*[-*]\s+", "", line).rstrip()]
        elif current:
            current.append(line.rstrip())
    if current:
        item = "\n".join(current).strip()
        if item and item != "-":
            items.append(item)
    if not items and section.strip():
        items.append(section.strip())
    return [item for item in items if _compact_space(item).strip(" -")]


def _feedback_objects_from_items(kind: str, items: list[str]) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        content = item.strip()
        if not content:
            continue
        objects.append(
            {
                "feedback_id": f"fb-{kind}-{index:03d}",
                "kind": kind,
                "source": "user_supplied_feedback",
                "content": content,
                "content_hash": _text_hash(content),
            }
        )
    return objects


def parse_reader_feedback(text: str) -> dict[str, Any]:
    """Parse the reader feedback template into raw feedback objects without model rewriting."""
    sections = {
        "reading_goal": _extract_feedback_section(text, ["Reading Goals", "阅读目标"]),
        "book_frame": _extract_feedback_section(text, ["Book Frame", "书籍框架"]),
        "frame_correction": _extract_feedback_section(text, ["Frame Corrections", "框架修正"]),
        "preserve_request": _extract_feedback_section(text, ["Preserve", "必须保留", "保留"]),
        "skip_request": _extract_feedback_section(text, ["Skip", "可以跳过", "跳过"]),
        "highlight": _extract_feedback_section(text, ["Highlights", "划线", "摘录"]),
        "chapter_note": _extract_feedback_section(text, ["Chapter Notes", "章节笔记"]),
        "concept_or_relation": _extract_feedback_section(text, ["Concepts Or Relations", "概念或关系"]),
        "disagreement": _extract_feedback_section(
            text,
            ["Disagreements Or Missing Context", "Disagreements", "Missing Context", "分歧或缺失背景"],
        ),
        "book_level_user_insight": _extract_feedback_section(
            text,
            ["Book-Level Insights", "Whole Book Insights", "全书洞察", "全书级洞察"],
        ),
        "external_reference": _extract_feedback_section(text, ["External References", "参考材料", "外部参考"]),
    }
    objects: list[dict[str, Any]] = []
    for kind, section in sections.items():
        objects.extend(_feedback_objects_from_items(kind, _bullet_items(section)))
    frame_text = "\n".join(
        section
        for section in [sections["book_frame"], sections["frame_correction"], _extract_answer_section(text, ["organization", "组织方式", "network", "网络"])]
        if section
    )
    preserve_text = "\n".join(
        section
        for section in [sections["preserve_request"], _extract_answer_section(text, ["preserve", "必须保留", "保留"])]
        if section
    )
    skip_text = "\n".join(
        section
        for section in [sections["skip_request"], _extract_answer_section(text, ["skip", "可以跳过", "跳过"])]
        if section
    )
    reference_text = "\n".join(
        section
        for section in [sections["external_reference"], _extract_answer_section(text, ["references", "reference", "参考材料", "参考"])]
        if section
    )
    network_override, secondary = _parse_network_override(frame_text)
    structural_review = {
        "schema": "book_weaver_user_review_v1",
        "network_override": network_override,
        "secondary_network_models": secondary,
        "preserve_content_types": _parse_content_types(preserve_text),
        "skip_content_types": _parse_content_types(skip_text),
        "references": _parse_references(reference_text),
        "raw_answers": text,
        "policy": "feedback-level structural review may adjust planning, but accepted knowledge still requires source evidence or explicit user-origin state",
    }
    if not objects and text.strip():
        objects.append(
            {
                "feedback_id": "fb-general-001",
                "kind": "general",
                "source": "user_supplied_feedback",
                "content": text.strip(),
                "content_hash": _text_hash(text),
            }
        )
    return {
        "schema": "book_weaver_reader_feedback_raw_v1",
        "objects": objects,
        "structural_review": structural_review,
        "raw_text": text,
        "policy": "raw feedback is preserved before alignment; external references are weak priors, not accepted source evidence",
    }


def _review_has_actionable_inputs(review: dict[str, Any]) -> bool:
    return bool(
        review.get("network_override")
        or review.get("secondary_network_models")
        or review.get("preserve_content_types")
        or review.get("skip_content_types")
        or review.get("references")
    )


def _align_feedback_object(
    feedback: dict[str, Any],
    chapters: list[dict[str, Any]],
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    content = str(feedback.get("content") or "")
    lowered = content.lower()
    aligned_chapters: list[str] = []
    aligned_units: list[str] = []
    reasons: list[str] = []

    for chapter in chapters:
        cid = str(chapter.get("chapter_id") or "")
        title = str(chapter.get("title") or "")
        if cid and cid.lower() in lowered:
            aligned_chapters.append(cid)
            reasons.append("chapter_id_mentioned")
        elif title and len(title) >= 4 and title.lower() in lowered:
            aligned_chapters.append(cid)
            reasons.append("chapter_title_mentioned")
        elif re.search(rf"\bchapter\s+{int(chapter.get('index') or 0)}\b", lowered):
            aligned_chapters.append(cid)
            reasons.append("chapter_index_mentioned")

    compact_content = _compact_space(content).lower()
    for unit in units:
        text = _compact_space(str(unit.get("text_translated") or unit.get("text_original") or ""))
        if len(text) < 24:
            continue
        unit_key = str(unit.get("unit_id") or "")
        if unit_key and unit_key.lower() in lowered:
            aligned_units.append(unit_key)
            aligned_chapters.append(str(unit.get("chapter_id")))
            reasons.append("unit_id_mentioned")
            continue
        snippet = text[:96].lower()
        if len(snippet) >= 24 and snippet in compact_content:
            aligned_units.append(unit_key)
            aligned_chapters.append(str(unit.get("chapter_id")))
            reasons.append("unit_excerpt_matched")
            continue
        if compact_content and len(compact_content) >= 32 and compact_content[:90] in text.lower():
            aligned_units.append(unit_key)
            aligned_chapters.append(str(unit.get("chapter_id")))
            reasons.append("feedback_text_matched_unit")

    aligned_chapters = sorted({chapter for chapter in aligned_chapters if chapter})
    aligned_units = sorted({unit for unit in aligned_units if unit})
    return {
        **feedback,
        "alignment": {
            "status": "aligned" if aligned_chapters or aligned_units else "unaligned",
            "chapter_ids": aligned_chapters,
            "unit_ids": aligned_units,
            "reasons": sorted(set(reasons)),
        },
    }


def ingest_reader_feedback(run_dir: Path, input_path: Path, out_dir: Path | None = None) -> dict[str, Path]:
    """Preserve reader feedback and write first-pass deterministic alignment objects."""
    run_dir = run_dir.expanduser().resolve()
    input_path = input_path.expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Missing feedback input: {input_path}")
    knowledge_dir = (out_dir.expanduser().resolve() if out_dir else run_dir / "knowledge")
    if not (knowledge_dir / "chapters.json").exists() or not (knowledge_dir / "semantic-units.json").exists():
        build_knowledge_package(run_dir, out_dir=knowledge_dir)

    text = input_path.read_text(encoding="utf-8")
    raw_feedback = parse_reader_feedback(text)
    chapters = _read_json_list(knowledge_dir / "chapters.json")
    units = _read_json_list(knowledge_dir / "semantic-units.json")
    aligned_objects = [_align_feedback_object(obj, chapters, units) for obj in raw_feedback["objects"]]
    aligned_feedback = {
        "schema": "book_weaver_reader_feedback_aligned_v1",
        "source_raw_feedback": None,
        "objects": aligned_objects,
        "summary": {
            "total": len(aligned_objects),
            "aligned": sum(1 for item in aligned_objects if item["alignment"]["status"] == "aligned"),
            "unaligned": sum(1 for item in aligned_objects if item["alignment"]["status"] == "unaligned"),
        },
        "policy": "alignment is advisory; unaligned book-level insights remain valid for the joint draft",
    }
    feedback_id = f"{input_path.stem}-{_text_hash(text)}"
    raw_dir = knowledge_dir / "feedback" / "raw"
    aligned_dir = knowledge_dir / "feedback" / "aligned"
    raw_dir.mkdir(parents=True, exist_ok=True)
    aligned_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "raw_markdown": raw_dir / f"{feedback_id}.md",
        "raw": raw_dir / f"{feedback_id}.json",
        "aligned": aligned_dir / f"{feedback_id}.json",
    }
    raw_feedback["source_path"] = str(input_path)
    aligned_feedback["source_raw_feedback"] = str(paths["raw"])
    paths["raw_markdown"].write_text(text, encoding="utf-8")
    paths["raw"].write_text(json.dumps(raw_feedback, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["aligned"].write_text(json.dumps(aligned_feedback, ensure_ascii=False, indent=2), encoding="utf-8")
    structural_review = raw_feedback.get("structural_review") or {}
    if isinstance(structural_review, dict) and _review_has_actionable_inputs(structural_review):
        plan_path = knowledge_dir / "plan.json"
        if not plan_path.exists():
            build_knowledge_plan(run_dir, out_dir=knowledge_dir, metadata_prior="auto")
        plan = _read_json(plan_path)
        reviewed_plan = _apply_review_to_plan(plan, structural_review)
        review_path = knowledge_dir / "user-review.json"
        reference_prior_path = knowledge_dir / "reference-prior.json"
        markdown_path = knowledge_dir / "plan.md"
        review_path.write_text(json.dumps(reviewed_plan["user_review"], ensure_ascii=False, indent=2), encoding="utf-8")
        if reviewed_plan.get("reference_prior"):
            reference_prior_path.write_text(
                json.dumps(reviewed_plan["reference_prior"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        plan_path.write_text(json.dumps(reviewed_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(_render_plan_markdown(reviewed_plan), encoding="utf-8")
        paths["review"] = review_path
        paths["reference_prior"] = reference_prior_path
        paths["plan"] = plan_path
        paths["plan_markdown"] = markdown_path
    return paths


def _chapter_matches_content_type(chapter: dict[str, Any], content_type: str) -> bool:
    title = str(chapter.get("title") or "").lower()
    mapping = {
        "appendix": ["appendix"],
        "glossary": ["glossary"],
        "chronology": ["chronology", "timeline"],
        "case_list": ["use case", "case list"],
        "illustrations": ["illustration", "list of illustrations", "figures"],
        "tables": ["table", "tables"],
        "references": ["references", "bibliography"],
        "notes": ["notes", "endnotes", "footnotes"],
        "index": ["index"],
        "copyright": ["copyright"],
        "publisher_pages": ["publisher", "imprint", "title page"],
        "blank_pages": ["blank"],
        "contents": ["contents", "table of contents"],
    }
    return any(term in title for term in mapping.get(content_type, []))


def _apply_review_to_plan(plan: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    final = plan["final_plan"]
    if review.get("network_override") in NETWORK_MODELS:
        final["primary_network_model"] = review["network_override"]
        final["secondary_network_models"] = review.get("secondary_network_models") or final.get("secondary_network_models", [])
        final["recommended_extractors"] = NETWORK_MODELS[review["network_override"]]["recommended_extractors"]
        final["rationale"] += f" User review set primary network model to `{review['network_override']}`."
    for chapter in final["chapter_roles"]:
        for content_type in review.get("preserve_content_types", []):
            if _chapter_matches_content_type(chapter, content_type):
                chapter["role"] = "preserve"
                chapter["reasons"] = sorted(set(chapter.get("reasons", []) + [f"user_preserve_{content_type}"]))
        for content_type in review.get("skip_content_types", []):
            if _chapter_matches_content_type(chapter, content_type):
                chapter["role"] = "skip"
                chapter["reasons"] = sorted(set(chapter.get("reasons", []) + [f"user_skip_{content_type}"]))
    plan["user_review"] = {
        **review,
        "applied": True,
    }
    if review.get("references"):
        plan["reference_prior"] = {
            "schema": "book_weaver_reference_prior_v1",
            "source": "user_supplied_reference",
            "references": review["references"],
            "policy": "reference material can influence planning and later candidate extraction prompts, but accepted knowledge must still cite the source book",
        }
    return plan


def build_knowledge_plan(
    run_dir: Path,
    out_dir: Path | None = None,
    planner: str = "rule",
    metadata_prior: str = "none",
) -> dict[str, Path]:
    """Build a network-oriented Phase B processing plan.

    The current planner is algorithmic. The output shape reserves an LLM adjudication layer so
    future model-backed planning can be added without changing downstream consumers.
    """
    run_dir = run_dir.expanduser().resolve()
    knowledge_dir = (out_dir.expanduser().resolve() if out_dir else run_dir / "knowledge")
    if planner != "rule":
        raise ValueError("Only planner='rule' is implemented. Model adjudication will be added behind this schema.")
    if not (knowledge_dir / "semantic-units.json").exists() or not (knowledge_dir / "chapters.json").exists():
        build_knowledge_package(run_dir, out_dir=knowledge_dir)

    chapters = _read_json_list(knowledge_dir / "chapters.json")
    units = _read_json_list(knowledge_dir / "semantic-units.json")
    if not chapters:
        raise ValueError(f"No chapters found in {knowledge_dir / 'chapters.json'}")

    if not (knowledge_dir / "suitability-report.json").exists():
        build_suitability_report(run_dir, out_dir=knowledge_dir)
    suitability = _read_json(knowledge_dir / "suitability-report.json")

    prior: dict[str, Any] | None = None
    if metadata_prior == "auto":
        prior_paths = build_metadata_prior(run_dir, out_dir=knowledge_dir)
        prior = _read_json(prior_paths["prior"])
    elif metadata_prior != "none":
        raise ValueError("metadata_prior must be 'none' or 'auto'")

    local_network_scores = _score_network_models(chapters, units, run_dir)
    network_scores = _score_network_models(chapters, units, run_dir, metadata_prior=prior)
    ranked_models = sorted(network_scores.items(), key=lambda item: item[1], reverse=True)
    primary_network_model = ranked_models[0][0] if ranked_models and ranked_models[0][1] > 0 else "faceted_index_network"
    confidence = _confidence_from_network_scores(network_scores)
    secondary_models = [name for name, score in ranked_models[1:3] if score > 0]
    chapter_roles = _planned_chapters(chapters, units)
    top_level_nodes = _candidate_top_level_nodes(primary_network_model, chapters, units, chapter_roles)
    network_definition = NETWORK_MODELS[primary_network_model]
    extract_count = sum(1 for chapter in chapter_roles if chapter["role"] == "extract")
    preserve_count = sum(1 for chapter in chapter_roles if chapter["role"] == "preserve")
    skip_count = sum(1 for chapter in chapter_roles if chapter["role"] == "skip")
    rationale = (
        f"The algorithm selected `{primary_network_model}` because it has the highest network score "
        f"({network_scores[primary_network_model]}). The plan uses this as the organizing skeleton, "
        f"then assigns chapter roles from structure and content signals rather than profile alone. "
        f"Current role counts: extract={extract_count}, preserve={preserve_count}, skip={skip_count}."
    )
    if primary_network_model == "playbook_network":
        rationale += " The book appears organized around domains, use cases, tools, steps, or practical application."
    elif primary_network_model == "argument_network":
        rationale += " The book appears organized around claims, concepts, positions, and argumentative development."
    elif primary_network_model == "event_timeline_network":
        rationale += " The book appears organized around historical development, events, actors, places, or temporal change."
    elif primary_network_model == "concept_network":
        rationale += " The book appears organized around concepts, definitions, frameworks, and explanatory relations."
    elif primary_network_model == "faceted_index_network":
        rationale += " The book appears better served by multiple entry facets than by a single linear skeleton."

    candidate = {
        "schema": "book_weaver_plan_candidate_v1",
        "planner": "rule",
        "metadata_prior_mode": metadata_prior,
        "metadata_prior_path": str(knowledge_dir / "metadata-prior.json") if prior else None,
        "local_network_scores": local_network_scores,
        "network_scores": network_scores,
        "ranked_network_models": [{"model": name, "score": score} for name, score in ranked_models],
        "primary_network_model": primary_network_model,
        "secondary_network_models": secondary_models,
        "confidence": confidence,
        "profile_hint": suitability.get("profile"),
        "chapter_roles": chapter_roles,
        "top_level_nodes": top_level_nodes,
        "rationale": rationale,
    }
    final_plan = {
        "primary_network_model": primary_network_model,
        "secondary_network_models": secondary_models,
        "confidence": confidence,
        "rationale": rationale,
        "top_level_nodes": top_level_nodes,
        "chapter_roles": chapter_roles,
        "recommended_extractors": network_definition["recommended_extractors"],
        "next_command": f"book-weaver knowledge extract {run_dir} --network-model {primary_network_model}",
    }
    plan = {
        "schema": "book_weaver_knowledge_plan_v1",
        "run_dir": str(run_dir),
        "source": {
            "knowledge_manifest": str(knowledge_dir / "manifest.json"),
            "suitability_report": str(knowledge_dir / "suitability-report.json"),
        },
        "planner": {
            "mode": planner,
            "llm_adjudication": None,
            "policy": "algorithm controls structure and validation; model adjudication may later propose changes but cannot bypass schema",
        },
        "algorithm_candidate": candidate,
        "metadata_prior": prior,
        "final_plan": final_plan,
        "quality_gates": [
            {
                "gate": "network_model_human_check",
                "expectation": "the selected network model must match the book's organizing logic, not just its subject words",
            },
            {
                "gate": "chapter_role_human_check",
                "expectation": "main body chapters should not be skipped; front/back matter should not be deep-extracted",
            },
            {
                "gate": "top_level_node_check",
                "expectation": "top-level nodes should form a two-to-three-level usable network skeleton",
            },
            {
                "gate": "provenance_check",
                "expectation": "all future extracted nodes must cite chapter_id and semantic unit evidence",
            },
        ],
    }

    paths = {
        "candidates": knowledge_dir / "plan-candidates.json",
        "plan": knowledge_dir / "plan.json",
        "markdown": knowledge_dir / "plan.md",
    }
    paths["candidates"].write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["plan"].write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["markdown"].write_text(_render_plan_markdown(plan), encoding="utf-8")
    return paths


ARGUMENT_CLAIM_RE = re.compile(
    r"\b(argues?|claims?|therefore|because|requires?|suggests?|shows?|demonstrates?|contends?|"
    r"should|must|cannot|however|nevertheless)\b|"
    r"(认为|主张|因此|因为|说明|表明|必须|应当|不能|然而|但是)",
    re.IGNORECASE,
)
ARGUMENT_EVIDENCE_RE = re.compile(
    r"\b(for example|for instance|case study|according to|evidence|data|survey|report|history|"
    r"study|research|citation|interview|archive|court|law|policy)\b|"
    r"(例如|案例|证据|数据显示|报告|研究|调查|根据|史料|访谈|法院|法律|政策)",
    re.IGNORECASE,
)
ARGUMENT_FACT_RE = re.compile(
    r"\b("
    r"\d{4}|percent|percentage|million|billion|survey|report|study|court|law|act|policy|"
    r"war|election|government|institution|university|press|committee|case|example|"
    r"Tocqueville|Jefferson|Cicero|Kant|Frankfurt|Aristotle|Plato|Rawls"
    r")\b|"
    r"(百分比|调查|报告|研究|法院|法律|政策|政府|机构|大学|案例|例如|战争|选举)",
    re.IGNORECASE,
)
ARGUMENT_CONCEPT_RE = re.compile(
    r"\b(concept of|theory of|framework|principle|definition of|notion of|idea of)\s+([A-Za-z][A-Za-z\- ]{2,60})",
    re.IGNORECASE,
)
ARGUMENT_CONCEPT_STOPWORDS = {
    "first",
    "second",
    "third",
    "fourth",
    "former",
    "latter",
    "for",
    "and",
    "or",
    "the",
    "a",
    "an",
    "this",
    "that",
    "these",
    "those",
    "same",
    "other",
    "another",
    "chapter",
    "book",
    "one",
    "two",
}


def _unit_text_for_extraction(unit: dict[str, Any]) -> str:
    return str(unit.get("text_translated") or unit.get("text_original") or "").strip()


def _short_label(text: str, *, max_chars: int = 110) -> str:
    compact = _compact_space(re.sub(r"^#+\s*", "", text))
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _evidence_pointer(unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "chapter_id": unit.get("chapter_id"),
        "unit_id": unit.get("unit_id"),
        "source_pages": unit.get("source_pages") or [],
        "page_start": unit.get("page_start"),
        "page_end": unit.get("page_end"),
        "text_original_hash": unit.get("text_original_hash"),
        "translation_alignment": unit.get("translation_alignment"),
    }


def _argument_node(
    *,
    node_id: str,
    node_type: str,
    label: str,
    chapter: dict[str, Any],
    unit: dict[str, Any] | None = None,
    text: str | None = None,
    confidence: float = 0.62,
) -> dict[str, Any]:
    source_unit_ids = [unit["unit_id"]] if unit and unit.get("unit_id") else []
    source_pages = unit.get("source_pages") if unit else chapter.get("source_pages")
    evidence = [_evidence_pointer(unit)] if unit else []
    return {
        "node_id": node_id,
        "node_type": node_type,
        "label": label,
        "text": text or label,
        "confidence": confidence,
        "source": {
            "chapter_id": chapter.get("chapter_id"),
            "chapter_index": chapter.get("index"),
            "source_unit_ids": source_unit_ids,
            "source_pages": source_pages or [],
            "evidence": evidence,
        },
    }


def _node_keywords(text: str) -> list[str]:
    lowered = text.lower()
    tokens = re.findall(r"\b[A-Za-z][A-Za-z\-]{3,}\b", lowered)
    stop = ARGUMENT_CONCEPT_STOPWORDS | {
        "that",
        "with",
        "from",
        "have",
        "this",
        "which",
        "their",
        "there",
        "would",
        "could",
        "should",
        "about",
        "between",
        "through",
        "chapter",
        "argument",
        "claim",
        "evidence",
    }
    seen: set[str] = set()
    keywords: list[str] = []
    for token in tokens:
        if token in stop or token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= 12:
            break
    return keywords


def _extract_argument_concepts(text: str) -> list[str]:
    concepts: list[str] = []
    for match in ARGUMENT_CONCEPT_RE.finditer(text):
        concept = _compact_space(match.group(2)).strip(" .,:;")
        if 3 <= len(concept) <= 70 and concept.lower() not in ARGUMENT_CONCEPT_STOPWORDS:
            concepts.append(concept)
    quoted = re.findall(r"[“\"]([^”\"]{3,50})[”\"]", text)
    concepts.extend(
        _compact_space(item)
        for item in quoted
        if len(item.split()) <= 6 and _compact_space(item).lower() not in ARGUMENT_CONCEPT_STOPWORDS
    )
    seen: set[str] = set()
    unique: list[str] = []
    for concept in concepts:
        key = concept.lower()
        if key not in seen:
            seen.add(key)
            unique.append(concept)
    return unique[:5]


def _build_argument_cross_links(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    existing = {(edge.get("edge_type"), edge.get("source_node_id"), edge.get("target_node_id")) for edge in edges}
    concepts = [node for node in nodes if node.get("node_type") == "concept"]
    claims = [node for node in nodes if node.get("node_type") == "claim"]
    facts = [node for node in nodes if node.get("node_type") in {"fact", "data_point", "case"}]

    for concept in concepts:
        concept_text = str(concept.get("label") or "").lower()
        if len(concept_text) < 4:
            continue
        linked = 0
        for claim in claims:
            if claim.get("source", {}).get("chapter_id") == concept.get("source", {}).get("chapter_id"):
                continue
            claim_text = str(claim.get("text") or claim.get("label") or "").lower()
            if concept_text in claim_text:
                key = ("relates_to_concept", claim.get("node_id"), concept.get("node_id"))
                if key not in existing:
                    existing.add(key)
                    edges.append(
                        {
                            "edge_id": f"edge-{len(edges) + 1:05d}",
                            "edge_type": "relates_to_concept",
                            "source_node_id": claim.get("node_id"),
                            "target_node_id": concept.get("node_id"),
                            "confidence": 0.42,
                            "source": (claim.get("source") or {}).get("evidence", [{}])[0],
                        }
                    )
                    linked += 1
                    if linked >= 5:
                        break

    claim_keywords = {str(claim.get("node_id")): set(_node_keywords(str(claim.get("text") or ""))) for claim in claims}
    for fact in facts:
        fact_keywords = set(_node_keywords(str(fact.get("text") or "")))
        if not fact_keywords:
            continue
        linked = 0
        for claim in claims:
            if claim.get("source", {}).get("chapter_id") == fact.get("source", {}).get("chapter_id"):
                continue
            overlap = fact_keywords & claim_keywords.get(str(claim.get("node_id")), set())
            if len(overlap) >= 2:
                key = ("cross_supports", fact.get("node_id"), claim.get("node_id"))
                if key not in existing:
                    existing.add(key)
                    edges.append(
                        {
                            "edge_id": f"edge-{len(edges) + 1:05d}",
                            "edge_type": "cross_supports",
                            "source_node_id": fact.get("node_id"),
                            "target_node_id": claim.get("node_id"),
                            "confidence": 0.36,
                            "source": (fact.get("source") or {}).get("evidence", [{}])[0],
                            "shared_keywords": sorted(overlap)[:8],
                        }
                    )
                    linked += 1
                    if linked >= 3:
                        break


def _extract_argument_network(chapters: list[dict[str, Any]], units: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    chapter_roles = {
        chapter["chapter_id"]: chapter
        for chapter in (plan.get("final_plan", {}).get("chapter_roles") or [])
        if chapter.get("role") == "extract"
    }
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    chapter_claims: dict[str, list[str]] = {}
    concept_index: dict[str, str] = {}
    chapter_by_id = {chapter["chapter_id"]: chapter for chapter in chapters}

    for chapter_id, role in chapter_roles.items():
        chapter = chapter_by_id.get(chapter_id) or role
        question_id = f"arg-{len(nodes) + 1:05d}"
        question = _argument_node(
            node_id=question_id,
            node_type="question",
            label=str(chapter.get("title") or f"Chapter {chapter.get('index')}"),
            chapter=chapter,
            confidence=0.72,
        )
        nodes.append(question)
        chapter_claims[chapter_id] = []

        chapter_units = [
            unit
            for unit in units
            if unit.get("chapter_id") == chapter_id and unit.get("kind") in {"paragraph", "quote", "list"}
        ]
        claim_units = [unit for unit in chapter_units if ARGUMENT_CLAIM_RE.search(_unit_text_for_extraction(unit))]
        evidence_units = [unit for unit in chapter_units if ARGUMENT_EVIDENCE_RE.search(_unit_text_for_extraction(unit))]
        fact_units = [
            unit
            for unit in chapter_units
            if ARGUMENT_FACT_RE.search(_unit_text_for_extraction(unit))
        ]
        if not claim_units:
            claim_units = chapter_units[:2]

        for unit in claim_units[:5]:
            text = _unit_text_for_extraction(unit)
            if not text:
                continue
            claim_id = f"arg-{len(nodes) + 1:05d}"
            nodes.append(
                _argument_node(
                    node_id=claim_id,
                    node_type="claim",
                    label=_short_label(text),
                    text=text,
                    chapter=chapter,
                    unit=unit,
                    confidence=0.66 if ARGUMENT_CLAIM_RE.search(text) else 0.48,
                )
            )
            chapter_claims[chapter_id].append(claim_id)
            edges.append(
                {
                    "edge_id": f"edge-{len(edges) + 1:05d}",
                    "edge_type": "develops",
                    "source_node_id": question_id,
                    "target_node_id": claim_id,
                    "confidence": 0.62,
                    "source": _evidence_pointer(unit),
                }
            )

            for concept in _extract_argument_concepts(text):
                key = concept.lower()
                concept_id = concept_index.get(key)
                if not concept_id:
                    concept_id = f"arg-{len(nodes) + 1:05d}"
                    concept_index[key] = concept_id
                    nodes.append(
                        _argument_node(
                            node_id=concept_id,
                            node_type="concept",
                            label=concept,
                            text=concept,
                            chapter=chapter,
                            unit=unit,
                            confidence=0.58,
                        )
                    )
                edges.append(
                    {
                        "edge_id": f"edge-{len(edges) + 1:05d}",
                        "edge_type": "uses_concept",
                        "source_node_id": claim_id,
                        "target_node_id": concept_id,
                        "confidence": 0.56,
                        "source": _evidence_pointer(unit),
                    }
                )

        for idx, unit in enumerate(evidence_units[:8]):
            text = _unit_text_for_extraction(unit)
            if not text:
                continue
            evidence_id = f"arg-{len(nodes) + 1:05d}"
            nodes.append(
                _argument_node(
                    node_id=evidence_id,
                    node_type="evidence",
                    label=_short_label(text),
                    text=text,
                    chapter=chapter,
                    unit=unit,
                    confidence=0.60,
                )
            )
            target_claims = chapter_claims.get(chapter_id) or []
            if target_claims:
                edges.append(
                    {
                        "edge_id": f"edge-{len(edges) + 1:05d}",
                        "edge_type": "supports",
                        "source_node_id": evidence_id,
                        "target_node_id": target_claims[min(idx, len(target_claims) - 1)],
                        "confidence": 0.50,
                        "source": _evidence_pointer(unit),
                    }
                )

        for idx, unit in enumerate(fact_units[:10]):
            text = _unit_text_for_extraction(unit)
            if not text:
                continue
            fact_type = "data_point" if re.search(r"\d|percent|million|billion|百分", text, re.IGNORECASE) else "fact"
            if re.search(r"\b(case|example|for example|例如|案例)\b", text, re.IGNORECASE):
                fact_type = "case"
            fact_id = f"arg-{len(nodes) + 1:05d}"
            nodes.append(
                _argument_node(
                    node_id=fact_id,
                    node_type=fact_type,
                    label=_short_label(text),
                    text=text,
                    chapter=chapter,
                    unit=unit,
                    confidence=0.54,
                )
            )
            target_claims = chapter_claims.get(chapter_id) or []
            if target_claims:
                edges.append(
                    {
                        "edge_id": f"edge-{len(edges) + 1:05d}",
                        "edge_type": "contextualizes",
                        "source_node_id": fact_id,
                        "target_node_id": target_claims[min(idx, len(target_claims) - 1)],
                        "confidence": 0.42,
                        "source": _evidence_pointer(unit),
                    }
                )

    _build_argument_cross_links(nodes, edges)

    return {
        "schema": "book_weaver_extraction_result_v1",
        "network_model": "argument_network",
        "extractor": "rule_argument_v1",
        "status": "completed",
        "nodes": nodes,
        "edges": edges,
        "warnings": [
            "rule-based extraction is a candidate layer; accepted knowledge still requires human or model review",
            "concept extraction is conservative and may miss implicit concepts",
        ],
    }


def _unsupported_extraction(network_model: str) -> dict[str, Any]:
    return {
        "schema": "book_weaver_extraction_result_v1",
        "network_model": network_model,
        "extractor": None,
        "status": "unsupported",
        "nodes": [],
        "edges": [],
        "warnings": [
            f"`{network_model}` has no extractor yet; this is intentional so one algorithm is not applied to all book types."
        ],
    }


def _render_extraction_report(result: dict[str, Any], plan: dict[str, Any]) -> str:
    nodes = result.get("nodes") or []
    edges = result.get("edges") or []
    counts: dict[str, int] = {}
    for node in nodes:
        counts[str(node.get("node_type"))] = counts.get(str(node.get("node_type")), 0) + 1
    nodes_by_id = {str(node.get("node_id")): node for node in nodes}
    nodes_by_chapter: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        chapter_id = str((node.get("source") or {}).get("chapter_id") or "unknown")
        nodes_by_chapter.setdefault(chapter_id, []).append(node)
    edges_by_target: dict[str, list[dict[str, Any]]] = {}
    edges_by_source: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        edges_by_target.setdefault(str(edge.get("target_node_id")), []).append(edge)
        edges_by_source.setdefault(str(edge.get("source_node_id")), []).append(edge)
    chapter_roles = {
        str(chapter.get("chapter_id")): chapter for chapter in plan.get("final_plan", {}).get("chapter_roles", [])
    }
    lines = [
        "# Knowledge Extraction Report",
        "",
        f"- network_model: `{result.get('network_model')}`",
        f"- extractor: `{result.get('extractor')}`",
        f"- status: `{result.get('status')}`",
        f"- nodes: `{len(nodes)}`",
        f"- edges: `{len(edges)}`",
        "",
        "## Node Counts",
        "",
    ]
    if counts:
        lines.extend(f"- `{key}`: {value}" for key, value in sorted(counts.items()))
    else:
        lines.append("- none")
    lines.extend(["", "## Chapter Argument Maps", ""])
    if not nodes:
        lines.append("No extracted nodes.")
    for chapter_id, chapter_nodes in nodes_by_chapter.items():
        role = chapter_roles.get(chapter_id, {})
        chapter_title = role.get("title") or next((node.get("label") for node in chapter_nodes if node.get("node_type") == "question"), chapter_id)
        lines.extend(
            [
                f"### {chapter_title}",
                "",
                f"- chapter_id: `{chapter_id}`",
                f"- role: `{role.get('role', 'extract')}`",
                "",
            ]
        )
        questions = [node for node in chapter_nodes if node.get("node_type") == "question"]
        concepts = [node for node in chapter_nodes if node.get("node_type") == "concept"]
        claims = [node for node in chapter_nodes if node.get("node_type") == "claim"]
        chapter_evidence = [node for node in chapter_nodes if node.get("node_type") == "evidence"]
        if questions:
            lines.append("**Question / organizing issue**")
            lines.append("")
            for question in questions:
                lines.append(f"- {question.get('label')}")
            lines.append("")
        if concepts:
            lines.append("**Concepts**")
            lines.append("")
            for concept in concepts[:20]:
                source = concept.get("source") or {}
                unit_ids = ", ".join(source.get("source_unit_ids") or [])
                lines.append(f"- `{concept.get('node_id')}` {concept.get('label')} ({unit_ids})")
            if len(concepts) > 20:
                lines.append(f"- ... {len(concepts) - 20} more concepts")
            lines.append("")
        if claims:
            lines.append("**Claims and Support**")
            lines.append("")
            for claim in claims:
                source = claim.get("source") or {}
                unit_ids = ", ".join(source.get("source_unit_ids") or [])
                pages = ", ".join(str(page) for page in (source.get("source_pages") or [])[:6])
                lines.append(f"- **Claim `{claim.get('node_id')}`** ({unit_ids}; pages {pages or 'n/a'})")
                lines.append(f"  - {claim.get('label')}")
                support_edges = [
                    edge for edge in edges_by_target.get(str(claim.get("node_id")), []) if edge.get("edge_type") == "supports"
                ]
                if support_edges:
                    lines.append("  - Evidence:")
                    for edge in support_edges[:6]:
                        evidence = nodes_by_id.get(str(edge.get("source_node_id")))
                        if not evidence:
                            continue
                        evidence_source = evidence.get("source") or {}
                        evidence_units = ", ".join(evidence_source.get("source_unit_ids") or [])
                        lines.append(f"    - `{evidence.get('node_id')}` {evidence.get('label')} ({evidence_units})")
                    if len(support_edges) > 6:
                        lines.append(f"    - ... {len(support_edges) - 6} more evidence links")
                concept_edges = [
                    edge for edge in edges_by_source.get(str(claim.get("node_id")), []) if edge.get("edge_type") == "uses_concept"
                ]
                if concept_edges:
                    concept_labels = [
                        str(nodes_by_id.get(str(edge.get("target_node_id")), {}).get("label"))
                        for edge in concept_edges[:8]
                        if nodes_by_id.get(str(edge.get("target_node_id")))
                    ]
                    if concept_labels:
                        lines.append(f"  - Concepts used: {', '.join(concept_labels)}")
            lines.append("")
        elif chapter_evidence:
            lines.append("**Evidence candidates without matched claims**")
            lines.append("")
            for evidence in chapter_evidence[:12]:
                source = evidence.get("source") or {}
                unit_ids = ", ".join(source.get("source_unit_ids") or [])
                lines.append(f"- `{evidence.get('node_id')}` {evidence.get('label')} ({unit_ids})")
            lines.append("")
        factual_nodes = [node for node in chapter_nodes if node.get("node_type") in {"fact", "data_point", "case"}]
        if factual_nodes:
            lines.append("**Facts / Data / Cases**")
            lines.append("")
            for fact in factual_nodes[:16]:
                source = fact.get("source") or {}
                unit_ids = ", ".join(source.get("source_unit_ids") or [])
                pages = ", ".join(str(page) for page in (source.get("source_pages") or [])[:6])
                lines.append(
                    f"- `{fact.get('node_id')}` `{fact.get('node_type')}` {fact.get('label')} "
                    f"({unit_ids}; pages {pages or 'n/a'})"
                )
            if len(factual_nodes) > 16:
                lines.append(f"- ... {len(factual_nodes) - 16} more factual nodes")
            lines.append("")

    cross_edges = [edge for edge in edges if edge.get("edge_type") in {"relates_to_concept", "cross_supports"}]
    if cross_edges:
        lines.extend(["", "## Cross-Chapter Links", "", "| Type | Source | Target | Shared |", "| --- | --- | --- | --- |"])
        for edge in cross_edges:
            source_node = nodes_by_id.get(str(edge.get("source_node_id")), {})
            target_node = nodes_by_id.get(str(edge.get("target_node_id")), {})
            source_label = _short_label(str(source_node.get("label") or edge.get("source_node_id")), max_chars=70).replace("|", "/")
            target_label = _short_label(str(target_node.get("label") or edge.get("target_node_id")), max_chars=70).replace("|", "/")
            shared = ", ".join(edge.get("shared_keywords") or [])
            lines.append(f"| `{edge.get('edge_type')}` | {source_label} | {target_label} | {shared} |")

    lines.extend(["", "## Edge Index", "", "| Type | Source | Target | Source Unit |", "| --- | --- | --- | --- |"])
    for edge in edges:
        source_node = nodes_by_id.get(str(edge.get("source_node_id")), {})
        target_node = nodes_by_id.get(str(edge.get("target_node_id")), {})
        edge_source = edge.get("source") or {}
        unit_id = edge_source.get("unit_id") or ""
        source_label = _short_label(str(source_node.get("label") or edge.get("source_node_id")), max_chars=60).replace("|", "/")
        target_label = _short_label(str(target_node.get("label") or edge.get("target_node_id")), max_chars=60).replace("|", "/")
        lines.append(f"| `{edge.get('edge_type')}` | {source_label} | {target_label} | `{unit_id}` |")
    if result.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result["warnings"])
    lines.extend(
        [
            "",
            "## Plan Context",
            "",
            f"- planned model: `{plan.get('final_plan', {}).get('primary_network_model')}`",
            f"- plan confidence: `{plan.get('final_plan', {}).get('confidence')}`",
            "",
        ]
    )
    return "\n".join(lines)


def build_knowledge_extraction(
    run_dir: Path,
    out_dir: Path | None = None,
    *,
    network_model: str | None = None,
) -> dict[str, Path]:
    """Run a profile-specific knowledge extractor.

    Only `argument_network` is implemented in this first version. Other network models produce
    an explicit unsupported report rather than being forced through the wrong algorithm.
    """
    run_dir = run_dir.expanduser().resolve()
    knowledge_dir = (out_dir.expanduser().resolve() if out_dir else run_dir / "knowledge")
    if not (knowledge_dir / "semantic-units.json").exists() or not (knowledge_dir / "chapters.json").exists():
        build_knowledge_package(run_dir, out_dir=knowledge_dir)
    if not (knowledge_dir / "plan.json").exists():
        build_knowledge_plan(run_dir, out_dir=knowledge_dir, metadata_prior="auto")

    plan = _read_json(knowledge_dir / "plan.json")
    selected_model = network_model or str(plan.get("final_plan", {}).get("primary_network_model") or "")
    if selected_model not in NETWORK_MODELS:
        raise ValueError(f"Unknown network_model: {selected_model}")

    chapters = _read_json_list(knowledge_dir / "chapters.json")
    units = _read_json_list(knowledge_dir / "semantic-units.json")
    if selected_model == "argument_network":
        result = _extract_argument_network(chapters, units, plan)
    else:
        result = _unsupported_extraction(selected_model)

    manifest = {
        "schema": "book_weaver_extraction_manifest_v1",
        "run_dir": str(run_dir),
        "network_model": selected_model,
        "status": result["status"],
        "extractor": result.get("extractor"),
        "files": {
            "nodes": str(knowledge_dir / "extracted-nodes.json"),
            "edges": str(knowledge_dir / "extracted-edges.json"),
            "report": str(knowledge_dir / "extraction-report.md"),
        },
        "counts": {
            "nodes": len(result.get("nodes") or []),
            "edges": len(result.get("edges") or []),
        },
    }
    paths = {
        "manifest": knowledge_dir / "extraction-manifest.json",
        "nodes": knowledge_dir / "extracted-nodes.json",
        "edges": knowledge_dir / "extracted-edges.json",
        "report": knowledge_dir / "extraction-report.md",
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["nodes"].write_text(json.dumps(result.get("nodes") or [], ensure_ascii=False, indent=2), encoding="utf-8")
    paths["edges"].write_text(json.dumps(result.get("edges") or [], ensure_ascii=False, indent=2), encoding="utf-8")
    paths["report"].write_text(_render_extraction_report(result, plan), encoding="utf-8")
    return paths


def apply_user_review(run_dir: Path, answers_path: Path, out_dir: Path | None = None) -> dict[str, Path]:
    """Apply one user-supplied structural review to an existing knowledge plan."""
    run_dir = run_dir.expanduser().resolve()
    answers_path = answers_path.expanduser().resolve()
    knowledge_dir = (out_dir.expanduser().resolve() if out_dir else run_dir / "knowledge")
    if not answers_path.exists():
        raise FileNotFoundError(f"Missing answers file: {answers_path}")
    plan_path = knowledge_dir / "plan.json"
    if not plan_path.exists():
        build_knowledge_plan(run_dir, out_dir=knowledge_dir, metadata_prior="auto")
    plan = _read_json(plan_path)
    answers = answers_path.read_text(encoding="utf-8")
    review = parse_user_review_answers(answers)
    reviewed_plan = _apply_review_to_plan(plan, review)
    review_path = knowledge_dir / "user-review.json"
    reference_prior_path = knowledge_dir / "reference-prior.json"
    review_path.write_text(json.dumps(reviewed_plan["user_review"], ensure_ascii=False, indent=2), encoding="utf-8")
    if reviewed_plan.get("reference_prior"):
        reference_prior_path.write_text(
            json.dumps(reviewed_plan["reference_prior"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    plan_path.write_text(json.dumps(reviewed_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = knowledge_dir / "plan.md"
    markdown_path.write_text(_render_plan_markdown(reviewed_plan), encoding="utf-8")
    return {
        "review": review_path,
        "reference_prior": reference_prior_path,
        "plan": plan_path,
        "markdown": markdown_path,
    }
