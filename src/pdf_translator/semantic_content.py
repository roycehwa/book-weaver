from __future__ import annotations

import hashlib
import re
from typing import Any, Literal


SEMANTIC_CONTENT_SCHEMA = "semantic_content_v1"


class SemanticContentError(ValueError):
    """Raised when semantic content cannot be represented losslessly."""


def _normalize_identity_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_marker(value: str) -> str:
    return re.sub(r"[\s.\])]+$", "", value.strip())


def stable_semantic_id(kind: str, page_no: int, marker: str, text: str) -> str:
    payload = "\x1f".join(
        (
            kind,
            str(page_no),
            _normalize_marker(marker),
            _normalize_identity_text(text),
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{kind}-{digest}"


_CITATION_CUE_RE = re.compile(
    r"(?ix)"
    r"\b(?:"
    r"see(?:\s+also)?|compare|cf\.|for\s+example|for\s+instance|"
    r"among\s+others,?\s+see"
    r")\s*,?\s*"
    r"(?=[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+)"
)
_CITATION_SIGNAL_RE = re.compile(
    r"(?ix)"
    r"(?:\bpp?\.\s*\d|\bvol\.\s*\d|\bno\.\s*\d|\bdoi\s*:|https?://|"
    r"\b[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+\s+"
    r"[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+,\s+"
    r"[A-ZÀ-ÖØ-Þ])"
)
_EXPLANATORY_START_RE = re.compile(
    r"(?i)^(?:for|while|the|this|these|scholars?|among|detailed|data|"
    r"information|an?\s+overview|in\s+this)\b"
)
_TRAILING_EXPLANATION_RE = re.compile(
    r"\.\s+(?=(?:The|This|These|It|They|We|I|Such|Until|Although|However|"
    r"Indeed|Also)\b)"
)


def _has_author_prefix(source_text: str) -> bool:
    prefix, separator, rest = source_text.strip().partition(",")
    return bool(
        separator
        and 1 <= len(prefix.split()) <= 5
        and prefix[:1].isupper()
        and rest.strip()[:1].isupper()
        and not _EXPLANATORY_START_RE.match(prefix)
    )


def _looks_citation_only(source_text: str) -> bool:
    stripped = source_text.strip()
    if len(stripped) >= 2 and stripped[0] in "\"“" and stripped[-1] in "\"”":
        return True
    if _EXPLANATORY_START_RE.match(stripped):
        return False
    if not _has_author_prefix(stripped):
        return False
    return _TRAILING_EXPLANATION_RE.search(stripped) is None


def _span(
    kind: Literal["prose", "citation"],
    source_text: str,
    *,
    page_no: int,
    marker: str,
    index: int,
) -> dict[str, Any]:
    return {
        "span_id": stable_semantic_id(
            f"footnote-{kind}",
            page_no,
            f"{marker}:{index}",
            source_text,
        ),
        "kind": kind,
        "source_text": source_text,
        "translatable": kind == "prose",
    }


def split_note_spans_losslessly(
    source_text: str,
    *,
    page_no: int,
    marker: str,
) -> list[dict[str, Any]]:
    citation_cue = _CITATION_CUE_RE.search(source_text)
    if citation_cue is not None:
        boundary = citation_cue.end()
        spans = [
            _span("prose", source_text[:boundary], page_no=page_no, marker=marker, index=0),
            _span("citation", source_text[boundary:], page_no=page_no, marker=marker, index=1),
        ]
    elif _has_author_prefix(source_text) and (
        trailing_explanation := _TRAILING_EXPLANATION_RE.search(source_text)
    ):
        boundary = trailing_explanation.end()
        spans = [
            _span("citation", source_text[:boundary], page_no=page_no, marker=marker, index=0),
            _span("prose", source_text[boundary:], page_no=page_no, marker=marker, index=1),
        ]
    elif _looks_citation_only(source_text):
        spans = [
            _span("citation", source_text, page_no=page_no, marker=marker, index=0)
        ]
    else:
        spans = [_span("prose", source_text, page_no=page_no, marker=marker, index=0)]

    if "".join(str(span["source_text"]) for span in spans) != source_text:
        raise SemanticContentError("footnote span split was not lossless")
    return spans


def build_semantic_footnote(
    *,
    page_no: int,
    marker: str,
    raw_text: str,
    bbox: list[float] | tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    source_text = raw_text.strip()
    if not source_text:
        raise SemanticContentError("empty footnote cannot be represented")
    normalized_bbox = [float(value) for value in bbox] if bbox is not None else None
    spans = split_note_spans_losslessly(
        source_text,
        page_no=page_no,
        marker=marker,
    )
    return {
        "schema": SEMANTIC_CONTENT_SCHEMA,
        "footnote_id": stable_semantic_id("footnote", page_no, marker, source_text),
        "marker": _normalize_marker(marker),
        "source_page": page_no,
        "source_text": source_text,
        "spans": spans,
        "backlinks": [],
        "source_bboxes": [normalized_bbox] if normalized_bbox is not None else [],
        "confidence": 1.0 if normalized_bbox is not None else 0.8,
    }
