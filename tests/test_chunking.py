from pdf_translator.chunking import split_markdown_into_chunks
from pdf_translator.chunking import join_chunk_texts


def test_review_merge_and_export_preserve_continuation_boundaries():
    from pdf_translator.review import _merge_reading_review_segments, translated_segments_to_chapters
    source = []
    translated = []
    for i, (text, separator) in enumerate([("First.", "\n\n"), ("Continued.", " "), ("Next paragraph.", "\n\n")]):
        base = dict(segment_id=str(i), chapter_id="c1", chapter_index=1, chapter_title="C", block_index=i,
                    separator_before=separator, source_location={}, source_path="test")
        source.append(dict(base, source_text=text))
        translated.append(dict(base, translated_text=text))
    _, merged = _merge_reading_review_segments(source, translated, min_chars=1)
    assert translated_segments_to_chapters(merged)[0]["markdown"] == "First. Continued.\n\nNext paragraph.\n"


def test_transport_boundaries_do_not_create_paragraphs() -> None:
    source = "# Title\n\n" + "One complete sentence. " * 15 + "\n\nA new paragraph."
    chunks = split_markdown_into_chunks(source, 60)
    reconstructed = join_chunk_texts([c.markdown for c in chunks], [c.separator_before for c in chunks])
    assert reconstructed.split("\n\n") == [p.strip() for p in source.split("\n\n")]
    assert all(len(c.markdown) <= 60 for c in chunks)


def test_split_markdown_preserves_code_fence_blocks() -> None:
    markdown = """# Title

Paragraph one.

```python
print("hello")
print("world")
```

Paragraph two.
"""
    chunks = split_markdown_into_chunks(markdown, max_chars=40)

    assert len(chunks) >= 2
    assert any("```python" in chunk.markdown and 'print("world")' in chunk.markdown for chunk in chunks)


def test_split_markdown_splits_large_block_linewise() -> None:
    markdown = "\n".join([f"line {idx}" for idx in range(20)])
    chunks = split_markdown_into_chunks(markdown, max_chars=30)

    assert len(chunks) > 1
    assert all(len(chunk.markdown) <= 30 for chunk in chunks)


def test_split_markdown_splits_oversized_paragraph_at_sentence_boundaries() -> None:
    markdown = (
        "First complete sentence. Second complete sentence. "
        "Third complete sentence. Fourth complete sentence."
    )

    chunks = split_markdown_into_chunks(markdown, max_chars=55)

    assert len(chunks) > 1
    assert all(chunk.markdown.endswith(".") for chunk in chunks)
    assert "".join(chunk.markdown for chunk in chunks).replace(" ", "") == markdown.replace(" ", "")


def test_split_markdown_keeps_oversized_table_atomic() -> None:
    markdown = (
        "| Name | Description |\n"
        "| --- | --- |\n"
        "| Geneva | A deliberately long table cell that exceeds the chunk limit. |\n"
        "| Savoy | Another deliberately long table cell that remains in this table. |"
    )

    chunks = split_markdown_into_chunks(markdown, max_chars=60)

    assert len(chunks) == 1
    assert chunks[0].markdown == markdown
