from __future__ import annotations

from pdf_translator.models import TranslationChunk


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
            lines_in_block = block.splitlines()
            running: list[str] = []
            running_size = 0
            for line in lines_in_block:
                line_size = len(line) + 1
                if running and running_size + line_size > max_chars:
                    chunks.append(
                        TranslationChunk(index=index, markdown="\n".join(running).strip())
                    )
                    index += 1
                    running = [line]
                    running_size = line_size
                else:
                    running.append(line)
                    running_size += line_size
            if running:
                chunks.append(TranslationChunk(index=index, markdown="\n".join(running).strip()))
                index += 1
            chunk_parts = []
            chunk_size = 0
            continue

        chunk_parts.append(block)
        chunk_size += block_size

    if chunk_parts:
        chunks.append(TranslationChunk(index=index, markdown="\n\n".join(chunk_parts)))

    return chunks
