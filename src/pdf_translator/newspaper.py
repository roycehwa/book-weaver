from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MIN_HEADLINE_CHARS = 18
BODY_MIN_CHARS = 120
BRIEFING_MIN_CHARS = 35
HEADLINE_X_GAP = 220.0
HEADLINE_Y_GAP = 220.0
COLUMN_CENTER_GAP = 190.0
HEADLINE_BAND_MARGIN = 40.0
COLUMN_BREAK_GAP = 240.0
SUBHEADLINE_GAP = 340.0

PHOTO_CREDIT_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r"\b[A-Z]{2,}(?:\s+[A-Z]{2,}){0,4}/(?:REUTERS|AP|AFP|GETTY(?: IMAGES)?|BLOOMBERG)\b",
        r"\b[A-Z]{1,6}\s+[A-Z]{2,}(?:\s+[A-Z]{2,}){0,4}/(?:REUTERS|AP|AFP|GETTY(?: IMAGES)?|BLOOMBERG)\b",
    ]
]

NON_EDITORIAL_HEADLINE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\btrustee'?s sale\b",
        r"\bpublic notice\b",
        r"\bforeclosure\b",
        r"\bsupreme court\b",
        r"\bcircuit court\b",
        r"\bdistrict court\b",
        r"washingtonpost\.com/",
        r"\b(?:merchandise|rentals|recruit|roommates|apartments)\b",
        r"\bmetal\s*&\s*petroleum\s*futures\b",
        r"\binterest rate futures\b",
    ]
]

NON_EDITORIAL_BODY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bund(er)? a power of sale\b",
        r"\bdeed of trust\b",
        r"\bsubstitute trustee\b",
        r"\bpublic auction\b",
        r"\bland records\b",
        r"\boriginal principal amount\b",
        r"\bplaintiff\b",
        r"\bdefendants?\b",
        r"\bindex no\.?\b",
        r"\bjudgment of foreclosure\b",
        r"\bplease take notice\b",
        r"\bto place an ad\b",
        r"\bfor recruitment advertisements\b",
        r"\btax deductible\b",
        r"\bpriced to sell\b",
        r"\bbring offers\b",
    ]
]

NAVIGATION_TEXT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"reports? & analysis pages?",
        r"\bopinion\b",
        r"\bpage \d+\b",
        r"\bpages? \d+(?:\s*&\s*\d+)?\b",
        r"\bmarkets page\b",
        r"\bbig read\b",
    ]
]

UTILITY_HEADER_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"^financial times$",
        r"^briefing$",
        r"^world markets$",
        r"^www\.",
        r"^[A-Z][A-Z ]{1,18}$",
    ]
]

NON_ARTICLE_TEXT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"subscribe",
        r"copyright",
        r"printed in",
        r"customer service",
        r"advertising",
        r"executive appointments",
        r"equinor",
        r"page \d+",
    ]
]


@dataclass(slots=True)
class NewsBlock:
    index: int
    label: str
    text: str
    page_no: int
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.top - self.bottom)

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2

    @property
    def area(self) -> float:
        return self.width * self.height


def _resolve_ref(structured: dict[str, Any], ref: str) -> tuple[str, dict[str, Any]] | None:
    if not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    if len(parts) != 2:
        return None
    bucket_name, raw_index = parts
    bucket = structured.get(bucket_name)
    if not isinstance(bucket, list):
        return None
    try:
        index = int(raw_index)
    except ValueError:
        return None
    if index < 0 or index >= len(bucket):
        return None
    return bucket_name, bucket[index]


