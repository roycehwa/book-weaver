from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


HUMANITIES_HISTORY = "humanities_history"
SOCIAL_ECON_PHILOSOPHY = "social_econ_philosophy"
SCIENCE_TECH_ENGINEERING = "science_tech_engineering"
FORMAL_LOGIC_PHILOSOPHY = "formal_logic_philosophy"

DEFAULT_GLOSSARY_PROFILE = SOCIAL_ECON_PHILOSOPHY

GLOSSARY_PROFILE_LABELS: dict[str, str] = {
    HUMANITIES_HISTORY: "人文·历史·艺术",
    SOCIAL_ECON_PHILOSOPHY: "社会·经济·哲学",
    SCIENCE_TECH_ENGINEERING: "科学·技术·工程",
    FORMAL_LOGIC_PHILOSOPHY: "逻辑·语言哲学",
}

VALID_GLOSSARY_PROFILES = frozenset(GLOSSARY_PROFILE_LABELS)


@dataclass(frozen=True)
class GlossaryProfilePolicy:
    profile_id: str
    label: str
    person_name_penalty: float
    domain_markers: frozenset[str]
    fragment_prefix_penalty: float
    enable_connector_phrases: bool
    enable_index_parse: bool
    index_parse_weight: float
    principles: tuple[str, ...]
    min_accept_score: float = 3.0
    min_word_count: int = 2
    allow_single_word_domain: bool = False
    single_word_markers: frozenset[str] = frozenset()


SOCIAL_DOMAIN_MARKERS = frozenset(
    {
        "act",
        "policy",
        "capitalism",
        "corporation",
        "corporate",
        "shareholder",
        "primacy",
        "board",
        "governance",
        "economic",
        "economy",
        "finance",
        "financial",
        "regulation",
        "regulatory",
        "federal",
        "commission",
        "institution",
        "stakeholder",
        "equity",
        "market",
        "labor",
        "union",
        "tax",
        "fiscal",
        "monetary",
        "democracy",
        "republic",
        "amendment",
        "statute",
        "legislation",
    }
)

HUMANITIES_DOMAIN_MARKERS = frozenset(
    {
        "revolution",
        "dynasty",
        "empire",
        "war",
        "museum",
        "renaissance",
        "baroque",
        "archaeolog",
        "excavation",
        "civilization",
        "folklore",
        "anthropolog",
    }
)

STEM_DOMAIN_MARKERS = frozenset(
    {
        "theorem",
        "algorithm",
        "equation",
        "definition",
        "experiment",
        "hypothesis",
        "formula",
        "symbol",
        "notation",
        "protocol",
        "method",
        "model",
        "data",
    }
)

LOGIC_DOMAIN_MARKERS = frozenset(
    {
        "logic",
        "logical",
        "syntax",
        "semantic",
        "semantics",
        "modal",
        "modality",
        "truth",
        "paradox",
        "predicate",
        "quantifier",
        "axiom",
        "lemma",
        "theorem",
        "proof",
        "validity",
        "valid",
        "consistency",
        "inconsistent",
        "necessity",
        "necessary",
        "possible",
        "possibility",
        "implication",
        "entailment",
        "reference",
        "referential",
        "deflation",
        "realism",
        "nominalism",
        "formal",
        "formalism",
        "tarski",
        "kripke",
        "frege",
        "russell",
        "wittgenstein",
        "godel",
        "goedel",
    }
)

FRAGMENT_PREFIXES = ("The ", "In ", "On ", "How ", "A ")

