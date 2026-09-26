"""Shared EPUB print-page anchor patterns (page_1 and page1 styles)."""

from __future__ import annotations

import re

# Match self-closing or paired tags with id="page_1" / id="page1" / id="page_i".
EPUB_PAGE_ANCHOR_RE = re.compile(
    rb"""<(?P<tag>[a-zA-Z][\w:.-]*)\b(?=[^>]*\bid=["'](?P<anchor>page_?(?P<label>[0-9]+|[ivxlcdm]+))["'])(?![^>]*\bhref=)[^>]*>""",
    re.IGNORECASE,
)
EPUB_PAGE_MARKER_TAG_RE = re.compile(
    rb"""<(?P<tag>[a-zA-Z][\w:.-]*)\b(?=[^>]*\bid=["'](?P<anchor>page_?(?:[0-9]+|[ivxlcdm]+))["'])(?![^>]*\bhref=)[^>]*(?:/>\s*|>\s*</(?P=tag)\s*>)""",
    re.IGNORECASE,
)


def page_label_from_anchor(anchor_id: str) -> str:
    if anchor_id.startswith("page_"):
        return anchor_id[5:]
    if anchor_id.lower().startswith("page"):
        return anchor_id[4:]
    return ""


def _body_content_start(xhtml: bytes) -> int:
    match = re.search(rb"<\s*body\b[^>]*>", xhtml, re.IGNORECASE)
    if not match:
        return 0
    return match.end()


def _body_content_end(xhtml: bytes) -> int:
    match = re.search(rb"</\s*body\s*>", xhtml, re.IGNORECASE)
    if not match:
        return len(xhtml)
    return match.start()


def fragment_has_visible_text(fragment: bytes) -> bool:
    text = re.sub(rb"<[^>]+>", b" ", fragment)
    text = re.sub(rb"&(?:nbsp|#160|#xa0);", b" ", text, flags=re.IGNORECASE)
    text = re.sub(rb"\s+", b"", text)
    return bool(text)


def iter_xhtml_page_spans(xhtml: bytes) -> list[tuple[str, int, int]]:
    """Split one spine document into preview pages.

    Print-page anchors often sit mid-paragraph. Text before the first anchor is
    still part of the reading order, so it stays as its own leading page instead
    of being dropped. ``anchor_id`` is empty for that leading page and for a
    document that has no print anchors at all.
    """

    anchors = list(EPUB_PAGE_ANCHOR_RE.finditer(xhtml))
    if not anchors:
        return [("", 0, len(xhtml))]

    spans: list[tuple[str, int, int]] = []
    first_start = anchors[0].start()
    body_start = _body_content_start(xhtml)
    if fragment_has_visible_text(xhtml[body_start:first_start]):
        spans.append(("", body_start, first_start))
    body_end = _body_content_end(xhtml)
    for index, match in enumerate(anchors):
        start = match.start()
        end = anchors[index + 1].start() if index + 1 < len(anchors) else body_end
        anchor_id = match.group("anchor").decode("utf-8", "replace")
        spans.append((anchor_id, start, end))
    return spans
