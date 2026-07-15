from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


COLUMN_GAP_THRESHOLD = 90.0
TOP_BAND_THRESHOLD = 680.0
HEADER_BAND_THRESHOLD = 560.0


@dataclass(slots=True)
class LayoutBlock:
    label: str
    text: str
    page_no: int
    left: float
    top: float
    bottom: float = 0.0


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


def _extract_text_blocks(structured: dict[str, Any]) -> list[LayoutBlock]:
    blocks: list[LayoutBlock] = []
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

        blocks.append(
            LayoutBlock(
                label=item.get("label", "text"),
                text=item.get("text", ""),
                page_no=int(first_prov.get("page_no", 0)),
                left=float(bbox.get("l", 0.0)),
                top=float(bbox.get("t", 0.0)),
                bottom=float(bbox.get("b", 0.0)),
            )
        )

    return blocks


def _cluster_columns(blocks: list[LayoutBlock]) -> list[float]:
    if not blocks:
        return []
    sorted_lefts = sorted(block.left for block in blocks)
    clusters: list[list[float]] = [[sorted_lefts[0]]]
    for left in sorted_lefts[1:]:
        if left - clusters[-1][-1] > COLUMN_GAP_THRESHOLD:
            clusters.append([left])
        else:
            clusters[-1].append(left)
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _column_index(block: LayoutBlock, columns: list[float]) -> int:
    if not columns:
        return 0
    return min(range(len(columns)), key=lambda idx: abs(block.left - columns[idx]))


def _normalize_text(text: str) -> str:
    normalized = text.replace("\n", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.replace(" ,", ",").replace(" .", ".")
    normalized = normalized.replace(" '", "'")
    normalized = normalized.strip(" \t\r\n")
    return normalized


def _looks_like_name(text: str) -> bool:
    candidate = text.strip()
    if not candidate or len(candidate) > 40:
        return False
    if any(char.isdigit() for char in candidate):
        return False
    if "@" in candidate:
        return False

    words = [word for word in re.split(r"\s+", candidate) if word]
    if not words or len(words) > 4:
        return False

    for word in words:
        stripped = word.strip(".,'\"")
        if not stripped:
            return False
        if not (stripped.isupper() or stripped.istitle()):
            return False
    return True


def _is_noise_block(block: LayoutBlock) -> bool:
    text = _normalize_text(block.text)
    upper = text.upper()

    if not text:
        return True
    if block.label == "page_footer":
        return True
    if re.fullmatch(r"\d+", text):
        return True
    if "NEWSWEEK.COM" in upper:
        return True
    if re.sub(r"[\s.]", "", upper) == "OCEANOFPDFCOM":
        return True
    if "OCEANOFPDF" in re.sub(r"[\s.]", "", upper):
        return True
    if "FOLLOW HIM ON" in upper or "EMAIL HIM" in upper or "@NEWSWEEK.COM" in upper:
        return True
    if "GETTY IMAGES" in upper or "TRIBUNE/GETTY" in upper:
        return True
    if block.label == "list_item" and " is " in text.lower() and "newsweek" in text.lower():
        return True
    if block.label == "section_header" and block.top >= TOP_BAND_THRESHOLD and len(text) <= 20:
        return True
    if re.fullmatch(r"[A-Z ]{2,12}", text) and " " in text and "." not in text:
        return True
    return False


def _format_block(block: LayoutBlock) -> str:
    text = _normalize_text(block.text)
    if block.label == "section_header":
        return f"## {text}"
    if block.label == "caption":
        return f"> {text}"
    return text


def _is_kicker(block: LayoutBlock) -> bool:
    text = _normalize_text(block.text)
    if block.label != "section_header" and block.label != "text":
        return False
    if len(text) > 30:
        return False
    return text.isupper() or "." in text


def _belongs_to_header_band(block: LayoutBlock) -> bool:
    if block.top < HEADER_BAND_THRESHOLD:
        return False
    if block.label in {"section_header", "caption"}:
        return True
    return len(_normalize_text(block.text)) <= 60


def _repair_bylines(blocks: list[LayoutBlock]) -> list[LayoutBlock]:
    repaired: list[LayoutBlock] = []
    pending_names: list[str] = []
    pending_anchor: LayoutBlock | None = None

    def flush_pending() -> None:
        nonlocal pending_names, pending_anchor
        if pending_names and pending_anchor is not None:
            repaired.append(
                LayoutBlock(
                    label="text",
                    text=f"By {' '.join(name.title() for name in pending_names)}",
                    page_no=pending_anchor.page_no,
                    left=pending_anchor.left,
                    top=pending_anchor.top,
                )
            )
        pending_names = []
        pending_anchor = None

    for block in blocks:
        text = _normalize_text(block.text)
        if not text:
            continue

        if text.lower() == "by":
            pending_anchor = pending_anchor or block
            continue

        if text.lower().endswith(" by") and len(text) > 4:
            block = LayoutBlock(
                label=block.label,
                text=text[:-3].rstrip(),
                page_no=block.page_no,
                left=block.left,
                top=block.top,
            )
            repaired.append(block)
            pending_anchor = block
            continue

        if pending_anchor is not None and _looks_like_name(text):
            pending_names.append(text)
            continue

        flush_pending()
        repaired.append(block)

    flush_pending()
    return repaired


def reconstruct_markdown(structured: dict[str, Any], fallback_markdown: str) -> str:
    blocks = [block for block in _extract_text_blocks(structured) if not _is_noise_block(block)]
    if not blocks:
        return fallback_markdown

    repaired_blocks = _repair_bylines(blocks)
    ordered: list[LayoutBlock] = []

    pages = sorted({block.page_no for block in repaired_blocks})
    for page_no in pages:
        page_blocks = [block for block in repaired_blocks if block.page_no == page_no]
        header_band = [block for block in page_blocks if _belongs_to_header_band(block)]
        body_blocks = [block for block in page_blocks if not _belongs_to_header_band(block)]
        columns = _cluster_columns(body_blocks)

        page_order = sorted(header_band, key=lambda block: (-block.top, block.left))
        page_order.extend(
            sorted(
                body_blocks,
                key=lambda block: (
                    _column_index(block, columns),
                    -block.top,
                    block.left,
                ),
            )
        )

        adjusted: list[LayoutBlock] = []
        index = 0
        while index < len(page_order):
            current = page_order[index]
            next_block = page_order[index + 1] if index + 1 < len(page_order) else None
            if (
                next_block is not None
                and current.page_no == next_block.page_no
                and current.label == "section_header"
                and next_block.label == "section_header"
                and _is_kicker(next_block)
                and not _is_kicker(current)
                and abs(current.top - next_block.top) <= 40
            ):
                adjusted.append(next_block)
                adjusted.append(current)
                index += 2
                continue

            adjusted.append(current)
            index += 1

        ordered.extend(adjusted)

    lines: list[str] = []
    previous_page = None
    for block in ordered:
        if previous_page is not None and block.page_no != previous_page:
            lines.append("")
        lines.append(_format_block(block))
        lines.append("")
        previous_page = block.page_no

    reconstructed = "\n".join(lines)
    reconstructed = re.sub(r"\n{3,}", "\n\n", reconstructed).strip()
    return reconstructed + "\n"