GLOSSARY_PROFILES: dict[str, GlossaryProfilePolicy] = {
    HUMANITIES_HISTORY: GlossaryProfilePolicy(
        profile_id=HUMANITIES_HISTORY,
        label=GLOSSARY_PROFILE_LABELS[HUMANITIES_HISTORY],
        person_name_penalty=0.0,
        domain_markers=HUMANITIES_DOMAIN_MARKERS,
        fragment_prefix_penalty=3.0,
        enable_connector_phrases=True,
        enable_index_parse=True,
        index_parse_weight=2.5,
        principles=(
            "优先人名、地名、事件、作品与地方性专名",
            "解析索引章与连接词短语（of/and）",
            "强力过滤句首碎片短语",
            "两词人名不降权",
        ),
    ),
    SOCIAL_ECON_PHILOSOPHY: GlossaryProfilePolicy(
        profile_id=SOCIAL_ECON_PHILOSOPHY,
        label=GLOSSARY_PROFILE_LABELS[SOCIAL_ECON_PHILOSOPHY],
        person_name_penalty=1.0,
        domain_markers=SOCIAL_DOMAIN_MARKERS,
        fragment_prefix_penalty=3.0,
        enable_connector_phrases=False,
        enable_index_parse=True,
        index_parse_weight=2.0,
        principles=(
            "优先政策、经济与治理概念",
            "两词人名默认降权",
            "排除书名与句首碎片",
        ),
    ),
    SCIENCE_TECH_ENGINEERING: GlossaryProfilePolicy(
        profile_id=SCIENCE_TECH_ENGINEERING,
        label=GLOSSARY_PROFILE_LABELS[SCIENCE_TECH_ENGINEERING],
        person_name_penalty=1.5,
        domain_markers=STEM_DOMAIN_MARKERS,
        fragment_prefix_penalty=2.0,
        enable_connector_phrases=False,
        enable_index_parse=True,
        index_parse_weight=3.0,
        principles=(
            "优先术语定义、方法与标准",
            "索引/附录术语加权",
            "叙事人名除非在索引中否则降权",
        ),
    ),
    FORMAL_LOGIC_PHILOSOPHY: GlossaryProfilePolicy(
        profile_id=FORMAL_LOGIC_PHILOSOPHY,
        label=GLOSSARY_PROFILE_LABELS[FORMAL_LOGIC_PHILOSOPHY],
        person_name_penalty=0.5,
        domain_markers=LOGIC_DOMAIN_MARKERS,
        fragment_prefix_penalty=2.0,
        enable_connector_phrases=True,
        enable_index_parse=True,
        index_parse_weight=3.5,
        min_accept_score=2.0,
        min_word_count=1,
        allow_single_word_domain=True,
        single_word_markers=LOGIC_DOMAIN_MARKERS,
        principles=(
            "优先逻辑、语义、模态与真理理论术语",
            "允许高频单词术语（modal、syntax、truth 等）",
            "索引章与连接词短语加权",
            "哲学家专名保留但不过度扩张",
        ),
    ),
}


DETECTION_KEYWORDS: dict[str, list[str]] = {
    HUMANITIES_HISTORY: [
        "history",
        "historical",
        "revolution",
        "war",
        "empire",
        "biography",
        "dynasty",
        "archaeolog",
        "excavation",
        "art",
        "painting",
        "sculpture",
        "music",
        "architecture",
        "museum",
        "gallery",
        "renaissance",
        "baroque",
        "local",
        "regional",
        "county",
        "province",
        "folklore",
        "civilization",
        "anthropolog",
        "ethnograph",
        "novel",
        "fiction",
        "story",
        "character",
        "tale",
        "历史",
        "考古",
        "艺术",
        "小说",
        "故事",
        "地方",
        "人类史",
        "通史",
    ],
    SOCIAL_ECON_PHILOSOPHY: [
        "policy",
        "capitalism",
        "market",
        "governance",
        "argue",
        "theory",
        "democracy",
        "shareholder",
        "corporation",
        "economic",
        "philosophy",
        "ethics",
        "治理",
        "股东",
        "资本主义",
        "政策",
    ],
    SCIENCE_TECH_ENGINEERING: [
        "theorem",
        "algorithm",
        "equation",
        "definition",
        "experiment",
        "formula",
        "figure",
        "table",
        "notation",
        "protocol",
        "engineering",
        "公式",
        "定理",
        "算法",
        "实验",
    ],
    FORMAL_LOGIC_PHILOSOPHY: [
        "logic",
        "logical",
        "syntax",
        "semantic",
        "semantics",
        "modal",
        "modality",
        "truth",
        "paradox",
        "predicate",
        "quantifier",
        "axiom",
        "validity",
        "consistency",
        "necessity",
        "entailment",
        "reference",
        "deflationism",
        "realism",
        "nominalism",
        "formal",
        "tarski",
        "kripke",
        "frege",
        "russell",
        "wittgenstein",
        "language",
        "meaning",
        "proposition",
        "逻辑",
        "语义",
        "模态",
        "真理",
        "悖论",
    ],
}

SUBHINT_KEYWORDS: dict[str, list[str]] = {
    "archaeology": ["archaeolog", "excavation", "artifact", "tomb", "site", "遗址", "文物", "考古"],
    "art_history": ["art", "painting", "museum", "renaissance", "baroque", "gallery", "艺术", "美术"],
    "local_regional": ["local", "regional", "county", "province", "folklore", "地方", "县志", "地域", "乡土"],
    "narrative_fiction": ["novel", "fiction", "story", "character", "tale", "小说", "故事"],
    "genre_history": ["history of", "civilization", "anthropolog", "人类史", "门类", "通史"],
}


