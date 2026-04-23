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
        r"\bcommon sense media\b",
        r"\bwhat parents need to know\b",
        r"\bfuneral services directory\b",
        r"\bdeath notices\b",
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
        r"\bterms of sale\b",
        r"\bcertified check\b",
        r"\bcashier'?s check\b",
        r"\bratification of sale\b",
        r"\bhoa assessments?\b",
        r"\bavailable in theaters\b",
        r"\bage \d{1,2}\+\b",
        r"\bfuneral services directory\b",
        r"\bmuseum\.[a-z]+\b",
    ]
]

LISTING_FRAGMENT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bW\s+L\b",
        r"\bR\s+H\s+E\b",
        r"\bR\s+H\s+BI\b",
        r"\bIP\s+H\s+R(?:ER)?\b",
        r"\bWP:\b",
        r"\bLP:\b",
        r"\bERA\b",
        r"\bshowtimes?\b",
        r"\bin theaters\b",
        r"\bstandings\b",
        r"\bschedule\b",
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
    if _is_quote_led(block.text) and len(block.text) <= 110:
        return False
    if (
        _is_utility_header(block.text)
        or _is_non_article_text(block.text)
        or _is_section_banner(block.text)
    ):
        return False
    if _looks_like_fragmented_headline(block.text):
        return False
    return True


