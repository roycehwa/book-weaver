from __future__ import annotations

import re

from pdf_translator.models import TranslationChunk
from bs4 import BeautifulSoup
from markdown import markdown as render_markdown


def markdown_block_structure(text: str) -> tuple[str, ...]:
    soup = BeautifulSoup(render_markdown(text, extensions=["tables", "fenced_code"]), "html.parser")
    return tuple(node.name for node in soup.children if getattr(node, "name", None))


def untranslated_prose_blocks(source: str, translated: str) -> list[int]:
    """Conservative exact-copy detection, independent of whole-chunk CJK ratio.

    Compare aligned prose paragraphs only; do not flag code, tables, titles or
    citation-only lists. This detects copied prose, not all semantic omissions.
    """
    def paragraphs(text: str) -> list[str]:
        soup = BeautifulSoup(render_markdown(text, extensions=["tables", "fenced_code"]), "html.parser")
        return [re.sub(r"\s+", " ", p.get_text(" ", strip=True)) for p in soup.find_all("p")
                if not p.find_parent(["li", "td", "th", "pre"])]
    before, after = paragraphs(source), paragraphs(translated)
    if len(before) != len(after):
        return []  # The structure contract handles unequal block counts.
    return [index for index, (a, b) in enumerate(zip(before, after))
            if a == b and len(re.findall(r"\b[a-z]+\b", a)) >= 12
            and len(re.findall(r"[A-Za-z]", a)) >= 80
            and not re.search(r"[\u4e00-\u9fff]", a)]


def join_chunk_texts(texts: list[str], separators: list[str]) -> str:
    if len(texts) != len(separators):
        raise ValueError("Chunk text and boundary counts differ")
    return "".join((separator if index else "") + text.strip()
                   for index, (text, separator) in enumerate(zip(texts, separators)))


def _is_atomic_markdown_block(block: str) -> bool:
    stripped = block.strip()
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if stripped.startswith("```") and stripped.endswith("```"):
        return True
    if re.fullmatch(r"!\[[^\]]*]\([^)]+\)", stripped, re.DOTALL):
        return True
    if len(lines) >= 2 and all(line.startswith("|") and line.endswith("|") for line in lines):
        return True
    return False


def _split_oversized_block(block: str, max_chars: int) -> list[str]:
    if _is_atomic_markdown_block(block):
        return [block.strip()]
    sentences = [
        match.group(0).strip()
        for match in re.finditer(
            r".+?(?:[.!?。！？]+[\"'”’)]*(?:\s+|$)|$)",
            block,
            re.DOTALL,
        )
        if match.group(0).strip()
    ] or [block.strip()]
    if len(sentences) == 1 and "\n" in block:
        sentences = [line.strip() for line in block.splitlines() if line.strip()]
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def markdown_source_blocks(markdown: str) -> list[str]:
    """Keep source paragraph boundaries and fenced code intact."""
    lines = markdown.splitlines()
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False

    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            current.append(line)
            continue

        if not in_fence and line.strip() == "":
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue

        current.append(line)

    if current:
        blocks.append("\n".join(current).strip())

    return blocks


def split_markdown_into_chunks(markdown: str, max_chars: int) -> list[TranslationChunk]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    blocks = markdown_source_blocks(markdown)

    chunks: list[TranslationChunk] = []
    chunk_parts: list[str] = []
    chunk_size = 0
    index = 0

    for block in blocks:
        block_size = len(block) + 2
        if chunk_parts and chunk_size + block_size > max_chars:
            chunks.append(TranslationChunk(index=index, markdown="\n\n".join(chunk_parts)))
            index += 1
            chunk_parts = []
            chunk_size = 0

        if not chunk_parts and block_size > max_chars:
            for part_index, part in enumerate(_split_oversized_block(block, max_chars)):
                chunks.append(TranslationChunk(index=index, markdown=part,
                    separator_before="\n\n" if part_index == 0 else " "))
                index += 1
            chunk_parts = []
            chunk_size = 0
            continue

        chunk_parts.append(block)
        chunk_size += block_size

    if chunk_parts:
        chunks.append(TranslationChunk(index=index, markdown="\n\n".join(chunk_parts)))

    return chunks
