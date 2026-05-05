from __future__ import annotations


def render_book_markdown(book: dict, *, include_trace: bool = False) -> str:
    key = "trace_markdown" if include_trace else "full_markdown"
    return str(book.get(key) or "")


def render_translation_input_markdown(book: dict) -> str:
    return str(book.get("full_markdown") or "")
