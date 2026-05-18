from __future__ import annotations

import json
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


def _count_keyword_hits(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(keyword.lower()) for keyword in keywords)


def _compact_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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
    chapter_units = [unit for unit in units if unit.get("chapter_id") == chapter.get("chapter_id")]
    paragraph_count = sum(1 for unit in chapter_units if unit.get("kind") == "paragraph")
    unit_count = len(chapter_units)

    hard_skip = re.search(
        r"\b(cover|copyright|dedication|contents|table of contents|title page|imprints? page|index|references|bibliography)\b",
        lower_title,
    )
    if hard_skip:
        return "skip", ["front_back_matter_or_navigation"]
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
