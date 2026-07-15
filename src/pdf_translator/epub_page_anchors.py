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
