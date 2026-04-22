from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdf_translator.newspaper import _extract_news_blocks
from pdf_translator.newspaper_rebuild import rebuild_articles_payload


@dataclass(slots=True)
class Region:
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
    def area(self) -> float:
        return self.width * self.height

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.bottom + self.height / 2.0


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
    return " ".join(text.split())


def _extract_caption_text(
    picture_item: dict[str, Any],
    structured: dict[str, Any],
) -> str | None:
    caption_refs = picture_item.get("captions")
    if not isinstance(caption_refs, list):
        return None

    parts: list[str] = []
    for entry in caption_refs:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("$ref")
        if not isinstance(ref, str):
            continue
        resolved = _resolve_ref(structured, ref)
        if not resolved:
            continue
        bucket_name, item = resolved
        if bucket_name != "texts":
            continue
        text = _normalize_text(str(item.get("text", "")).strip())
        if text:
            parts.append(text)

    if not parts:
        return None
    return " ".join(parts)


def _extract_picture_regions(
    structured: dict[str, Any],
    *,
    min_area: float = 3500.0,
    min_dimension: float = 35.0,
) -> list[dict[str, Any]]:
    pictures = structured.get("pictures")
    if not isinstance(pictures, list):
        return []

    regions: list[dict[str, Any]] = []
    for picture_index, picture_item in enumerate(pictures):
        if not isinstance(picture_item, dict):
            continue
        prov = picture_item.get("prov")
        if not isinstance(prov, list) or not prov:
            continue
        first_prov = prov[0]
        if not isinstance(first_prov, dict):
            continue
        bbox = first_prov.get("bbox")
        if not isinstance(bbox, dict):
            continue
        try:
            page_no = int(first_prov.get("page_no", 0))
            left = float(bbox.get("l", 0.0))
            top = float(bbox.get("t", 0.0))
            right = float(bbox.get("r", 0.0))
            bottom = float(bbox.get("b", 0.0))
        except (TypeError, ValueError):
            continue
        region = Region(page_no=page_no, left=left, top=top, right=right, bottom=bottom)
        if page_no <= 0:
            continue
        if region.width < min_dimension or region.height < min_dimension:
            continue
        if region.area < min_area:
            continue

        caption = _extract_caption_text(picture_item, structured)
        regions.append(
            {
                "picture_index": picture_index,
                "page_no": page_no,
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "caption": caption,
            }
        )
    return regions


def _article_regions(payload: dict[str, Any], structured: dict[str, Any]) -> list[dict[str, Any]]:
    articles = payload.get("articles")
    if not isinstance(articles, list):
        return []

    blocks = _extract_news_blocks(structured)
    regions: list[dict[str, Any]] = []
    for article_index, article in enumerate(articles):
        if not isinstance(article, dict):
            continue
        block_indexes = article.get("block_indexes")
        if not isinstance(block_indexes, list):
            continue
        page_start = int(article.get("page_start", 0) or 0)
        selected_blocks = []
        for raw_index in block_indexes:
            if not isinstance(raw_index, int):
                continue
            if raw_index < 0 or raw_index >= len(blocks):
                continue
            selected_blocks.append(blocks[raw_index])

        if page_start > 0:
            page_blocks = [block for block in selected_blocks if block.page_no == page_start]
            if page_blocks:
                selected_blocks = page_blocks

        if not selected_blocks:
            continue

        left = min(block.left for block in selected_blocks)
        right = max(block.right for block in selected_blocks)
        top = max(block.top for block in selected_blocks)
        bottom = min(block.bottom for block in selected_blocks)
        region = Region(page_no=page_start or selected_blocks[0].page_no, left=left, top=top, right=right, bottom=bottom)
        regions.append(
            {
                "article_index": article_index,
                "page_no": region.page_no,
                "left": region.left,
                "top": region.top,
                "right": region.right,
                "bottom": region.bottom,
            }
        )
    return regions


