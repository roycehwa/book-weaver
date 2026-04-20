from pdf_translator.chunking import split_markdown_into_chunks


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
