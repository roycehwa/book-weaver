"""Detect books likely to hit LLM content moderation during glossary/translation."""

from __future__ import annotations

import re
from typing import Any

# (phrase, weight) — matched case-insensitively in title + candidate terms
_SENSITIVE_PHRASES: tuple[tuple[str, int], ...] = (
    ("chinese communist party", 4),
    ("chinese communist", 3),
    ("cultural revolution", 4),
    ("hong kong", 3),
    ("taiwan", 3),
    ("xinjiang", 3),
    ("tibet", 2),
    ("mao zedong", 3),
    ("deng xiaoping", 2),
    ("sun yat-sen", 1),
    ("cold war", 2),
    ("secularism", 2),
    ("political discourse", 2),
    ("from revolution", 2),
    ("love hong kong", 4),
    ("communist", 2),
    ("revolution", 1),
    ("religion", 1),
)

_HIGH_RISK_THRESHOLD = 4


def _normalize_blob(*parts: str) -> str:
    return " ".join(part.strip().lower() for part in parts if part and part.strip())


def assess_sensitive_content_risk(
    book: dict[str, Any],
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata = book.get("metadata", {}) if isinstance(book.get("metadata"), dict) else {}
    title_bits = [
        str(metadata.get("title") or ""),
        str(metadata.get("subtitle") or ""),
        str(metadata.get("author") or ""),
    ]
    candidate_terms = [
        str(item.get("source") or "")
        for item in (candidates or [])
        if isinstance(item, dict)
    ][:40]
    blob = _normalize_blob(*title_bits, *candidate_terms)

    matched: list[str] = []
    score = 0
    for phrase, weight in _SENSITIVE_PHRASES:
        if phrase in blob:
            score += weight
            matched.append(phrase)

    level = "high" if score >= _HIGH_RISK_THRESHOLD else "low"
    return {
        "sensitive_content_risk": level,
        "sensitive_content_score": score,
        "sensitive_content_signals": sorted(set(matched)),
    }
