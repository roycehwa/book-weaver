from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "were",
    "will",
    "with",
}


@dataclass(slots=True)
class ParagraphProfile:
    text: str
    word_count: int
    digit_ratio: float
    uppercase_ratio: float
    dot_leader_count: int
    time_count: int
    month_count: int
    terms: set[str]


HEADLINE_STOPWORDS = STOPWORDS | {
    "from",
    "page",
    "edition",
    "story",
    "continued",
}


PHOTO_CREDIT_PREFIX_PATTERN = re.compile(
    r"^(?:[A-Z][A-Z'’.\-]+(?:\s+[A-Z][A-Z'’.\-]+)*\s*/\s*"
    r"(?:THE NEW YORK TIMES|ASSOCIATED PRESS|REUTERS|AFP|GETTY IMAGES)\s*)+",
)
PHOTO_CREDIT_ONLY_PATTERN = re.compile(
    r"^(?:PHOTOGRAPHS?\s+BY\s+.+|[A-Z][A-Z'’.\-]+(?:\s+[A-Z][A-Z'’.\-]+)*\s*/\s*"
    r"(?:THE NEW YORK TIMES|ASSOCIATED PRESS|REUTERS|AFP|GETTY IMAGES))$"
)
CAPTION_LIKE_PATTERN = re.compile(
    r"^(?:Clockwise from|Left,|Above,|From left|From top|Top left|Top right)",
    re.IGNORECASE,
)
FROM_PAGE_MARKER_PATTERN = re.compile(
    r"^[A-Z][A-Za-z0-9'’.,\-\s]{0,120}\bFROM PAGE \d+\b",
    re.IGNORECASE,
)


