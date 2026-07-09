"""Classify chapters and blocks for the translation policy.

The translator skips entire chapters whose ``kind`` is in
:data:`NON_TRANSLATABLE_CHAPTER_KINDS` and individual blocks whose
``kind`` is in :data:`NON_TRANSLATABLE_BLOCK_KINDS`.

Chapter classification is heuristic. It uses three signals already
present in the BookIR:

1. The chapter's existing ``preserve_original`` / ``resource_only`` flags
   set during rebuild.
2. The chapter title (matched against :data:`TITLE_HINTS`).
3. The aggregated page ``kind`` counts from ``pages[*].page_kind``.

This module is intentionally dependency-free so it can be imported from
tests without spinning up the full pipeline.
"""

from __future__ import annotations

from typing import Any, Iterable

# ----------------------------- enums --------------------------------------

#: Chapter kinds. ``narrative`` is the default for prose body chapters.
CHAPTER_KINDS = (
    "cover",
    "toc",
    "front_matter",
    "narrative",
    "apparatus",
    "bibliography",
    "index",
    "appendix",
)

#: Block kinds. ``text`` is the default. ``table`` and ``figure`` are
#: rendered as image references; their captions (``caption``) are
#: optional-translate.
BLOCK_KINDS = (
    "text",
    "table",
    "figure",
    "caption",
    "note",
    "heading",
    "list",
)

#: Chapter kinds that should not be sent to the translator at all.
NON_TRANSLATABLE_CHAPTER_KINDS: frozenset[str] = frozenset({
    "cover",
    "toc",
    "apparatus",
    "bibliography",
    "index",
    "appendix",
    # ``front_matter`` is left translatable by default; many academic
    # books have substantive forewords. Override per chapter if not.
})

#: Block kinds that should not be sent to the translator even when
#: the surrounding chapter is translatable (e.g. a table that lives
#: inside a narrative chapter).
NON_TRANSLATABLE_BLOCK_KINDS: frozenset[str] = frozenset({
    "table",
    "figure",
})

#: Title substrings (lower-cased) that map a chapter to a non-translatable
#: kind. Order matters: more specific entries come first.
TITLE_HINTS: tuple[tuple[str, str], ...] = (
    # cover / front matter
    ("cover", "cover"),
    ("half title", "cover"),
    ("title page", "cover"),
    ("copyright", "front_matter"),
    ("imprint", "front_matter"),
    ("dedication", "front_matter"),
    ("epigraph", "front_matter"),
    ("preface", "front_matter"),
    ("foreword", "front_matter"),
    ("acknowledg", "front_matter"),
    ("introduction", "front_matter"),
    ("list of contributors", "front_matter"),
    ("list of abbreviations", "front_matter"),
    ("editorial board", "front_matter"),
    ("note on the text", "apparatus"),
    ("translator's note", "apparatus"),
    # apparatus
    ("notes on transcription", "apparatus"),
    ("notes on dates", "apparatus"),
    ("notes on the text", "apparatus"),
    ("abbreviations", "apparatus"),
    ("glossary", "apparatus"),
    ("editorial note", "apparatus"),
    # toc
    ("table of contents", "toc"),
    ("contents", "toc"),
    ("list of figures", "toc"),
    ("list of tables", "toc"),
    ("list of maps", "toc"),
    ("list of illustrations", "toc"),
    # bibliography
    ("bibliography", "bibliography"),
    ("references", "bibliography"),
    ("works cited", "bibliography"),
    ("further reading", "bibliography"),
    # index
    ("index", "index"),
    # appendix
    ("appendix", "appendix"),
    ("annex", "appendix"),
)


# ----------------------------- chapter classification ----------------------


def _title_kind(title: str) -> str | None:
    if not title:
        return None
    t = title.strip().lower()
    for needle, kind in TITLE_HINTS:
        if needle in t:
            return kind
    return None


def _majority_page_kind(pages: Iterable[dict[str, Any]], chapter_pages: list[int]) -> str | None:
    """Look at the kinds of pages that belong to this chapter and pick
    the majority. Returns ``None`` when there are no pages.
    """
    counts: dict[str, int] = {}
    for page in pages:
        try:
            page_no = int(page.get("page_no") or 0)
        except (TypeError, ValueError):
            continue
        if chapter_pages and page_no not in chapter_pages:
            continue
        kind = str(page.get("page_kind") or "")
        if not kind:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


_PAGE_KIND_TO_CHAPTER_KIND = {
    "toc": "toc",
    "references": "bibliography",
    "index": "index",
    "notes_heavy": "apparatus",
    "back_matter": "apparatus",
    "front_matter": "front_matter",
    "table_heavy": "narrative",  # tables in body, don't change chapter kind
    "body": "narrative",
    "visual_only": "cover",
}


def classify_chapter(
    chapter: dict[str, Any],
    *,
    pages: list[dict[str, Any]] | None = None,
) -> str:
    """Decide the chapter's kind.

    Resolution order:

    1. If ``chapter['kind']`` is already set explicitly, trust it.
    2. If ``preserve_original`` and ``resource_only`` are both true, the
       chapter is preserved as-is. Map common apparatus page kinds.
    3. If the chapter's title matches :data:`TITLE_HINTS`, use that.
    4. Otherwise, fall back to the majority page kind from ``pages``.
    5. Final default: ``narrative``.
    """
    explicit = chapter.get("kind")
    if isinstance(explicit, str) and explicit in CHAPTER_KINDS:
        return explicit

    title_kind = _title_kind(str(chapter.get("title") or ""))
    preserve = bool(chapter.get("preserve_original"))
    resource_only = bool(chapter.get("resource_only"))
    if preserve and resource_only and title_kind:
        return title_kind
    if preserve and resource_only and not title_kind:
        # generic "preserved" resource only chapter → treat as front matter
        return "front_matter"

    if title_kind:
        return title_kind

    if pages is not None:
        try:
            chapter_pages = [int(p) for p in (chapter.get("source_pages") or [])]
        except (TypeError, ValueError):
            chapter_pages = []
        page_kind = _majority_page_kind(pages, chapter_pages)
        if page_kind and page_kind in _PAGE_KIND_TO_CHAPTER_KIND:
            return _PAGE_KIND_TO_CHAPTER_KIND[page_kind]

    return "narrative"


# ----------------------------- block classification -----------------------


def classify_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate each block with a normalized ``kind`` field if missing.

    The function is non-destructive: existing kinds are preserved if
    they are already in :data:`BLOCK_KINDS`; otherwise the block is
    treated as ``text``.
    """
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("kind") or block.get("type")
        if isinstance(kind, str) and kind in BLOCK_KINDS:
            block["kind"] = kind
        else:
            block["kind"] = "text"
    return blocks


# ----------------------------- translation input filter -------------------


def should_translate_chapter(chapter: dict[str, Any]) -> bool:
    """True if the chapter should be sent to the translator."""
    if not isinstance(chapter, dict):
        return False
    if chapter.get("translate") is False:
        return False
    if bool(chapter.get("preserve_original")) and bool(chapter.get("resource_only")):
        # preserved resource chapters are never translated regardless of kind
        return False
    return classify_chapter(chapter) not in NON_TRANSLATABLE_CHAPTER_KINDS


def should_translate_block(block: dict[str, Any]) -> bool:
    if not isinstance(block, dict):
        return False
    kind = block.get("kind") or "text"
    if kind in NON_TRANSLATABLE_BLOCK_KINDS:
        return False
    return True
