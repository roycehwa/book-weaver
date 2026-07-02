from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable, Literal


OcrDisposition = Literal["reading", "review", "suspect_ocr"]


@dataclass(frozen=True)
class OcrAssessment:
    disposition: OcrDisposition
    score: float
    reason_codes: tuple[str, ...]
    raw_text: str
    page_no: int
    bbox: tuple[float, float, float, float] | None


_WEIGHTS = {
    "control_character_density": 0.65,
    "symbol_density": 0.25,
    "fragmented_tokens": 0.25,
    "impossible_word_join": 0.55,
    "out_of_page_bbox": 0.55,
    "figure_overlap": 0.25,
    "table_overlap": 0.25,
    "header_overlap": 0.15,
    "footer_overlap": 0.20,
    "scan_artifact_overlap": 0.25,
    "evidence_disagreement": 0.45,
}


def _normalize_bbox(
    bbox: Iterable[float] | None,
) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    values = tuple(float(value) for value in bbox)
    if len(values) != 4:
        raise ValueError("bbox must contain exactly four coordinates")
    return values


def _collect_reason_codes(
    text: str,
    *,
    bbox: tuple[float, float, float, float] | None,
    page_size: tuple[float, float],
    overlaps: set[str],
    evidence_text: str | None,
) -> set[str]:
    reasons: set[str] = set()
    length = max(len(text), 1)
    controls = sum(
        unicodedata.category(char) == "Cc" and char not in "\n\r\t" for char in text
    )
    if controls / length >= 0.02:
        reasons.add("control_character_density")

    symbols = sum(
        not char.isalnum() and not char.isspace() and char not in "'’.,;:!?-–—()[]"
        for char in text
    )
    punctuation = sum(
        not char.isalnum() and not char.isspace() for char in text
    )
    if symbols / length >= 0.06 or punctuation / length >= 0.20:
        reasons.add("symbol_density")

    tokens = text.split()
    fragmented = sum(
        bool(re.fullmatch(r"[\d:/.-]{1,8}", token)) for token in tokens
    )
    if tokens and fragmented / len(tokens) >= 0.45:
        reasons.add("fragmented_tokens")

    if re.search(r"\b(?:of|and|the|in|to|for)[A-Z][a-z]{2,}", text):
        reasons.add("impossible_word_join")

    if bbox is not None:
        x0, y0, x1, y1 = bbox
        width, height = page_size
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x0 >= x1 or y0 >= y1:
            reasons.add("out_of_page_bbox")

    for overlap in overlaps:
        key = f"{overlap}_overlap"
        if key in _WEIGHTS:
            reasons.add(key)

    if evidence_text is not None:
        extracted = set(re.findall(r"\w+", text.casefold()))
        evidence = set(re.findall(r"\w+", evidence_text.casefold()))
        union = extracted | evidence
        agreement = len(extracted & evidence) / len(union) if union else 1.0
        if agreement < 0.25:
            reasons.add("evidence_disagreement")
    return reasons


def assess_ocr_block(
    text: str,
    *,
    page_no: int,
    bbox: Iterable[float] | None = None,
    page_size: tuple[float, float] = (595.0, 842.0),
    overlaps: set[str] | frozenset[str] = frozenset(),
    evidence_text: str | None = None,
) -> OcrAssessment:
    normalized_bbox = _normalize_bbox(bbox)
    reasons = _collect_reason_codes(
        text,
        bbox=normalized_bbox,
        page_size=page_size,
        overlaps=set(overlaps),
        evidence_text=evidence_text,
    )
    score = min(1.0, sum(_WEIGHTS[reason] for reason in reasons))
    disposition: OcrDisposition
    if score >= 0.82:
        disposition = "suspect_ocr"
    elif score >= 0.55:
        disposition = "review"
    else:
        disposition = "reading"
    return OcrAssessment(
        disposition=disposition,
        score=round(score, 4),
        reason_codes=tuple(sorted(reasons)),
        raw_text=text,
        page_no=page_no,
        bbox=normalized_bbox,
    )
