from __future__ import annotations

import re


_GLOSSARY_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:\*\*)?"
    r"(?:MANDATORY[ \t]+GLOSSARY(?:[^\n]*)?|术语对照(?:[^\n]*)?|强制术语表(?:[^\n]*)?|"
    r"必用术语表(?:[^\n]*)?|引用条目(?:[^\n]*)?)"
    r"(?:\*\*)?[：:]?[ \t]*$"
)
_GLOSSARY_MAPPING_RE = re.compile(r"(?m)^[ \t]*[-*][ \t]+.+?(?:=>|→).+$")
_SOURCE_MARKDOWN_WRAPPER_RE = re.compile(
    r"\A\s*<\s*source_markdown\s*>\s*(.*?)\s*</\s*source_markdown\s*>\s*\Z",
    re.IGNORECASE | re.DOTALL,
)


def sanitize_translation_output(text: str) -> str:
    """Remove model-echoed transport controls from translated content.

    The translation prompt encloses input in ``SOURCE_MARKDOWN`` tags. Some
    providers echo that outer wrapper even though they translated its contents.
    Strip it only when it encloses the complete response, so real inline HTML is
    untouched. A glossary heading alone is not enough evidence because scholarly
    prose may discuss terminology; that suffix is removed only when it also
    contains source-target mapping lines.
    """

    wrapper = _SOURCE_MARKDOWN_WRAPPER_RE.fullmatch(text)
    if wrapper:
        text = wrapper.group(1).strip()

    for match in _GLOSSARY_HEADING_RE.finditer(text):
        suffix = text[match.start() :]
        if _GLOSSARY_MAPPING_RE.search(suffix):
            return text[: match.start()].rstrip()
    return text.strip()