def _overlap_ratio(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    overlap = max(0.0, min(end_a, end_b) - max(start_a, start_b))
    baseline = max(1.0, min(end_a - start_a, end_b - start_b))
    return overlap / baseline


def _match_score(article_region: Region, picture_region: Region) -> float:
    x_overlap = _overlap_ratio(article_region.left, article_region.right, picture_region.left, picture_region.right)
    y_overlap = _overlap_ratio(article_region.bottom, article_region.top, picture_region.bottom, picture_region.top)

    horizontal_gap = 0.0
    if x_overlap <= 0:
        horizontal_gap = min(abs(article_region.left - picture_region.right), abs(article_region.right - picture_region.left))
    vertical_gap = 0.0
    if y_overlap <= 0:
        vertical_gap = min(abs(article_region.bottom - picture_region.top), abs(article_region.top - picture_region.bottom))

    center_distance = math.hypot(article_region.center_x - picture_region.center_x, article_region.center_y - picture_region.center_y)
    base_distance_score = 1.0 / (1.0 + center_distance / 420.0)
    gap_score = 1.0 / (1.0 + horizontal_gap / 180.0) + 1.0 / (1.0 + vertical_gap / 180.0)

    score = x_overlap * 2.2 + y_overlap * 1.2 + base_distance_score + gap_score

    # Prefer images near the upper part of a story.
    if picture_region.center_y >= article_region.center_y:
        score += 0.25
    return score


def _match_geometry(article_region: Region, picture_region: Region) -> dict[str, float]:
    x_overlap = _overlap_ratio(article_region.left, article_region.right, picture_region.left, picture_region.right)
    y_overlap = _overlap_ratio(article_region.bottom, article_region.top, picture_region.bottom, picture_region.top)

    horizontal_gap = 0.0
    if x_overlap <= 0:
        horizontal_gap = min(abs(article_region.left - picture_region.right), abs(article_region.right - picture_region.left))
    vertical_gap = 0.0
    if y_overlap <= 0:
        vertical_gap = min(abs(article_region.bottom - picture_region.top), abs(article_region.top - picture_region.bottom))

    return {
        "x_overlap": x_overlap,
        "y_overlap": y_overlap,
        "horizontal_gap": horizontal_gap,
        "vertical_gap": vertical_gap,
    }


def _is_plausible_picture_match(
    article_region: Region,
    picture_region: Region,
    *,
    min_primary_overlap: float,
    max_secondary_gap: float,
) -> tuple[bool, dict[str, float]]:
    geometry = _match_geometry(article_region, picture_region)
    x_overlap = geometry["x_overlap"]
    y_overlap = geometry["y_overlap"]
    horizontal_gap = geometry["horizontal_gap"]
    vertical_gap = geometry["vertical_gap"]

    x_aligned = x_overlap >= min_primary_overlap and (y_overlap > 0.0 or vertical_gap <= max_secondary_gap)
    y_aligned = y_overlap >= min_primary_overlap and (x_overlap > 0.0 or horizontal_gap <= max_secondary_gap)
    return (x_aligned or y_aligned), geometry


def match_pictures_to_articles(
    payload: dict[str, Any],
    structured: dict[str, Any],
    *,
    max_images_per_article: int = 1,
    min_match_score: float = 2.8,
    min_score_gap: float = 0.75,
    min_score_ratio: float = 1.18,
    min_primary_overlap: float = 0.16,
    max_secondary_gap: float = 70.0,
) -> dict[str, Any]:
    matched_payload = copy.deepcopy(payload)
    articles = matched_payload.get("articles")
    if not isinstance(articles, list):
        return matched_payload

    for article in articles:
        if isinstance(article, dict):
            article["illustration_images"] = []

    article_regions_raw = _article_regions(payload, structured)
    picture_regions_raw = _extract_picture_regions(structured)
    if not article_regions_raw or not picture_regions_raw:
        return matched_payload

    article_regions = {
        entry["article_index"]: Region(
            page_no=int(entry["page_no"]),
            left=float(entry["left"]),
            top=float(entry["top"]),
            right=float(entry["right"]),
            bottom=float(entry["bottom"]),
        )
        for entry in article_regions_raw
    }

    picture_regions = [
        (
            picture_entry,
            Region(
                page_no=int(picture_entry["page_no"]),
                left=float(picture_entry["left"]),
                top=float(picture_entry["top"]),
                right=float(picture_entry["right"]),
                bottom=float(picture_entry["bottom"]),
            ),
        )
        for picture_entry in picture_regions_raw
    ]

    scores_by_article: dict[int, list[tuple[float, dict[str, Any]]]] = {index: [] for index in article_regions}

    for picture_entry, picture_region in picture_regions:
        candidates: list[tuple[int, float]] = []
        for article_index, article_region in article_regions.items():
            if article_region.page_no != picture_region.page_no:
                continue
            score = _match_score(article_region, picture_region)
            candidates.append((article_index, score))

        if not candidates:
            continue
        candidates.sort(key=lambda item: item[1], reverse=True)
        best_article_index, best_score = candidates[0]
        second_score = candidates[1][1] if len(candidates) > 1 else 0.0
        score_gap = best_score - second_score
        score_ratio = best_score / max(0.001, second_score) if second_score > 0 else float("inf")

        if best_score < min_match_score:
            continue
        if second_score > 0 and score_gap < min_score_gap:
            continue
        if second_score > 0 and score_ratio < min_score_ratio:
            continue

        best_article_region = article_regions.get(best_article_index)
        if best_article_region is None:
            continue
        plausible, geometry = _is_plausible_picture_match(
            best_article_region,
            picture_region,
            min_primary_overlap=min_primary_overlap,
            max_secondary_gap=max_secondary_gap,
        )
        if not plausible:
            continue

        picture_entry_with_match = dict(picture_entry)
        picture_entry_with_match["match"] = {
            "score_gap": round(score_gap, 3),
            "score_ratio": None if score_ratio == float("inf") else round(score_ratio, 3),
            "x_overlap": round(geometry["x_overlap"], 3),
            "y_overlap": round(geometry["y_overlap"], 3),
            "horizontal_gap": round(geometry["horizontal_gap"], 1),
            "vertical_gap": round(geometry["vertical_gap"], 1),
        }
        scores_by_article[best_article_index].append((best_score, picture_entry_with_match))

    for article_index, scored_pictures in scores_by_article.items():
        scored_pictures.sort(key=lambda item: item[0], reverse=True)
        selected = scored_pictures[: max(1, max_images_per_article)]
        article = articles[article_index]
        if not isinstance(article, dict):
            continue
        article["illustration_images"] = [
            {
                "picture_index": entry["picture_index"],
                "page_no": entry["page_no"],
                "left": entry["left"],
                "top": entry["top"],
                "right": entry["right"],
                "bottom": entry["bottom"],
                "caption": entry.get("caption"),
                "score": round(score, 3),
                "score_gap": entry.get("match", {}).get("score_gap"),
                "score_ratio": entry.get("match", {}).get("score_ratio"),
                "x_overlap": entry.get("match", {}).get("x_overlap"),
                "y_overlap": entry.get("match", {}).get("y_overlap"),
                "horizontal_gap": entry.get("match", {}).get("horizontal_gap"),
                "vertical_gap": entry.get("match", {}).get("vertical_gap"),
            }
            for score, entry in selected
        ]

    return matched_payload


def _crop_picture_regions(
    payload: dict[str, Any],
    source_pdf: Path,
    *,
    images_dir: Path,
    render_scale: float = 2.0,
    margin_points: float = 8.0,
    allowed_article_indexes: set[int] | None = None,
) -> dict[str, Any]:
    import pypdfium2 as pdfium

    cropped_payload = copy.deepcopy(payload)
    articles = cropped_payload.get("articles")
    if not isinstance(articles, list):
        return cropped_payload

    images_dir.mkdir(parents=True, exist_ok=True)
    for existing in images_dir.glob("article-*-img-*-p*.png"):
        try:
            existing.unlink()
        except OSError:
            continue

    document = pdfium.PdfDocument(str(source_pdf))
    page_cache: dict[int, tuple[Any, float, float]] = {}

    for article_index, article in enumerate(articles):
        if not isinstance(article, dict):
            continue
        if allowed_article_indexes is not None and article_index not in allowed_article_indexes:
            article["illustration_images"] = []
            continue
        pictures = article.get("illustration_images")
        if not isinstance(pictures, list) or not pictures:
            continue
        exported: list[dict[str, Any]] = []

        for image_rank, picture in enumerate(pictures, start=1):
            if not isinstance(picture, dict):
                continue
            try:
                page_no = int(picture.get("page_no", 0))
                left = float(picture.get("left", 0.0))
                top = float(picture.get("top", 0.0))
                right = float(picture.get("right", 0.0))
                bottom = float(picture.get("bottom", 0.0))
            except (TypeError, ValueError):
                continue
            if page_no <= 0 or page_no > len(document):
                continue

            if page_no not in page_cache:
                page = document[page_no - 1]
                page_width, page_height = page.get_size()
                page_image = page.render(scale=render_scale).to_pil()
                page_cache[page_no] = (page_image, page_width, page_height)

            page_image, page_width, page_height = page_cache[page_no]
            image_width, image_height = page_image.size

            crop_left = max(0, int(math.floor((left - margin_points) * render_scale)))
            crop_right = min(image_width, int(math.ceil((right + margin_points) * render_scale)))
            crop_top = max(0, int(math.floor((page_height - (top + margin_points)) * render_scale)))
            crop_bottom = min(image_height, int(math.ceil((page_height - (bottom - margin_points)) * render_scale)))
            if crop_right <= crop_left or crop_bottom <= crop_top:
                continue

            cropped = page_image.crop((crop_left, crop_top, crop_right, crop_bottom))
            filename = f"article-{article_index + 1:03d}-img-{image_rank:02d}-p{page_no:02d}.png"
            output_path = images_dir / filename
            cropped.save(output_path)

            exported_entry = dict(picture)
            exported_entry["path"] = str(output_path.resolve())
            exported.append(exported_entry)

        article["illustration_images"] = exported

    return cropped_payload


def _select_article_indexes(
    payload: dict[str, Any],
    *,
    selected_only: bool,
    max_articles: int | None,
) -> list[int]:
    articles = payload.get("articles")
    if not isinstance(articles, list):
        return []
    if not selected_only:
        selected_indexes = [index for index, article in enumerate(articles) if isinstance(article, dict)]
    else:
        indexes = payload.get("selected_article_indexes")
        selected_indexes = []
        if isinstance(indexes, list):
            for raw_index in indexes:
                if not isinstance(raw_index, int):
                    continue
                if raw_index < 0 or raw_index >= len(articles):
                    continue
                if isinstance(articles[raw_index], dict):
                    selected_indexes.append(raw_index)
        else:
            top_count = int(payload.get("selected_top_half_count", 0) or 0)
            selected_indexes = [index for index, article in enumerate(articles[:top_count]) if isinstance(article, dict)]

    if max_articles is not None and max_articles > 0:
        selected_indexes = selected_indexes[:max_articles]
    return selected_indexes


def _select_articles(payload: dict[str, Any], *, selected_only: bool, max_articles: int | None) -> list[dict[str, Any]]:
    articles = payload.get("articles")
    if not isinstance(articles, list):
        return []
    selected_indexes = _select_article_indexes(payload, selected_only=selected_only, max_articles=max_articles)
    return [articles[index] for index in selected_indexes if 0 <= index < len(articles) and isinstance(articles[index], dict)]


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _safe_image_alt_text(caption: Any, article_position: int, image_rank: int) -> str:
    normalized = _optional_text(caption)
    if not normalized:
        return f"article-{article_position}-image-{image_rank}"
    sanitized = re.sub(r"[\[\]\(\)]", " ", normalized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if not sanitized:
        return f"article-{article_position}-image-{image_rank}"
    if len(sanitized) > 80:
        sanitized = sanitized[:77].rstrip() + "..."
    return sanitized


def _markdown_image_target(path: str) -> str:
    return f"<{path}>" if any(char in path for char in (" ", "(", ")")) else path


def render_illustrated_reading_markdown(
    payload: dict[str, Any],
    *,
    selected_only: bool = True,
    max_articles: int | None = None,
) -> tuple[str, dict[str, Any]]:
    selected_articles = _select_articles(payload, selected_only=selected_only, max_articles=max_articles)
    source_pdf = str(payload.get("source_pdf", ""))
    source_name = Path(source_pdf).name if source_pdf else "unknown.pdf"
    all_articles = payload.get("articles")
    total_count = len(all_articles) if isinstance(all_articles, list) else 0

    lines = [
        f"# Illustrated Reading Edition: {source_name}",
        "",
        f"Source PDF: {source_pdf or 'unknown'}",
        f"Article candidates: {total_count}",
        f"Included in this file: {len(selected_articles)} ({'selected' if selected_only else 'all'})",
        "",
    ]

    image_count = 0
    for position, article in enumerate(selected_articles, start=1):
        headline = _optional_text(article.get("headline")) or f"Untitled article {position}"
        page_start = article.get("page_start", "?")
        quality = article.get("quality") if isinstance(article.get("quality"), dict) else {}
        quality_grade = _optional_text(quality.get("grade")) or "unknown"
        quality_score = quality.get("score", "?")
        rank_score = article.get("score", "?")

        lines.append(f"## {position}. {headline}")
        lines.append("")
        lines.append(f"Page: {page_start} | Quality: {quality_grade} ({quality_score}) | Rank score: {rank_score}")

        pictures = article.get("illustration_images")
        if isinstance(pictures, list):
            for image_rank, picture in enumerate(pictures, start=1):
                if not isinstance(picture, dict):
                    continue
                path = picture.get("path")
                if not path:
                    continue
                alt_text = _safe_image_alt_text(picture.get("caption"), position, image_rank)
                lines.append(f"![{alt_text}]({_markdown_image_target(path)})")
                image_count += 1

        deck = _optional_text(article.get("deck"))
        if deck:
            lines.append(f"Deck: {deck}")

        byline = _optional_text(article.get("byline"))
        if byline:
            lines.append(f"Byline: {byline}")

        dateline = _optional_text(article.get("dateline"))
        if dateline:
            lines.append(f"Dateline: {dateline}")

        body_text = str(article.get("rebuilt_body_text") or article.get("body_text") or "").strip()
        if body_text:
            lines.append("")
            lines.append(body_text)
        lines.append("")

    markdown_text = "\n".join(lines).strip() + "\n"
    summary = {
        "included_articles": len(selected_articles),
        "total_articles": total_count,
        "included_images": image_count,
    }
    return markdown_text, summary


def write_illustrated_outputs(
    payload: dict[str, Any],
    structured: dict[str, Any],
    source_pdf: Path,
    *,
    output_markdown_path: Path,
    output_json_path: Path | None = None,
    images_dir: Path | None = None,
    selected_only: bool = True,
    max_articles: int | None = None,
    max_images_per_article: int = 1,
    render_scale: float = 2.0,
) -> dict[str, Any]:
    rebuilt_payload = rebuild_articles_payload(payload)
    matched_payload = match_pictures_to_articles(
        rebuilt_payload,
        structured,
        max_images_per_article=max_images_per_article,
    )
    selected_indexes = _select_article_indexes(
        matched_payload,
        selected_only=selected_only,
        max_articles=max_articles,
    )

    target_images_dir = images_dir or output_markdown_path.parent / "article-images"
    illustrated_payload = _crop_picture_regions(
        matched_payload,
        source_pdf=source_pdf,
        images_dir=target_images_dir,
        render_scale=render_scale,
        allowed_article_indexes=set(selected_indexes),
    )

    markdown_text, summary = render_illustrated_reading_markdown(
        illustrated_payload,
        selected_only=selected_only,
        max_articles=max_articles,
    )
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.write_text(markdown_text, encoding="utf-8")

    if output_json_path is not None:
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(
            json.dumps(illustrated_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "markdown_path": str(output_markdown_path),
        "json_path": str(output_json_path) if output_json_path is not None else None,
        "images_dir": str(target_images_dir),
        "summary": summary,
    }