def _looks_like_fragmented_headline(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return True
    if normalized.startswith(("*", ",")):
        return True
    if normalized[:1].islower():
        return True
    if normalized.startswith("BY ") and len(normalized) > 70:
        return True
    if len(normalized) > 120:
        return True
    if normalized.count(".") >= 2 and len(normalized) > 70:
        return True
    if normalized.count('"') >= 2 and len(normalized) > 90:
        return True
    return False


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


def _belongs_to_briefing_rail(
    block: NewsBlock,
    briefing_headers: list[NewsBlock],
    page_width: float,
) -> bool:
    if block.label not in {"list_item", "text", "section_header"}:
        return False
    for header in briefing_headers:
        if block.index == header.index:
            continue
        if block.left < header.left - 20:
            continue
        if block.top >= header.top:
            continue
        if block.center_x <= page_width * 0.72:
            continue
        return True
    return False


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
    is_wide_headline = headline.width >= page_width * 0.55
    if not is_wide_headline:
        if block.center_x < headline.left - HEADLINE_BAND_MARGIN:
            return False
        if block.center_x > headline.right + HEADLINE_BAND_MARGIN:
            return False
    if headline.width < page_width * 0.42 and abs(headline.center_x - block.center_x) > HEADLINE_X_GAP:
        return False
    overlap = _horizontal_overlap_ratio(headline.left, headline.right, block.left, block.right)
    if not is_wide_headline and overlap <= 0 and abs(headline.center_x - block.center_x) > HEADLINE_X_GAP:
        return False
    return True


def _assign_blocks_to_headlines(
    page_blocks: list[NewsBlock],
    headlines: list[NewsBlock],
    page_width: float,
    briefing_headers: list[NewsBlock] | None = None,
) -> dict[int, list[NewsBlock]]:
    assignments: dict[int, list[NewsBlock]] = {headline.index: [] for headline in headlines}
    sorted_headlines = sorted(headlines, key=lambda block: (-block.top, block.left))
    next_headline_top: dict[int, float | None] = {}
    for position, headline in enumerate(sorted_headlines):
        next_headline_top[headline.index] = (
            sorted_headlines[position + 1].top if position + 1 < len(sorted_headlines) else None
        )
    briefing_headers = briefing_headers or []

    for block in page_blocks:
        if block.index in assignments:
            continue
        if _is_non_article_text(block.text):
            continue
        if _belongs_to_briefing_rail(block, briefing_headers, page_width):
            continue

        best_headline: NewsBlock | None = None
        best_key: tuple[float, float] | None = None
        for headline in sorted_headlines:
            cutoff_top = next_headline_top.get(headline.index)
            if cutoff_top is not None and block.top <= cutoff_top:
                continue
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


def _column_assignment_gap(blocks: list[NewsBlock]) -> float:
    widths = sorted(block.width for block in blocks if block.width > 0)
    if not widths:
        return 60.0
    median_width = widths[len(widths) // 2]
    return min(110.0, max(38.0, median_width * 0.45))


def _group_blocks_into_columns(blocks: list[NewsBlock]) -> list[dict[str, Any]]:
    if not blocks:
        return []

    assignment_gap = _column_assignment_gap(blocks)
    columns: list[dict[str, Any]] = []
    for block in sorted(blocks, key=lambda item: (item.left, -item.top)):
        chosen: dict[str, Any] | None = None
        best_key: tuple[int, float, float] | None = None
        for column in columns:
            overlap = _horizontal_overlap_ratio(column["left"], column["right"], block.left, block.right)
            left_gap = abs(block.left - column["left"])
            right_gap = abs(block.right - column["right"])
            if overlap < 0.35 and (left_gap > assignment_gap or right_gap > assignment_gap * 1.5):
                continue
            key = (0 if overlap >= 0.35 else 1, min(left_gap, right_gap), left_gap + right_gap)
            if best_key is None or key < best_key:
                chosen = column
                best_key = key
        if chosen is None:
            chosen = {"blocks": [], "left": block.left, "right": block.right}
            columns.append(chosen)
        chosen["blocks"].append(block)
        chosen["left"] = sum(item.left for item in chosen["blocks"]) / len(chosen["blocks"])
        chosen["right"] = sum(item.right for item in chosen["blocks"]) / len(chosen["blocks"])

    return sorted(columns, key=lambda item: item["left"])


def _order_blocks_for_reading(blocks: list[NewsBlock]) -> list[NewsBlock]:
    if not blocks:
        return []

    ordered: list[NewsBlock] = []
    for column in _group_blocks_into_columns(blocks):
        ordered.extend(sorted(column["blocks"], key=lambda item: (-item.top, item.left)))
    return ordered


def _trim_column_breaks(blocks: list[NewsBlock]) -> list[NewsBlock]:
    if not blocks:
        return []

    kept: list[NewsBlock] = []
    for column in _group_blocks_into_columns(blocks):
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

    if _looks_like_listing_fragment(headline=headline, deck=deck, body_text=body_text):
        signal_count += 3

    # Be more aggressive on late newspaper pages, where classifieds and notices cluster.
    if page_start >= 20 and signal_count >= 2:
        return True
    return signal_count >= 3


def _looks_like_listing_fragment(*, headline: str, deck: str | None, body_text: str) -> bool:
    combined = " ".join(
        part for part in [_normalize_text(headline), _normalize_text(deck or ""), _normalize_text(body_text)] if part
    )
    if not combined:
        return False

    paragraphs = [paragraph.strip() for paragraph in body_text.split("\n\n") if paragraph.strip()]
    paragraph_count = len(paragraphs)
    short_paragraph_count = sum(1 for paragraph in paragraphs if len(paragraph.split()) <= 4)
    short_para_ratio = short_paragraph_count / paragraph_count if paragraph_count else 0.0

    tokens = re.findall(r"\S+", body_text)
    token_count = len(tokens)
    digit_token_count = sum(1 for token in tokens if any(char.isdigit() for char in token))
    digit_ratio = digit_token_count / token_count if token_count else 0.0

    dot_leader_count = len(re.findall(r"\.{4,}", combined))
    time_count = len(re.findall(r"\b\d{1,2}:\d{2}\b", combined))
    calendar_marker_count = len(
        re.findall(
            r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\b|"
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\b",
            combined,
            re.IGNORECASE,
        )
    )
    listing_pattern_hits = sum(1 for pattern in LISTING_FRAGMENT_PATTERNS if pattern.search(combined))

    headline_dot_leader = bool(re.search(r"\.{4,}", headline))
    headline_starts_with_date = bool(
        re.match(
            r"^\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\b|"
            r"^\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:[-/]\d{1,2})?",
            headline,
            re.IGNORECASE,
        )
    )
    headline_alpha_count = sum(1 for char in headline if char.isalpha())
    headline_nonalpha_count = sum(1 for char in headline if not char.isspace() and not char.isalpha())

    signal_count = 0
    if dot_leader_count >= 6:
        signal_count += 2
    if digit_ratio >= 0.40:
        signal_count += 2
    elif digit_ratio >= 0.28 and paragraph_count >= 6:
        signal_count += 1
    if digit_ratio >= 0.35 and short_para_ratio >= 0.20 and paragraph_count >= 5:
        signal_count += 2
    if short_paragraph_count >= 14:
        signal_count += 2
    elif short_para_ratio >= 0.45 and paragraph_count >= 8:
        signal_count += 2
    if time_count >= 5:
        signal_count += 2
    elif time_count >= 3 and digit_ratio >= 0.25:
        signal_count += 1
    if calendar_marker_count >= 8 and paragraph_count >= 6:
        signal_count += 1
    if calendar_marker_count >= 6 and time_count >= 2:
        signal_count += 2
    if headline_starts_with_date and len(body_text) < 2200:
        signal_count += 2
    if listing_pattern_hits >= 2:
        signal_count += 2
    elif listing_pattern_hits == 1:
        signal_count += 1
    if headline_dot_leader:
        signal_count += 2
    if len(headline) >= 24 and headline_nonalpha_count > headline_alpha_count:
        signal_count += 1

    if signal_count >= 4:
        return True
    return signal_count >= 3 and paragraph_count >= 8 and len(body_text) < 2600


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


def _quality_score_penalty(quality: dict[str, Any], page_no: int) -> float:
    grade = quality.get("grade")
    warnings = set(quality.get("warnings", []))
    penalty = 0.0
    if grade == "medium":
        penalty += 6.0
    elif grade == "low":
        penalty += 22.0
    if "starts_mid_sentence" in warnings:
        penalty += 20.0
    if "truncated_tail" in warnings:
        penalty += 10.0
    if "fragment_paragraph" in warnings:
        penalty += 8.0
    if "weak_lead" in warnings:
        penalty += 6.0
    if page_no >= 15 and grade == "low":
        penalty += 8.0
    return penalty


def _page_fragment_metrics(
    *,
    page_blocks: list[NewsBlock],
    headline_candidates: list[NewsBlock],
    page_width: float,
) -> dict[str, Any]:
    text_blocks = [
        block
        for block in page_blocks
        if block.label in {"text", "list_item", "section_header"} and not _is_utility_header(block.text)
    ]
    if not text_blocks:
        return {
            "block_count": 0,
            "short_ratio": 0.0,
            "digit_heavy_ratio": 0.0,
            "dot_blocks": 0,
            "time_blocks": 0,
            "headline_count": len(headline_candidates),
            "wide_headlines": 0,
            "big_blocks": 0,
        }

    token_lists = [re.findall(r"\S+", block.text) for block in text_blocks]
    block_count = len(token_lists)
    short_blocks = sum(1 for tokens in token_lists if len(tokens) <= 6)
    digit_heavy_blocks = 0
    for tokens in token_lists:
        if not tokens:
            continue
        digit_ratio = sum(1 for token in tokens if any(char.isdigit() for char in token)) / len(tokens)
        if digit_ratio >= 0.35:
            digit_heavy_blocks += 1

    dot_blocks = sum(1 for block in text_blocks if re.search(r"\.{4,}", block.text))
    time_blocks = sum(1 for block in text_blocks if re.search(r"\b\d{1,2}:\d{2}\b", block.text))
    big_blocks = sum(1 for tokens in token_lists if len(tokens) >= 80)
    wide_headlines = sum(1 for headline in headline_candidates if headline.width >= page_width * 0.34)

    return {
        "block_count": block_count,
        "short_ratio": short_blocks / block_count,
        "digit_heavy_ratio": digit_heavy_blocks / block_count,
        "dot_blocks": dot_blocks,
        "time_blocks": time_blocks,
        "headline_count": len(headline_candidates),
        "wide_headlines": wide_headlines,
        "big_blocks": big_blocks,
    }


def _page_skip_reason(metrics: dict[str, Any]) -> str | None:
    block_count = metrics["block_count"]
    short_ratio = metrics["short_ratio"]
    digit_heavy_ratio = metrics["digit_heavy_ratio"]
    dot_blocks = metrics["dot_blocks"]
    time_blocks = metrics["time_blocks"]
    headline_count = metrics["headline_count"]
    wide_headlines = metrics["wide_headlines"]
    big_blocks = metrics["big_blocks"]

    if (
        block_count >= 180
        and short_ratio >= 0.75
        and (digit_heavy_ratio >= 0.35 or dot_blocks >= 10 or time_blocks >= 20)
        and wide_headlines <= 1
    ):
        return "dense_fragment_page"
    if block_count >= 500 and short_ratio >= 0.88 and digit_heavy_ratio >= 0.45 and wide_headlines <= 1:
        return "numeric_grid_page"
    if block_count >= 220 and short_ratio >= 0.80 and wide_headlines == 0 and big_blocks <= 2:
        return "no_story_high_fragment_page"
    if block_count >= 30 and short_ratio >= 0.96 and headline_count <= 1 and big_blocks == 0:
        return "tiny_entries_page"
    return None


def _keep_article(article: dict[str, Any]) -> bool:
    headline = article["headline"]
    body_chars = article["body_chars"]
    article_type = article["article_type"]
    quality = article.get("quality", {})
    warnings = set(quality.get("warnings", []))
    if not headline:
        return False
    if _is_non_article_text(headline) or _is_section_banner(headline):
        return False
    if _looks_like_fragmented_headline(headline):
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
    if "starts_mid_sentence" in warnings and article["page_start"] >= 8:
        return False
    if quality.get("grade") == "low" and article["page_start"] >= 20:
        return False
    return True


def extract_newspaper_articles(structured: dict[str, Any], source_pdf: Path) -> dict[str, Any]:
    blocks = _extract_news_blocks(structured)
    page_sizes = _page_sizes(source_pdf)
    total_pages = len(page_sizes)
    articles: list[dict[str, Any]] = []
    skipped_pages: list[dict[str, Any]] = []
    processed_page_count = 0

    for page_no in range(1, total_pages + 1):
        page_width = page_sizes[page_no][0]
        page_blocks = [block for block in blocks if block.page_no == page_no]
        headline_candidates = [block for block in page_blocks if _is_headline(block)]
        page_metrics = _page_fragment_metrics(
            page_blocks=page_blocks,
            headline_candidates=headline_candidates,
            page_width=page_width,
        )
        skip_reason = _page_skip_reason(page_metrics)
        if skip_reason is not None:
            skipped_pages.append(
                {
                    "page_no": page_no,
                    "reason": skip_reason,
                    "metrics": page_metrics,
                }
            )
            continue

        processed_page_count += 1
        headlines, dependent_headlines = _group_headlines(headline_candidates, page_width)
        briefing_headers = [block for block in page_blocks if _is_briefing_header(block)]
        assignments = _assign_blocks_to_headlines(
            page_blocks,
            headlines,
            page_width,
            briefing_headers=briefing_headers,
        )

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
                    ) - _quality_score_penalty(quality, page_no),
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
        "processed_page_count": processed_page_count,
        "skipped_pages": skipped_pages,
        "article_count": len(ranked_articles),
        "selected_top_half_count": top_count,
        "quality_summary": quality_summary,
        "articles": ranked_articles,
        "selected_article_indexes": list(range(top_count)),
    }