def profile_policy(profile_id: str) -> GlossaryProfilePolicy:
    if profile_id not in GLOSSARY_PROFILES:
        raise ValueError(f"Unknown glossary profile: {profile_id!r}")
    return GLOSSARY_PROFILES[profile_id]


def _book_corpus(book: dict[str, Any]) -> str:
    parts: list[str] = []
    metadata = book.get("metadata", {}) if isinstance(book.get("metadata"), dict) else {}
    for key in ("title", "subtitle", "author", "publisher", "series"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    for chapter in book.get("chapters", [])[:3]:
        title = str(chapter.get("title") or "")
        if title:
            parts.append(title)
    for chapter in book.get("chapters", []):
        markdown = str(chapter.get("markdown") or chapter.get("title") or "")
        if markdown:
            parts.append(markdown)
    return "\n".join(parts)


def _count_keyword_hits(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    hits = 0
    for keyword in keywords:
        if keyword.lower() in lowered:
            hits += 1
    return hits


def _detect_subhints(text: str) -> list[str]:
    scored: list[tuple[int, str]] = []
    for name, keywords in SUBHINT_KEYWORDS.items():
        score = _count_keyword_hits(text, keywords)
        if score > 0:
            scored.append((score, name))
    scored.sort(reverse=True)
    return [name for score, name in scored[:3] if score >= 1]


def detect_glossary_profile(book: dict[str, Any], *, corpus: str | None = None) -> dict[str, Any]:
    text = corpus if corpus is not None else _book_corpus(book)
    scores = {profile_id: _count_keyword_hits(text, keywords) for profile_id, keywords in DETECTION_KEYWORDS.items()}

    if re.search(r"\b(19|20)\d{2}\b|\b\d{1,2}(st|nd|rd|th)\s+century\b", text, re.IGNORECASE):
        scores[HUMANITIES_HISTORY] += 5

    table_like = sum(1 for chapter in book.get("chapters", []) if "table" in str(chapter.get("title", "")).lower())
    if table_like >= 2:
        scores[SCIENCE_TECH_ENGINEERING] += 3

    logic_like = _count_keyword_hits(text, DETECTION_KEYWORDS[FORMAL_LOGIC_PHILOSOPHY])
    if logic_like >= 4:
        scores[FORMAL_LOGIC_PHILOSOPHY] += 6
    metadata = book.get("metadata", {}) if isinstance(book.get("metadata"), dict) else {}
    title_blob = " ".join(
        str(metadata.get(key) or "")
        for key in ("title", "subtitle")
    ).lower()
    if any(token in title_blob for token in ("logic", "syntax", "truth", "modal", "paradox", "semantic", "language")):
        scores[FORMAL_LOGIC_PHILOSOPHY] += 8

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_profile, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0

    if top_score <= 0:
        chosen = DEFAULT_GLOSSARY_PROFILE
        confidence = 0.35
    else:
        chosen = top_profile
        margin = (top_score - second_score) / top_score if top_score else 0.0
        if margin < 0.15:
            confidence = 0.45
            chosen = DEFAULT_GLOSSARY_PROFILE
        else:
            confidence = min(0.98, 0.5 + margin * 0.5 + min(top_score, 20) * 0.02)

    subhints = _detect_subhints(text) if chosen == HUMANITIES_HISTORY else []

    return {
        "glossary_profile": chosen,
        "glossary_profile_label": GLOSSARY_PROFILE_LABELS[chosen],
        "glossary_profile_source": "auto",
        "glossary_profile_confidence": round(confidence, 3),
        "glossary_profile_scores": scores,
        "glossary_profile_overridden": False,
        "humanities_subhints": subhints,
    }


def profile_resolution_from_artifacts(
    run_dir_policy: dict[str, Any] | None,
    *,
    explicit_profile: str | None,
) -> tuple[str, str, bool]:
    """Return (profile_id, source, overridden)."""
    if explicit_profile:
        if explicit_profile not in VALID_GLOSSARY_PROFILES:
            raise ValueError(f"Unknown glossary profile: {explicit_profile!r}")
        source = "user" if run_dir_policy else "cli"
        return explicit_profile, source, True

    if run_dir_policy:
        profile = run_dir_policy.get("glossary_profile")
        if isinstance(profile, str) and profile in VALID_GLOSSARY_PROFILES:
            if run_dir_policy.get("glossary_profile_overridden"):
                return profile, "user", True
            return profile, str(run_dir_policy.get("glossary_profile_source") or "auto"), False

    return DEFAULT_GLOSSARY_PROFILE, "auto", False