def _normalize_paragraph(text: str) -> str:
    normalized = text.replace("\r\n", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\s*\n+\s*", " ", normalized)
    normalized = re.sub(r"([A-Za-z])-\s+([a-z])", r"\1\2", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _paragraph_profile(paragraph: str) -> ParagraphProfile:
    tokens = re.findall(r"[A-Za-z0-9']+", paragraph)
    word_count = len(tokens)
    digit_token_count = sum(1 for token in tokens if any(char.isdigit() for char in token))
    digit_ratio = digit_token_count / word_count if word_count else 0.0

    alpha_chars = [char for char in paragraph if char.isalpha()]
    uppercase_ratio = (
        sum(1 for char in alpha_chars if char.isupper()) / len(alpha_chars) if alpha_chars else 0.0
    )

    terms = {
        token.lower()
        for token in tokens
        if token.isalpha() and len(token) >= 4 and token.lower() not in STOPWORDS
    }

    return ParagraphProfile(
        text=paragraph,
        word_count=word_count,
        digit_ratio=digit_ratio,
        uppercase_ratio=uppercase_ratio,
        dot_leader_count=len(re.findall(r"\.{4,}", paragraph)),
        time_count=len(re.findall(r"\b\d{1,2}:\d{2}\b", paragraph)),
        month_count=len(
            re.findall(
                r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\b|"
                r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\b",
                paragraph,
                re.IGNORECASE,
            )
        ),
        terms=terms,
    )


def _is_listing_like(profile: ParagraphProfile) -> bool:
    if profile.dot_leader_count >= 1:
        return True
    if profile.digit_ratio >= 0.48 and profile.word_count >= 8:
        return True
    if profile.time_count >= 2:
        return True
    if profile.month_count >= 5 and profile.word_count <= 90:
        return True
    if profile.uppercase_ratio >= 0.85 and profile.word_count <= 12:
        return True
    return False


def _is_narrative_like(profile: ParagraphProfile) -> bool:
    if profile.word_count < 18:
        return False
    if profile.digit_ratio >= 0.25:
        return False
    return not _is_listing_like(profile)


def _is_heading_like(profile: ParagraphProfile) -> bool:
    text = profile.text
    if profile.word_count < 4 or profile.word_count > 24:
        return False
    if text.endswith((".", "!", "?", "”", "\"")) and profile.word_count > 10:
        return False
    if profile.uppercase_ratio >= 0.72 and profile.word_count <= 14:
        return True
    if re.match(r"^[A-Z][A-Z ]{2,}", text):
        return True
    if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){2,}", text) and profile.word_count <= 16:
        return True
    if re.match(r"^[A-Z][A-Za-z'’\-]+(?:\s+[a-z][A-Za-z'’\-]+){2,}", text):
        return True
    return False


def _is_noise(profile: ParagraphProfile) -> bool:
    if profile.word_count <= 2:
        return True
    if profile.word_count <= 5 and profile.dot_leader_count == 0 and profile.time_count == 0 and profile.month_count == 0:
        return True
    if profile.word_count <= 4 and profile.uppercase_ratio >= 0.8:
        return True
    if profile.dot_leader_count >= 1 and profile.word_count <= 12:
        return True
    return False


def _term_overlap(left: ParagraphProfile, right: ParagraphProfile) -> float:
    if not left.terms or not right.terms:
        return 0.0
    intersection = len(left.terms & right.terms)
    baseline = min(len(left.terms), len(right.terms))
    return intersection / baseline if baseline else 0.0


def _is_hard_break(previous: ParagraphProfile, current: ParagraphProfile) -> bool:
    overlap = _term_overlap(previous, current)
    if previous.word_count >= 20 and _is_heading_like(current):
        return True
    if _is_heading_like(previous) and _is_narrative_like(current):
        return False
    if _is_narrative_like(previous) and _is_listing_like(current):
        return True
    if previous.word_count >= 20 and current.word_count <= 6:
        return True
    if (
        previous.word_count >= 25
        and current.word_count >= 20
        and overlap < 0.02
        and current.month_count >= 2
        and current.digit_ratio >= 0.18
    ):
        return True
    return False


def _split_segments(profiles: list[ParagraphProfile]) -> list[list[ParagraphProfile]]:
    segments: list[list[ParagraphProfile]] = []
    for profile in profiles:
        if not segments:
            segments.append([profile])
            continue
        last_profile = segments[-1][-1]
        if _is_hard_break(last_profile, profile):
            segments.append([profile])
        else:
            segments[-1].append(profile)
    return segments


def _segment_score(segment: list[ParagraphProfile], index: int) -> float:
    word_total = sum(profile.word_count for profile in segment)
    narrative_count = sum(1 for profile in segment if _is_narrative_like(profile))
    listing_count = sum(1 for profile in segment if _is_listing_like(profile))
    short_count = sum(1 for profile in segment if profile.word_count <= 5)

    return (
        narrative_count * 16.0
        + math.sqrt(max(1, word_total)) * 2.4
        - listing_count * 18.0
        - short_count * 10.0
        - index * 3.0
    )


def _segment_terms(segment: list[ParagraphProfile]) -> set[str]:
    terms: set[str] = set()
    for profile in segment:
        terms |= profile.terms
    return terms


def _segment_overlap(left: list[ParagraphProfile], right: list[ParagraphProfile]) -> float:
    left_terms = _segment_terms(left)
    right_terms = _segment_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    intersection = len(left_terms & right_terms)
    baseline = min(len(left_terms), len(right_terms))
    return intersection / baseline if baseline else 0.0


def _can_merge_segment(candidate: list[ParagraphProfile], anchor: list[ParagraphProfile]) -> bool:
    candidate_words = sum(profile.word_count for profile in candidate)
    candidate_narrative = sum(1 for profile in candidate if _is_narrative_like(profile))
    candidate_listing = sum(1 for profile in candidate if _is_listing_like(profile))
    overlap = _segment_overlap(candidate, anchor)

    if candidate_narrative >= 2 and candidate_listing <= 1 and overlap >= 0.12:
        return True
    if candidate_words >= 180 and candidate_narrative >= 3 and candidate_listing == 0 and overlap >= 0.08:
        return True
    return False


def _merge_soft_breaks(paragraphs: list[str]) -> list[str]:
    merged: list[str] = []
    for paragraph in paragraphs:
        if not merged:
            merged.append(paragraph)
            continue
        previous = merged[-1]
        if previous and previous[-1] not in '.!?"”’\'' and paragraph[:1].islower():
            merged[-1] = f"{previous} {paragraph}"
        else:
            merged.append(paragraph)
    return merged


def _clean_paragraph_text(paragraph: str) -> str | None:
    cleaned = _normalize_paragraph(paragraph)
    if not cleaned:
        return None

    if FROM_PAGE_MARKER_PATTERN.match(cleaned):
        return None
    if CAPTION_LIKE_PATTERN.match(cleaned) and len(cleaned.split()) > 8:
        return None
    if PHOTO_CREDIT_ONLY_PATTERN.match(cleaned):
        return None

    stripped_credit = PHOTO_CREDIT_PREFIX_PATTERN.sub("", cleaned).strip()
    if stripped_credit:
        cleaned = stripped_credit

    # Continuation fragments often start with a dash and a lowercase word.
    cleaned = re.sub(r"^[—-]\s+(?=[a-z])", "", cleaned)

    if not cleaned:
        return None
    return cleaned


def _ends_with_terminal_punctuation(text: str) -> bool:
    stripped = text.rstrip(" \t\r\n'\"”)]}")
    return stripped.endswith((".", "!", "?"))


def _looks_truncated_fragment(text: str) -> bool:
    tokens = re.findall(r"[A-Za-z0-9']+", text)
    if not tokens:
        return True
    profile = _paragraph_profile(text)
    if _is_heading_like(profile):
        return False
    if len(tokens) <= 16 and not _ends_with_terminal_punctuation(text):
        return True
    return False


def _drop_fragmentary_paragraphs(paragraphs: list[str]) -> list[str]:
    if not paragraphs:
        return []

    cleaned: list[str] = []
    profiles = [_paragraph_profile(paragraph) for paragraph in paragraphs]
    for index, paragraph in enumerate(paragraphs):
        profile = profiles[index]
        previous = cleaned[-1] if cleaned else None
        previous_profile = _paragraph_profile(previous) if previous else None

        if CAPTION_LIKE_PATTERN.match(paragraph) and len(paragraph.split()) > 8:
            continue
        if _looks_truncated_fragment(paragraph):
            continue
        if paragraph[:1].islower() and previous and _ends_with_terminal_punctuation(previous):
            overlap = _term_overlap(previous_profile, profile) if previous_profile else 0.0
            if overlap < 0.15:
                continue

        cleaned.append(paragraph)

    return cleaned


def _headline_terms(headline: str) -> set[str]:
    tokens = re.findall(r"[A-Za-z']+", headline)
    return {
        token.lower()
        for token in tokens
        if len(token) >= 4 and token.lower() not in HEADLINE_STOPWORDS
    }


def _headline_similarity(left: str, right: str) -> float:
    left_terms = _headline_terms(left)
    right_terms = _headline_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    intersection = len(left_terms & right_terms)
    baseline = min(len(left_terms), len(right_terms))
    return intersection / baseline if baseline else 0.0


def _split_body_paragraphs(body_text: str) -> list[str]:
    paragraphs: list[str] = []
    for part in body_text.split("\n\n"):
        cleaned = _clean_paragraph_text(part)
        if cleaned:
            paragraphs.append(cleaned)
    return paragraphs


def _paragraph_fingerprint(text: str) -> str:
    lowered = text.lower()
    lowered = re.sub(r"[^a-z0-9]+", "", lowered)
    return lowered


def _merge_body_fragments(paragraph_groups: list[list[str]]) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for group in paragraph_groups:
        for paragraph in group:
            fingerprint = _paragraph_fingerprint(paragraph)
            if not fingerprint or fingerprint in seen:
                continue
            seen.add(fingerprint)
            merged.append(paragraph)
    return "\n\n".join(merged).strip()


def _looks_fragmented_frontmatter(text: str) -> bool:
    normalized = _normalize_paragraph(text)
    if not normalized:
        return True
    if normalized[:1] in {"-", "—", ",", ";"}:
        return True
    if normalized[:1].islower():
        return True
    return False


def _selected_article_indexes(payload: dict[str, Any], article_count: int) -> set[int]:
    selected_indexes = payload.get("selected_article_indexes")
    if isinstance(selected_indexes, list):
        return {
            raw_index
            for raw_index in selected_indexes
            if isinstance(raw_index, int) and 0 <= raw_index < article_count
        }
    top_count = int(payload.get("selected_top_half_count", 0) or 0)
    return {index for index in range(min(article_count, max(0, top_count)))}


def _merge_selected_related_fragments(articles: list[dict[str, Any]], selected_indexes: set[int]) -> None:
    for article_index in sorted(selected_indexes):
        article = articles[article_index]
        if not isinstance(article, dict):
            continue

        headline = str(article.get("headline", "")).strip()
        if not headline:
            continue
        page_start = int(article.get("page_start", 0) or 0)
        body_text = str(article.get("body_text", "")).strip()
        article_score = float(article.get("score", 0.0) or 0.0)
        if not body_text:
            continue

        related_indexes: list[int] = []
        for candidate_index, candidate in enumerate(articles):
            if candidate_index == article_index:
                continue
            if not isinstance(candidate, dict):
                continue
            candidate_score = float(candidate.get("score", 0.0) or 0.0)
            if candidate_index in selected_indexes and candidate_score >= article_score * 0.9:
                continue
            candidate_body = str(candidate.get("body_text", "")).strip()
            if len(candidate_body) < 180:
                continue

            candidate_headline = str(candidate.get("headline", "")).strip()
            similarity = _headline_similarity(headline, candidate_headline)
            if similarity < 0.72:
                continue

            candidate_page = int(candidate.get("page_start", 0) or 0)
            if page_start > 0 and candidate_page > 0 and abs(page_start - candidate_page) > 6:
                continue
            related_indexes.append(candidate_index)

        if not related_indexes:
            continue

        related_indexes.sort(
            key=lambda index: (
                int(articles[index].get("page_start", 0) or 0),
                -float(articles[index].get("score", 0.0) or 0.0),
            )
        )

        groups: list[list[str]] = []
        for related_index in related_indexes:
            related_article = articles[related_index]
            related_page = int(related_article.get("page_start", 0) or 0)
            related_body = _split_body_paragraphs(str(related_article.get("body_text", "")))
            if not related_body:
                continue
            if related_page > 0 and page_start > 0 and related_page <= page_start:
                groups.append(related_body)

        groups.append(_split_body_paragraphs(body_text))

        for related_index in related_indexes:
            related_article = articles[related_index]
            related_page = int(related_article.get("page_start", 0) or 0)
            related_body = _split_body_paragraphs(str(related_article.get("body_text", "")))
            if not related_body:
                continue
            if related_page > 0 and page_start > 0 and related_page > page_start:
                groups.append(related_body)

        merged_body = _merge_body_fragments(groups)
        if merged_body:
            article["body_text"] = merged_body
            article["merged_fragment_indexes"] = related_indexes

        if _looks_fragmented_frontmatter(str(article.get("deck", ""))):
            for related_index in related_indexes:
                candidate_deck = str(articles[related_index].get("deck", "")).strip()
                if candidate_deck and not _looks_fragmented_frontmatter(candidate_deck):
                    article["deck"] = candidate_deck
                    break


def rebuild_article_body(body_text: str) -> tuple[str, dict[str, Any]]:
    raw_paragraphs = [part.strip() for part in body_text.split("\n\n") if part.strip()]
    normalized_paragraphs = [cleaned for paragraph in raw_paragraphs if (cleaned := _clean_paragraph_text(paragraph))]
    profiles = [_paragraph_profile(paragraph) for paragraph in normalized_paragraphs if paragraph]
    profiles = [profile for profile in profiles if not _is_noise(profile)]

    if not profiles:
        rebuilt = _normalize_paragraph(body_text)
        return rebuilt, {"raw_paragraphs": len(raw_paragraphs), "kept_paragraphs": 1, "segments": 1}

    segments = _split_segments(profiles)
    best_index = max(range(len(segments)), key=lambda index: _segment_score(segments[index], index))
    selected_indexes = [best_index]
    selected_segment = segments[best_index]
    total_word_count = sum(profile.word_count for profile in profiles)

    # Preserve intro/context when the previous segment is clearly topically connected.
    if best_index > 0:
        previous = segments[best_index - 1]
        if _can_merge_segment(previous, selected_segment):
            selected_indexes.insert(0, best_index - 1)
            selected_segment = previous + selected_segment

    # Keep a connected next segment when we still cover too little of the source body.
    selected_word_total = sum(profile.word_count for profile in selected_segment)
    if selected_word_total < total_word_count * 0.78 and best_index + 1 < len(segments):
        next_segment = segments[best_index + 1]
        if _can_merge_segment(next_segment, segments[selected_indexes[-1]]):
            selected_indexes.append(best_index + 1)
            selected_segment = selected_segment + next_segment

    # Keep an adjacent segment when the selected segment is too short, to avoid over-truncation.
    selected_word_total = sum(profile.word_count for profile in selected_segment)
    if selected_word_total < total_word_count * 0.45 and best_index + 1 < len(segments):
        candidate = segments[best_index + 1]
        if sum(1 for profile in candidate if _is_listing_like(profile)) <= 1:
            if best_index + 1 not in selected_indexes:
                selected_indexes.append(best_index + 1)
            selected_segment = selected_segment + candidate

    rebuilt_paragraphs = _merge_soft_breaks([profile.text for profile in selected_segment])
    rebuilt_paragraphs = _drop_fragmentary_paragraphs(rebuilt_paragraphs)
    rebuilt_text = "\n\n".join(paragraph for paragraph in rebuilt_paragraphs if paragraph).strip()

    metadata = {
        "raw_paragraphs": len(raw_paragraphs),
        "kept_paragraphs": len(rebuilt_paragraphs),
        "segments": len(segments),
        "selected_segment_index": best_index,
        "selected_segment_indexes": selected_indexes,
    }
    return rebuilt_text, metadata


def rebuild_articles_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rebuilt_payload = copy.deepcopy(payload)
    articles = rebuilt_payload.get("articles")
    if not isinstance(articles, list):
        return rebuilt_payload

    selected_indexes = _selected_article_indexes(rebuilt_payload, len(articles))
    _merge_selected_related_fragments(articles, selected_indexes)

    for article in articles:
        if not isinstance(article, dict):
            continue
        body_text = str(article.get("body_text", "")).strip()
        rebuilt_body_text, rebuild_meta = rebuild_article_body(body_text)
        article["rebuilt_body_text"] = rebuilt_body_text
        article["rebuild_meta"] = rebuild_meta
        article["rebuilt_body_chars"] = len(rebuilt_body_text)

    return rebuilt_payload


def _select_articles(payload: dict[str, Any], *, selected_only: bool, max_articles: int | None) -> list[dict[str, Any]]:
    articles = payload.get("articles")
    if not isinstance(articles, list):
        return []

    selected: list[dict[str, Any]]
    if selected_only:
        indexes = payload.get("selected_article_indexes")
        selected = []
        if isinstance(indexes, list):
            for raw_index in indexes:
                if not isinstance(raw_index, int):
                    continue
                if raw_index < 0 or raw_index >= len(articles):
                    continue
                article = articles[raw_index]
                if isinstance(article, dict):
                    selected.append(article)
        else:
            top_count = int(payload.get("selected_top_half_count", 0) or 0)
            selected = [article for article in articles[:top_count] if isinstance(article, dict)]
    else:
        selected = [article for article in articles if isinstance(article, dict)]

    if max_articles is not None and max_articles > 0:
        selected = selected[:max_articles]
    return selected


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def render_rebuilt_reading_markdown(
    payload: dict[str, Any],
    *,
    selected_only: bool = True,
    max_articles: int | None = None,
) -> tuple[str, dict[str, Any]]:
    rebuilt_payload = rebuild_articles_payload(payload)
    selected_articles = _select_articles(
        rebuilt_payload,
        selected_only=selected_only,
        max_articles=max_articles,
    )

    source_pdf = str(rebuilt_payload.get("source_pdf", ""))
    source_name = Path(source_pdf).name if source_pdf else "unknown.pdf"
    total_articles = rebuilt_payload.get("articles")
    total_count = len(total_articles) if isinstance(total_articles, list) else 0

    lines = [
        f"# Rebuilt Reading Edition: {source_name}",
        "",
        f"Source PDF: {source_pdf or 'unknown'}",
        f"Article candidates: {total_count}",
        f"Included in this file: {len(selected_articles)} ({'selected' if selected_only else 'all'})",
        "",
    ]

    if not selected_articles:
        lines.append("_No article content available._")
        lines.append("")
        markdown_text = "\n".join(lines)
        summary = {"included_articles": 0, "total_articles": total_count}
        return markdown_text, summary

    dropped_paragraphs = 0
    raw_paragraphs = 0
    for index, article in enumerate(selected_articles, start=1):
        headline = _optional_text(article.get("headline")) or f"Untitled article {index}"
        page_start = article.get("page_start", "?")
        quality = article.get("quality") if isinstance(article.get("quality"), dict) else {}
        quality_grade = _optional_text(quality.get("grade")) or "unknown"
        quality_score = quality.get("score", "?")
        rank_score = article.get("score", "?")

        lines.append(f"## {index}. {headline}")
        lines.append("")
        lines.append(f"Page: {page_start} | Quality: {quality_grade} ({quality_score}) | Rank score: {rank_score}")

        deck = _optional_text(article.get("deck"))
        if deck:
            lines.append(f"Deck: {deck}")

        byline = _optional_text(article.get("byline"))
        if byline:
            lines.append(f"Byline: {byline}")

        dateline = _optional_text(article.get("dateline"))
        if dateline:
            lines.append(f"Dateline: {dateline}")

        rebuild_meta = article.get("rebuild_meta", {})
        if isinstance(rebuild_meta, dict):
            raw_count = int(rebuild_meta.get("raw_paragraphs", 0) or 0)
            kept_count = int(rebuild_meta.get("kept_paragraphs", 0) or 0)
            if raw_count > 0:
                raw_paragraphs += raw_count
                dropped_paragraphs += max(0, raw_count - kept_count)

        body_text = str(article.get("rebuilt_body_text") or article.get("body_text") or "").strip()
        if body_text:
            lines.append("")
            lines.append(body_text)
        lines.append("")

    markdown_text = "\n".join(lines).strip() + "\n"
    summary = {
        "included_articles": len(selected_articles),
        "total_articles": total_count,
        "raw_paragraphs": raw_paragraphs,
        "dropped_paragraphs": dropped_paragraphs,
    }
    return markdown_text, summary


def write_rebuilt_outputs(
    payload: dict[str, Any],
    *,
    output_markdown_path: Path,
    output_json_path: Path | None = None,
    selected_only: bool = True,
    max_articles: int | None = None,
) -> dict[str, Any]:
    markdown_text, summary = render_rebuilt_reading_markdown(
        payload,
        selected_only=selected_only,
        max_articles=max_articles,
    )
    rebuilt_payload = rebuild_articles_payload(payload)

    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.write_text(markdown_text, encoding="utf-8")

    if output_json_path is not None:
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(
            json.dumps(rebuilt_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "markdown_path": str(output_markdown_path),
        "json_path": str(output_json_path) if output_json_path is not None else None,
        "summary": summary,
    }
