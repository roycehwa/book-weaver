from __future__ import annotations

from pathlib import Path

from pdf_translator.knowledge import emit_mindmap_mermaid_from_book, emit_wiki_outline_from_book


def test_emit_wiki_outline_writes_index_and_chapter_stubs(tmp_path: Path) -> None:
    book = {
        "chapters": [
            {"index": 1, "title": "Alpha", "markdown": "x"},
            {"index": 2, "title": "Beta", "markdown": "y"},
        ]
    }
    out = tmp_path / "wiki"
    emit_wiki_outline_from_book(book, out)
    assert (out / "index.md").is_file()
    assert (out / "001-alpha.md").is_file()
    assert "Alpha" in (out / "001-alpha.md").read_text(encoding="utf-8")


def test_emit_mindmap_mermaid_writes_fenced_block(tmp_path: Path) -> None:
    book = {"chapters": [{"index": 1, "title": "Only", "markdown": "m"}]}
    p = tmp_path / "mm.md"
    emit_mindmap_mermaid_from_book(book, p)
    text = p.read_text(encoding="utf-8")
    assert "```mermaid" in text
    assert "flowchart TB" in text
    assert "Only" in text
