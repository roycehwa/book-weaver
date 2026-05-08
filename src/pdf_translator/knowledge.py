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


APPARATUS_TITLE_RE = re.compile(
    r"\b("
    r"contents|copyright|dedication|acknowledg|preface|notes?|references|bibliography|index|glossary|appendix|"
    r"目录|版权|致谢|前言|注释|参考文献|书目|索引|术语表|附录"
    r")\b",
    re.IGNORECASE,
)


def _count_keyword_hits(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(keyword.lower()) for keyword in keywords)


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
    if APPARATUS_TITLE_RE.search(title):
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