def _select_articles_for_reading(
    result: dict[str, Any],
    *,
    selected_only: bool,
) -> list[dict[str, Any]]:
    articles = result.get("articles")
    if not isinstance(articles, list):
        return []
    if not selected_only:
        return [article for article in articles if isinstance(article, dict)]

    indexes = result.get("selected_article_indexes")
    if not isinstance(indexes, list):
        top_count = int(result.get("selected_top_half_count", 0) or 0)
        return [article for article in articles[:top_count] if isinstance(article, dict)]

    selected: list[dict[str, Any]] = []
    for raw_index in indexes:
        if not isinstance(raw_index, int):
            continue
        if raw_index < 0 or raw_index >= len(articles):
            continue
        article = articles[raw_index]
        if isinstance(article, dict):
            selected.append(article)
    return selected


def _single_line(text: str | None) -> str:
    if not text:
        return ""
    return _normalize_text(text)


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    return _single_line(str(value).strip())


def render_newspaper_reading_markdown(
    result: dict[str, Any],
    *,
    selected_only: bool = True,
    max_articles: int | None = None,
) -> str:
    selected_articles = _select_articles_for_reading(result, selected_only=selected_only)
    if max_articles is not None and max_articles > 0:
        selected_articles = selected_articles[:max_articles]

    source_pdf = str(result.get("source_pdf", ""))
    source_name = Path(source_pdf).name if source_pdf else "unknown.pdf"
    all_articles = result.get("articles")
    all_count = len(all_articles) if isinstance(all_articles, list) else 0
    selected_count = int(result.get("selected_top_half_count", 0) or 0)

    lines = [
        f"# Reading Edition: {source_name}",
        "",
        f"Source PDF: {source_pdf or 'unknown'}",
        f"Article candidates: {all_count}",
        f"Selected top-half: {selected_count}",
        f"Included in this file: {len(selected_articles)} ({'selected' if selected_only else 'all'})",
        "",
    ]

    if not selected_articles:
        lines.append("_No article content available._")
        lines.append("")
        return "\n".join(lines)

    for position, article in enumerate(selected_articles, start=1):
        headline = _optional_text(article.get("headline")) or f"Untitled article {position}"
        page_start = article.get("page_start", "?")
        article_type = _optional_text(article.get("article_type")) or "unknown"
        quality = article.get("quality") if isinstance(article.get("quality"), dict) else {}
        quality_grade = _optional_text(quality.get("grade")) or "unknown"
        quality_score = quality.get("score", "?")
        rank_score = article.get("score", "?")

        lines.append(f"## {position}. {headline}")
        lines.append("")
        lines.append(
            f"Page: {page_start} | Type: {article_type} | Quality: {quality_grade} ({quality_score}) | Rank score: {rank_score}"
        )

        byline = _optional_text(article.get("byline"))
        if byline:
            lines.append(f"Byline: {byline}")

        dateline = _optional_text(article.get("dateline"))
        if dateline:
            lines.append(f"Dateline: {dateline}")

        deck = _optional_text(article.get("deck"))
        if deck:
            lines.append(f"Deck: {deck}")

        body_text = str(article.get("body_text", "")).strip()
        if body_text:
            lines.append("")
            lines.append(body_text)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def write_newspaper_reading_markdown(
    result: dict[str, Any],
    output_path: Path,
    *,
    selected_only: bool = True,
    max_articles: int | None = None,
) -> str:
    markdown_text = render_newspaper_reading_markdown(
        result,
        selected_only=selected_only,
        max_articles=max_articles,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_text, encoding="utf-8")
    return markdown_text


def write_newspaper_articles(structured: dict[str, Any], source_pdf: Path, output_path: Path) -> dict[str, Any]:
    result = extract_newspaper_articles(structured, source_pdf)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
