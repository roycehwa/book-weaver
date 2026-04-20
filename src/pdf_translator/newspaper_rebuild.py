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


def rebuild_article_body(body_text: str) -> tuple[str, dict[str, Any]]:
    raw_paragraphs = [part.strip() for part in body_text.split("\n\n") if part.strip()]
    normalized_paragraphs = [_normalize_paragraph(paragraph) for paragraph in raw_paragraphs]
    profiles = [_paragraph_profile(paragraph) for paragraph in normalized_paragraphs if paragraph]
    profiles = [profile for profile in profiles if not _is_noise(profile)]

    if not profiles:
        rebuilt = _normalize_paragraph(body_text)
        return rebuilt, {"raw_paragraphs": len(raw_paragraphs), "kept_paragraphs": 1, "segments": 1}

    segments = _split_segments(profiles)
    best_index = max(range(len(segments)), key=lambda index: _segment_score(segments[index], index))
    selected_segment = segments[best_index]

    # Keep an adjacent segment when the selected segment is too short, to avoid over-truncation.
    selected_word_total = sum(profile.word_count for profile in selected_segment)
    total_word_count = sum(profile.word_count for profile in profiles)
    if selected_word_total < total_word_count * 0.45 and best_index + 1 < len(segments):
        candidate = segments[best_index + 1]
        if sum(1 for profile in candidate if _is_listing_like(profile)) <= 1:
            selected_segment = selected_segment + candidate

    rebuilt_paragraphs = _merge_soft_breaks([profile.text for profile in selected_segment])
    rebuilt_text = "\n\n".join(paragraph for paragraph in rebuilt_paragraphs if paragraph).strip()

    metadata = {
        "raw_paragraphs": len(raw_paragraphs),
        "kept_paragraphs": len(rebuilt_paragraphs),
        "segments": len(segments),
        "selected_segment_index": best_index,
    }
    return rebuilt_text, metadata


def rebuild_articles_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rebuilt_payload = copy.deepcopy(payload)
    articles = rebuilt_payload.get("articles")
    if not isinstance(articles, list):
        return rebuilt_payload

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
