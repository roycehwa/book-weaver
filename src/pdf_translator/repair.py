"""Polish-time repair for structure, images, and in-book links.

After translation, the model may correct headings, lists, tables, images, and
in-book links. Prose stays put. Review shows that result. Export packages the
confirmed text and does not call the model. File paths never appear in the
text shown to the reader.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
from urllib.parse import unquote


class RepairLane(str, Enum):
    DETERMINISTIC = "deterministic"
    BOUNDED_MODEL = "bounded_model"
    USER_DECISION = "user_decision"


@dataclass(frozen=True)
class RepairCase:
    code: str
    lane: RepairLane
    user_summary: str
    question: str = ""
    choices: tuple[str, ...] = ()

    def public(self) -> dict[str, object]:
        return {
            "code": self.code,
            "lane": self.lane.value,
            "user_summary": self.user_summary,
            "question": self.question,
            "choices": list(self.choices),
        }


def missing_required_images(count: int) -> RepairCase:
    return RepairCase(
        code="missing_required_images",
        lane=RepairLane.DETERMINISTIC,
        user_summary=f"有 {count} 张图片没能放进导出文件。应用已跳过它们，不需要你连接图片。",
    )


def classify_export_message(message: str) -> RepairCase:
    text = message or ""
    if "必需图片无法读取" in text or "没有放进导出文件" in text or "没能放进导出文件" in text:
        count = _leading_count(text)
        return missing_required_images(count if count is not None else 0)
    if "链接目标发生变化" in text or "书内链接" in text:
        return RepairCase(
            code="navigation_link_drift",
            lane=RepairLane.DETERMINISTIC,
            user_summary="书内链接和原书有些不一样。这些差异已经记下，不需要你修改，也不影响导出。",
        )
    if "片段尚无输出" in text or "翻译失败占位" in text:
        return RepairCase(
            code="segment_translation_gap",
            lane=RepairLane.BOUNDED_MODEL,
            user_summary="有几段译文还不完整。可以在审阅里补上译文。",
        )
    return RepairCase(
        code="unclassified_export_block",
        lane=RepairLane.DETERMINISTIC,
        user_summary="应用已记下这个结构差异并继续处理，不需要你改结构。",
    )


STRUCTURE_MODEL_CODES = {
    "markdown_block_structure_loss",
    "markdown_block_structure_regression",
    "line_count_regression",
    "final_newline_regression",
    "markdown_link_structure_loss",
    "markdown_prose_block_shape_changed",
    "image_marker_regression",
    "link_structure_regression",
    "footnote_marker_regression",
    "footnotes_regression",
    "html_tags_regression",
    "inline_code_regression",
    "preserve_markers_regression",
    "emails_regression",
    "invented_link_targets",
    "lost_link_targets",
    "urls_regression",
    "html_anchors_regression",
    "link_destinations_regression",
    "auto_links_regression",
    "polished_urls_regression",
    "polished_html_anchors_regression",
    "polished_auto_links_regression",
    "polished_inline_code_regression",
    "polished_emails_regression",
    "polished_preserve_markers_regression",
}


_STRUCTURE_LINE_RE = re.compile(
    r"^(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|\|)|!\[[^\]]*\]\(|\[[^\]]+\]\("
)


def polish_wrapped_lines(markdown: str, *, complete) -> str:
    """Join layout-wrapped prose lines. Wording and real paragraph breaks stay."""

    lines = markdown.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or _STRUCTURE_LINE_RE.search(line):
            output.append(line)
            index += 1
            continue
        group = [line]
        index += 1
        while index < len(lines) and lines[index].strip() and not _STRUCTURE_LINE_RE.search(lines[index]):
            group.append(lines[index])
            index += 1
        if len(group) == 1:
            output.extend(group)
            continue
        fragment = "\n".join(group)
        try:
            reply = complete(
                "把这些被版面拆开的行接成一段。不要改用词。不要合并下一段。只返回这一段。",
                fragment,
            ).strip()
        except Exception:
            output.extend(group)
            continue
        if re.sub(r"\s+", "", reply) != re.sub(r"\s+", "", fragment):
            output.extend(group)
            continue
        output.append(reply)
    return "\n".join(output)


def polish_structure_markdown(
    markdown: str,
    *,
    source_markdown: str,
    complete,
    available_image_names: list[str] | None = None,
) -> str:
    """Repair headings, lists, tables, images, and in-book links after translation.

    Prose lines stay in place. A model failure keeps the original line.
    """

    lines = markdown.splitlines()
    indexes = [index for index, line in enumerate(lines) if _STRUCTURE_LINE_RE.search(line)]
    source_lines = [line for line in source_markdown.splitlines() if _STRUCTURE_LINE_RE.search(line)]
    for start in range(0, len(indexes), 20):
        batch = indexes[start:start + 20]
        fragment = "\n".join(lines[index] for index in batch)
        source_batch = "\n".join(source_lines[start:start + len(batch)])
        try:
            reply = complete(
                "只修正这些标题、列表、表格、图片和书内链接。不要重写正文。按原行数返回。",
                f"原文结构：\n{source_batch}\n\n译文结构：\n{fragment}",
            ).strip()
        except Exception:
            continue
        reply_lines = reply.splitlines()
        if len(reply_lines) != len(batch):
            continue
        for index, replacement in zip(batch, reply_lines):
            lines[index] = replacement
    updated = "\n".join(lines)
    names = [name for name in (available_image_names or []) if name]
    if names:
        broken = _unresolved_image_srcs(updated, set(names))
        if broken:
            updated = repair_image_references(
                updated,
                broken_srcs=broken,
                available_names=names,
                complete=complete,
            )
    return updated


def _unresolved_image_srcs(markdown: str, available: set[str]) -> list[str]:
    suffixes = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff", ".avif"}
    broken: list[str] = []
    for match in re.finditer(r"!\[[^\]]*\]\(([^)\n]+)\)", markdown):
        dest = unquote(match.group(1).strip().strip("<>")).split()[0]
        name = Path(dest).name
        if Path(name).suffix.lower() in suffixes and name not in available and dest not in broken:
            broken.append(dest)
    return broken


def repair_structure_markdown(markdown: str, *, problem: str, complete) -> str:
    """Ask the model to repair structure lines only. A model failure keeps the chapter."""

    lines = markdown.splitlines()
    indexes = [index for index, line in enumerate(lines) if _STRUCTURE_LINE_RE.search(line)]
    if not indexes:
        return markdown
    for start in range(0, len(indexes), 20):
        batch = indexes[start:start + 20]
        fragment = "\n".join(lines[index] for index in batch)
        try:
            reply = complete(
                "只修正这些标题、列表、表格、引用、代码、图片和书内链接。不要重写正文。按原行数返回。",
                f"问题：{problem}\n\n{fragment}",
            ).strip()
        except Exception:
            continue
        reply_lines = reply.splitlines()
        if len(reply_lines) != len(batch):
            continue
        for index, replacement in zip(batch, reply_lines):
            lines[index] = replacement
    return "\n".join(lines)


def repair_image_references(
    markdown: str,
    *,
    broken_srcs: list[str],
    available_names: list[str],
    complete,
    attempts: int = 2,
) -> str:
    """Ask the model to reconnect image references. Rules do not guess the path."""

    available = set(available_names)
    current = markdown
    broken_lines = [
        line
        for line in current.splitlines()
        if any(src and src in line for src in broken_srcs)
    ]
    for line in broken_lines:
        replacement = line
        for _attempt in range(attempts):
            reply = complete(
                "只修正图片引用，让它指向给出的文件名。不要翻译正文。只返回这一行。",
                f"对不上的引用所在行：{line}\n可用文件名：{', '.join(available_names)}",
            ).strip()
            if _image_line_uses_available_file(reply, available):
                replacement = reply
                break
        if replacement != line:
            current = current.replace(line, replacement, 1)
    return current


def _image_line_uses_available_file(line: str, available: set[str]) -> bool:
    destinations = re.findall(r"!\[[^\]]*\]\(([^)\n]+)\)", line)
    if not destinations:
        return False
    return all(Path(unquote(dest.strip().strip("<>")).split()[0]).name in available for dest in destinations)


def record_system_repair(run_dir: Path, case: RepairCase) -> None:
    """Append a path-free note. This does not ask the reader to do anything."""

    path = Path(run_dir) / "repair-log.json"
    payload: dict[str, object] = {"items": []}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("items"), list):
            payload = loaded
    items = payload.setdefault("items", [])
    if isinstance(items, list):
        items.append(
            {
                "code": case.code,
                "lane": case.lane.value,
                "user_summary": case.user_summary,
            }
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _leading_count(text: str) -> int | None:
    digits = ""
    seen_digit = False
    for char in text:
        if char.isdigit():
            digits += char
            seen_digit = True
        elif seen_digit:
            break
    if not digits:
        return None
    return int(digits)
