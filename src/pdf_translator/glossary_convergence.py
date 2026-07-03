from __future__ import annotations

import re


_GLOSSARY_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:\*\*)?"
    r"(?:MANDATORY[ \t]+GLOSSARY(?:[^\n]*)?|术语对照(?:[^\n]*)?|强制术语表(?:[^\n]*)?)"
    r"(?:\*\*)?[：:]?[ \t]*$"
)
_GLOSSARY_MAPPING_RE = re.compile(r"(?m)^[ \t]*[-*][ \t]+.+?(?:=>|→).+$")


def sanitize_translation_output(text: str) -> str:
    """Remove a model-echoed glossary control appendix.

    A heading alone is not enough evidence because scholarly prose may discuss
    terminology. The suffix is removed only when it also contains source-target
    mapping lines, which are control data and never valid translated content.
    """

    for match in _GLOSSARY_HEADING_RE.finditer(text):
        suffix = text[match.start() :]
        if _GLOSSARY_MAPPING_RE.search(suffix):
            return text[: match.start()].rstrip()
    return text.strip()
