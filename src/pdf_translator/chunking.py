from __future__ import annotations

import re

from pdf_translator.models import TranslationChunk


def _split_oversized_block(block: str, max_chars: int) -> list[str]:
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


def split_markdown_into_chunks(markdown: str, max_chars: int) -> list[TranslationChunk]:
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

    chunks: list[TranslationChunk] = []
    chunk_parts: list[str] = []
    chunk_size = 0
    index = 0

    for block in blocks:
        block_size = len(block) + 2
        if chunk_parts and chunk_size + block_size > max_chars:
            chunks.append(TranslationChunk(index=index, markdown="\n\n".join(chunk_parts)))
            index += 1
            chunk_parts = [block]
            chunk_size = block_size
            continue

        if not chunk_parts and block_size > max_chars:
            for part in _split_oversized_block(block, max_chars):
                chunks.append(TranslationChunk(index=index, markdown=part))
                index += 1
            chunk_parts = []
            chunk_size = 0
            continue

        chunk_parts.append(block)
        chunk_size += block_size

    if chunk_parts:
        chunks.append(TranslationChunk(index=index, markdown="\n\n".join(chunk_parts)))

    return chunks