def _normalize_text(text: str) -> str:
    normalized = text.replace("\n", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _sanitize_inline_credits(text: str) -> str:
    cleaned = _normalize_text(text)
    for pattern in PHOTO_CREDIT_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -:|")


def _is_utility_header(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(pattern.search(normalized) for pattern in UTILITY_HEADER_PATTERNS)


def _is_non_article_text(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(pattern.search(normalized) for pattern in NON_ARTICLE_TEXT_PATTERNS)


def _is_section_banner(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if normalized.startswith("FT BIG READ"):
        return True
    alpha = [char for char in normalized if char.isalpha()]
    if alpha and all(char.isupper() for char in alpha):
        words = normalized.replace(".", " ").replace("&", " ").split()
        if 2 <= len(words) <= 8 and len(normalized) <= 60:
            return True
    return False


def _is_quote_led(text: str) -> bool:
    normalized = _normalize_text(text)
    return normalized.startswith(("'", '"', "‘", "“"))


def _uppercase_ratio(text: str) -> float:
    alpha = [char for char in text if char.isalpha()]
    if not alpha:
        return 0.0
    return sum(1 for char in alpha if char.isupper()) / len(alpha)


def _is_navigation_text(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(pattern.search(normalized) for pattern in NAVIGATION_TEXT_PATTERNS)


def _matches_any(text: str, patterns: list[re.Pattern[str]]) -> bool:
    normalized = _normalize_text(text)
    return any(pattern.search(normalized) for pattern in patterns)


def _is_byline_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if len(normalized) > 90 or "." in normalized:
        return False
    if "-" in normalized or "—" in normalized:
        return False
    if len(normalized.split()) > 12:
        return False
    if re.search(r"\b(page|pages|subscribe|copyright)\b", normalized, re.IGNORECASE):
        return False
    return _uppercase_ratio(normalized) >= 0.6


def _is_dateline_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if len(normalized) > 48:
        return False
    if _is_navigation_text(normalized):
        return False
    if "-" not in normalized and "—" not in normalized:
        return False
    return _uppercase_ratio(normalized) >= 0.35


def _extract_news_blocks(structured: dict[str, Any]) -> list[NewsBlock]:
    blocks: list[NewsBlock] = []
    children = structured.get("body", {}).get("children", [])
    for child in children:
        ref = child.get("$ref") if isinstance(child, dict) else None
        if not ref:
            continue
        resolved = _resolve_ref(structured, ref)
        if not resolved:
            continue
        bucket_name, item = resolved
        if bucket_name != "texts":
            continue

        prov = item.get("prov") or []
        if not prov:
            continue
        first_prov = prov[0]
        bbox = first_prov.get("bbox") or {}
        text = _normalize_text(item.get("text", ""))
        if not text:
            continue

        blocks.append(
            NewsBlock(
                index=len(blocks),
                label=item.get("label", "text"),
                text=text,
                page_no=int(first_prov.get("page_no", 0)),
                left=float(bbox.get("l", 0.0)),
                top=float(bbox.get("t", 0.0)),
                right=float(bbox.get("r", 0.0)),
                bottom=float(bbox.get("b", 0.0)),
            )
        )
    return blocks


def _page_sizes(source_pdf: Path) -> dict[int, tuple[float, float]]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(source_pdf))
    sizes: dict[int, tuple[float, float]] = {}
    for index in range(len(document)):
        sizes[index + 1] = document[index].get_size()
    return sizes


def _is_headline(block: NewsBlock) -> bool:
    if block.label != "section_header":
        return False
    if len(block.text) < MIN_HEADLINE_CHARS:
        return False
    if (
        _is_utility_header(block.text)
        or _is_non_article_text(block.text)
        or _is_section_banner(block.text)
    ):
        return False
    return True


def _is_briefing_header(block: NewsBlock) -> bool:
    return block.label == "section_header" and _normalize_text(block.text).lower() == "briefing"


def _build_briefing_articles(blocks: list[NewsBlock], page_width: float) -> list[dict[str, Any]]:
    if not blocks:
        return []
    briefing_articles: list[dict[str, Any]] = []
    briefing_headers = [block for block in blocks if _is_briefing_header(block)]
    if not briefing_headers:
        return briefing_articles

    for header in briefing_headers:
        briefing_items = [
            block
            for block in blocks
            if block.left >= header.left - 20
            and block.top < header.top
            and block.center_x > page_width * 0.72
            and block.label in {"list_item", "text", "section_header"}
        ]
        for item in briefing_items:
            if item.label == "section_header" and item.index == header.index:
                continue
            if _is_non_article_text(item.text):
                continue
            headline = item.text.split("  ")[0].split("—")[0].strip("· ").strip()
            if len(headline) < 10:
                headline = item.text[:80]
            briefing_articles.append(
                {
                    "page_start": item.page_no,
                    "headline": headline,
                    "deck": None,
                    "byline": None,
                    "dateline": None,
                    "body_text": item.text,
                    "body_chars": len(item.text),
                    "block_indexes": [item.index],
                    "article_type": "briefing_item",
                    "quality": _article_quality(
                        headline=headline,
                        deck=None,
                        body_text=item.text,
                        byline=None,
                        dateline=None,
                    ),
                    "score": 20.0,
                }
            )
    return briefing_articles


def _headline_distance(anchor: NewsBlock, block: NewsBlock) -> tuple[float, float]:
    return abs(anchor.center_x - block.center_x), anchor.top - block.top


def _horizontal_overlap_ratio(left_a: float, right_a: float, left_b: float, right_b: float) -> float:
    overlap = max(0.0, min(right_a, right_b) - max(left_a, left_b))
    width = max(1.0, min(right_a - left_a, right_b - left_b))
    return overlap / width


def _is_story_block_candidate(block: NewsBlock, headline: NewsBlock, page_width: float) -> bool:
    if block.label in {"page_header", "page_footer", "caption"}:
        return False
    if _is_briefing_header(block) or _is_utility_header(block.text):
        return False
    if block.center_x < headline.left - HEADLINE_BAND_MARGIN:
        return False
    if block.center_x > headline.right + HEADLINE_BAND_MARGIN:
        return False
    if headline.width < page_width * 0.42 and abs(headline.center_x - block.center_x) > HEADLINE_X_GAP:
        return False
    overlap = _horizontal_overlap_ratio(headline.left, headline.right, block.left, block.right)
    if overlap <= 0 and abs(headline.center_x - block.center_x) > HEADLINE_X_GAP:
        return False
    return True


def _assign_blocks_to_headlines(
    page_blocks: list[NewsBlock],
    headlines: list[NewsBlock],
    page_width: float,
) -> dict[int, list[NewsBlock]]:
    assignments: dict[int, list[NewsBlock]] = {headline.index: [] for headline in headlines}
    sorted_headlines = sorted(headlines, key=lambda block: (-block.top, block.left))

    for block in page_blocks:
        if block.index in assignments:
            continue
        if _is_non_article_text(block.text):
            continue

        best_headline: NewsBlock | None = None
        best_key: tuple[float, float] | None = None
        for headline in sorted_headlines:
            if not _is_story_block_candidate(block, headline, page_width):
                continue
            horizontal_gap, vertical_gap = _headline_distance(headline, block)
            overlap = _horizontal_overlap_ratio(headline.left, headline.right, block.left, block.right)
            if vertical_gap < -HEADLINE_Y_GAP:
                continue
            if vertical_gap > 0 and (
                horizontal_gap <= HEADLINE_X_GAP
                or overlap >= 0.35
                or headline.width >= page_width * 0.55
            ):
                proximity_penalty = 0.0 if overlap >= 0.35 else horizontal_gap
                key = (proximity_penalty, vertical_gap)
            else:
                continue

            if best_key is None or key < best_key:
                best_headline = headline
                best_key = key

        if best_headline is not None:
            assignments[best_headline.index].append(block)

    return assignments


def _group_headlines(headlines: list[NewsBlock], page_width: float) -> tuple[list[NewsBlock], dict[int, list[NewsBlock]]]:
    primary: list[NewsBlock] = []
    dependents: dict[int, list[NewsBlock]] = {}
    sorted_headlines = sorted(headlines, key=lambda block: (-block.top, block.left))

    for headline in sorted_headlines:
        parent: NewsBlock | None = None
        for candidate in primary:
            vertical_gap = candidate.bottom - headline.top
            overlap = _horizontal_overlap_ratio(candidate.left, candidate.right, headline.left, headline.right)
            if vertical_gap < 0 or vertical_gap > SUBHEADLINE_GAP:
                continue
            if overlap < 0.35 and abs(candidate.center_x - headline.center_x) > HEADLINE_X_GAP:
                continue
            if candidate.width <= headline.width * 1.2:
                continue
            if candidate.width < page_width * 0.4 and headline.width > page_width * 0.28:
                continue
            parent = candidate
            break

        if parent is None:
            primary.append(headline)
            dependents.setdefault(headline.index, [])
        else:
            dependents.setdefault(parent.index, []).append(headline)

    return primary, dependents


def _order_blocks_for_reading(blocks: list[NewsBlock]) -> list[NewsBlock]:
    if not blocks:
        return []

    columns: list[dict[str, Any]] = []
    for block in sorted(blocks, key=lambda item: (item.center_x, -item.top)):
        chosen: dict[str, Any] | None = None
        best_gap: float | None = None
        for column in columns:
            gap = abs(block.center_x - column["center_x"])
            if gap <= COLUMN_CENTER_GAP and (best_gap is None or gap < best_gap):
                chosen = column
                best_gap = gap
        if chosen is None:
            chosen = {"blocks": [], "center_x": block.center_x}
            columns.append(chosen)
        chosen["blocks"].append(block)
        chosen["center_x"] = sum(item.center_x for item in chosen["blocks"]) / len(chosen["blocks"])

    ordered: list[NewsBlock] = []
    for column in sorted(columns, key=lambda item: item["center_x"]):
        ordered.extend(sorted(column["blocks"], key=lambda item: (-item.top, item.left)))
    return ordered


def _trim_column_breaks(blocks: list[NewsBlock]) -> list[NewsBlock]:
    if not blocks:
        return []

    columns: list[dict[str, Any]] = []
    for block in sorted(blocks, key=lambda item: (item.center_x, -item.top)):
        chosen: dict[str, Any] | None = None
        best_gap: float | None = None
        for column in columns:
            gap = abs(block.center_x - column["center_x"])
            if gap <= COLUMN_CENTER_GAP and (best_gap is None or gap < best_gap):
                chosen = column
                best_gap = gap
        if chosen is None:
            chosen = {"blocks": [], "center_x": block.center_x}
            columns.append(chosen)
        chosen["blocks"].append(block)
        chosen["center_x"] = sum(item.center_x for item in chosen["blocks"]) / len(chosen["blocks"])

    kept: list[NewsBlock] = []
    for column in columns:
        ordered = sorted(column["blocks"], key=lambda item: (-item.top, item.left))
        contiguous: list[NewsBlock] = []
        previous: NewsBlock | None = None
        for block in ordered:
            if previous is not None:
                gap = previous.bottom - block.top
                if gap > COLUMN_BREAK_GAP:
                    break
            contiguous.append(block)
            previous = block
        kept.extend(contiguous)
    return kept


def _extract_front_matter(
    headline: NewsBlock,
    dependent_headlines: list[NewsBlock],
    assigned_blocks: list[NewsBlock],
) -> tuple[str | None, str | None, str | None, list[NewsBlock]]:
    if not assigned_blocks and not dependent_headlines:
        return None, None, None, []

    ordered_dependents = sorted(dependent_headlines, key=lambda block: (-block.top, block.left))
    dependent_indexes = {block.index for block in ordered_dependents}
    deck_parts = [block.text for block in ordered_dependents if len(block.text) <= 220]
    byline: str | None = None
    dateline: str | None = None
    remaining: list[NewsBlock] = []
    body_started = False

    for block in assigned_blocks:
        if block.index in dependent_indexes:
            continue
        if _is_navigation_text(block.text):
            continue
        if not body_started and _is_dateline_text(block.text):
            dateline = dateline or block.text
            continue
        if not body_started and _is_byline_text(block.text):
            byline = byline or block.text
            continue
        if (
            not body_started
            and block.label == "text"
            and len(block.text) <= 140
            and (
                block.width >= headline.width * 0.45
                or block.top >= headline.bottom - 120
            )
        ):
            deck_parts.append(block.text)
            continue
        body_started = True
        remaining.append(block)

    deck = " ".join(part.strip() for part in deck_parts if part.strip()) or None
    return deck, byline, dateline, remaining


def _is_closed_paragraph(text: str) -> bool:
    return bool(text) and text[-1] in '.!?"”’\']'


def _trim_fragment_edges(blocks: list[NewsBlock]) -> list[NewsBlock]:
    trimmed = list(blocks)
    while trimmed and (_is_navigation_text(trimmed[-1].text) or (len(trimmed[-1].text) < 80 and not _is_closed_paragraph(trimmed[-1].text))):
        trimmed.pop()
    while trimmed and (_is_navigation_text(trimmed[0].text) or _is_byline_text(trimmed[0].text) or _is_dateline_text(trimmed[0].text)):
        trimmed.pop(0)
    return trimmed


def _article_quality(
    *,
    headline: str,
    deck: str | None,
    body_text: str,
    byline: str | None,
    dateline: str | None,
) -> dict[str, Any]:
    paragraphs = [paragraph.strip() for paragraph in body_text.split("\n\n") if paragraph.strip()]
    warnings: list[str] = []
    score = 100

    if not paragraphs:
        return {"score": 0, "grade": "low", "warnings": ["empty_body"]}

    first = paragraphs[0]
    last = paragraphs[-1]

    if first[:1].islower():
        warnings.append("starts_mid_sentence")
        score -= 18
    if len(first.split()) < 5:
        warnings.append("weak_lead")
        score -= 10
    if not _is_closed_paragraph(last):
        warnings.append("truncated_tail")
        score -= 16
    if any(len(paragraph.split()) < 4 for paragraph in paragraphs):
        warnings.append("fragment_paragraph")
        score -= 8
    if len(body_text) < 900:
        warnings.append("thin_body")
        score -= 10
    if byline is None:
        warnings.append("missing_byline")
        score -= 4
    if dateline is None and headline and len(headline) > 40:
        warnings.append("missing_dateline")
        score -= 4
    if deck is None and len(headline) > 32:
        warnings.append("missing_deck")
        score -= 3

    score = max(0, min(100, score))
    grade = "high" if score >= 80 else "medium" if score >= 60 else "low"
    return {"score": score, "grade": grade, "warnings": warnings}


def _is_non_editorial_article(*, headline: str, deck: str | None, body_text: str, page_start: int) -> bool:
    excerpt = _normalize_text(body_text[:1600])
    signal_count = 0

    if _matches_any(headline, NON_EDITORIAL_HEADLINE_PATTERNS):
        signal_count += 2
    if deck and _matches_any(deck, NON_EDITORIAL_HEADLINE_PATTERNS):
        signal_count += 1
    if _matches_any(excerpt, NON_EDITORIAL_BODY_PATTERNS):
        signal_count += 2
    if re.search(r"\b(?:call|tel)\s*[:\-]?\s*\(?\d{3}\)?", excerpt, re.IGNORECASE):
        signal_count += 1
    if excerpt.count("www.") >= 1 or excerpt.count("@") >= 1:
        signal_count += 1

    # Be more aggressive on late newspaper pages, where classifieds and notices cluster.
    if page_start >= 20 and signal_count >= 2:
        return True
    return signal_count >= 3


def _article_score(*, page_no: int, total_pages: int, headline: str, body_chars: int, article_type: str) -> float:
    front_page_bonus = max(0.0, 30.0 - (page_no - 1) * 1.25)
    body_bonus = min(35.0, body_chars / 180.0)
    headline_bonus = min(20.0, len(headline) / 6.0)
    type_bonus = 0.0
    if article_type == "main_story":
        type_bonus = 15.0
    elif article_type == "secondary_story":
        type_bonus = 8.0
    elif article_type == "briefing_item":
        type_bonus = -10.0
    penalty = 0.0
    if body_chars < 900:
        penalty += 18.0
    elif body_chars < 1500:
        penalty += 8.0
    if page_no <= 2 and body_chars < 1200:
        penalty += 10.0
    if _is_quote_led(headline):
        penalty += 10.0 if body_chars < 3200 else 4.0
    return round(front_page_bonus + body_bonus + headline_bonus + type_bonus - penalty, 2)


def _keep_article(article: dict[str, Any]) -> bool:
    headline = article["headline"]
    body_chars = article["body_chars"]
    article_type = article["article_type"]
    if not headline:
        return False
    if _is_non_article_text(headline) or _is_section_banner(headline):
        return False
    if _is_non_editorial_article(
        headline=headline,
        deck=article.get("deck"),
        body_text=article["body_text"],
        page_start=article["page_start"],
    ):
        return False
    if article_type == "briefing_item":
        return body_chars >= BRIEFING_MIN_CHARS
    if body_chars < BODY_MIN_CHARS:
        return False
    if body_chars < 260 and article["page_start"] >= 15:
        return False
    if _is_quote_led(headline) and body_chars < 2200:
        return False
    if article["page_start"] <= 2 and body_chars < 900:
        return False
    return True


def extract_newspaper_articles(structured: dict[str, Any], source_pdf: Path) -> dict[str, Any]:
    blocks = _extract_news_blocks(structured)
    page_sizes = _page_sizes(source_pdf)
    total_pages = len(page_sizes)
    articles: list[dict[str, Any]] = []

    for page_no in range(1, total_pages + 1):
        page_width = page_sizes[page_no][0]
        page_blocks = [block for block in blocks if block.page_no == page_no]
        headline_candidates = [block for block in page_blocks if _is_headline(block)]
        headlines, dependent_headlines = _group_headlines(headline_candidates, page_width)
        assignments = _assign_blocks_to_headlines(page_blocks, headlines, page_width)

        for headline in headlines:
            top_ordered_blocks = sorted(
                assignments.get(headline.index, []),
                key=lambda block: (-block.top, block.left),
            )
            deck, byline, dateline, body_blocks = _extract_front_matter(
                headline,
                dependent_headlines.get(headline.index, []),
                top_ordered_blocks,
            )
            body_blocks = _trim_column_breaks(body_blocks)
            body_blocks = _order_blocks_for_reading(body_blocks)
            body_blocks = _trim_fragment_edges(body_blocks)
            body_text = "\n\n".join(block.text for block in body_blocks if not _is_utility_header(block.text))
            cleaned_headline = _sanitize_inline_credits(headline.text)
            cleaned_deck = _sanitize_inline_credits(deck) if deck else None
            cleaned_body_text = "\n\n".join(_sanitize_inline_credits(block.text) for block in body_blocks if not _is_utility_header(block.text))
            body_chars = len(body_text)
            article_type = "main_story" if headline.width >= page_width * 0.38 or headline.top > 2800 else "secondary_story"
            quality = _article_quality(
                headline=cleaned_headline,
                deck=cleaned_deck,
                body_text=cleaned_body_text,
                byline=byline,
                dateline=dateline,
            )
            articles.append(
                {
                    "page_start": page_no,
                    "headline": cleaned_headline,
                    "deck": cleaned_deck,
                    "byline": byline,
                    "dateline": dateline,
                    "body_text": cleaned_body_text,
                    "body_chars": len(cleaned_body_text),
                    "block_indexes": [headline.index] + [block.index for block in body_blocks],
                    "article_type": article_type,
                    "quality": quality,
                    "score": _article_score(
                        page_no=page_no,
                        total_pages=total_pages,
                        headline=cleaned_headline,
                        body_chars=len(cleaned_body_text),
                        article_type=article_type,
                    ),
                }
            )

        articles.extend(_build_briefing_articles(page_blocks, page_width))

    filtered_articles = [article for article in articles if _keep_article(article)]
    ranked_articles = sorted(
        filtered_articles,
        key=lambda article: (-article["score"], article["page_start"], article["headline"]),
    )
    top_count = max(1, round(len(ranked_articles) / 2)) if ranked_articles else 0
    quality_summary = {
        "high": sum(1 for article in ranked_articles if article.get("quality", {}).get("grade") == "high"),
        "medium": sum(1 for article in ranked_articles if article.get("quality", {}).get("grade") == "medium"),
        "low": sum(1 for article in ranked_articles if article.get("quality", {}).get("grade") == "low"),
    }

    return {
        "source_pdf": str(source_pdf),
        "total_pages": total_pages,
        "article_count": len(ranked_articles),
        "selected_top_half_count": top_count,
        "quality_summary": quality_summary,
        "articles": ranked_articles,
        "selected_article_indexes": list(range(top_count)),
    }


def write_newspaper_articles(structured: dict[str, Any], source_pdf: Path, output_path: Path) -> dict[str, Any]:
    result = extract_newspaper_articles(structured, source_pdf)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
