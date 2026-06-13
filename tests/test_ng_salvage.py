from __future__ import annotations

from pathlib import Path

from pdf_translator.ng_salvage import first_missing_chunk_index, iter_global_chunks


def test_ng_salvage_chunk_indexes_match_translation_pipeline() -> None:
    book = {
        "chapters": [
            {
                "index": 1,
                "title": "Chapter 1",
                "markdown": "First paragraph.\n\nSecond paragraph.",
                "translate": True,
            }
        ]
    }

    chunks = iter_global_chunks(book, max_chunk_chars=20)

    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_first_missing_chunk_index_starts_at_zero(tmp_path: Path) -> None:
    (tmp_path / "book.json").write_text(
        """
        {
          "chapters": [
            {
              "index": 1,
              "title": "Chapter 1",
              "markdown": "A short chapter.",
              "translate": true
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    (tmp_path / "translation-cache").mkdir()

    assert first_missing_chunk_index(tmp_path, max_chunk_chars=9000) == 0
