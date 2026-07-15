from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BOOKWEAVER_FONT_FAMILY = "BookWeaver Text"


@dataclass(frozen=True)
class EpubEmbeddedFont:
    family: str
    source_path: Path
    epub_href: str
    media_type: str


def normalize_epub_language(language: str | None) -> str:
    if not language or not str(language).strip():
        return "en"
    normalized = str(language).strip().lower().replace("_", "-")
    base = normalized.split("-", 1)[0]
    aliases = {
        "english": "en",
        "chinese": "zh",
        "mandarin": "zh",
    }
    return aliases.get(base, base)


def is_cjk_language(language: str | None) -> bool:
    base = normalize_epub_language(language)
    return base in {"zh", "ja", "ko"}


def resolve_epub_content_language(
    *,
    source_language: str | None,
    target_language: str | None,
    content_is_translated: bool,
) -> str:
    """Pick one language for EPUB metadata and typography."""

    source = normalize_epub_language(source_language)
    target = normalize_epub_language(target_language)
    if content_is_translated:
        return target
    return source or target or "en"


def _first_existing_path(candidates: list[str | None]) -> Path | None:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return path.resolve()
    return None


def _font_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".otf":
        return "font/otf"
    if suffix == ".woff":
        return "font/woff"
    if suffix == ".woff2":
        return "font/woff2"
    return "font/ttf"


def resolve_embedded_font(language: str | None) -> EpubEmbeddedFont | None:
    """Embed one reading font when available so readers do not pick randomly."""

    configured = os.environ.get("BOOKWEAVER_EPUB_FONT")
    if is_cjk_language(language):
        font_path = _first_existing_path(
            [
                configured,
                os.environ.get("BOOKWEAVER_EPUB_FONT_CJK"),
                os.environ.get("PDF_TRANSLATOR_CJK_FONT"),
                "/System/Library/Fonts/Supplemental/Songti.ttc",
                "/System/Library/Fonts/STHeiti Light.ttc",
                "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
                "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
            ]
        )
        epub_name = "bookweaver-text.ttc" if font_path and font_path.suffix.lower() == ".ttc" else "bookweaver-text.ttf"
    else:
        font_path = _first_existing_path(
            [
                configured,
                os.environ.get("BOOKWEAVER_EPUB_FONT_LATIN"),
                "/System/Library/Fonts/Supplemental/Georgia.ttf",
                "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
                "C:/Windows/Fonts/georgia.ttf",
            ]
        )
        epub_name = "bookweaver-text.ttf"

    if font_path is None:
        return None
    return EpubEmbeddedFont(
        family=BOOKWEAVER_FONT_FAMILY,
        source_path=font_path,
        epub_href=f"fonts/{epub_name}",
        media_type=_font_media_type(font_path),
    )


def build_epub_css(*, language: str | None, embedded_font: EpubEmbeddedFont | None) -> str:
    """Build deterministic EPUB CSS for the book's content language."""

    if embedded_font is not None:
        font_face = (
            "@font-face {\n"
            f'  font-family: "{embedded_font.family}";\n'
            f'  src: url("../{embedded_font.epub_href}");\n'
            "  font-weight: normal;\n"
            "  font-style: normal;\n"
            "}\n"
        )
        primary_stack = f'"{embedded_font.family}", serif'
    elif is_cjk_language(language):
        font_face = ""
        primary_stack = '"Source Han Serif SC", "Noto Serif SC", "Songti SC", serif'
    else:
        font_face = ""
        primary_stack = 'Georgia, "Times New Roman", "Palatino Linotype", serif'

    return f"""{font_face}@page {{
  margin: 1.4rem 1.2rem;
}}
html {{
  font-family: {primary_stack};
}}
body {{
  font-family: inherit;
  line-height: 1.82;
  margin: 0;
  padding: 1.8rem 1.45rem 2.4rem;
  color: #222831;
  background: #fffdf8;
  font-size: 1.03rem;
}}
body, p, li, td, th, div, span, em, strong, i, b, a, blockquote, figcaption {{
  font-family: inherit;
}}
body.bookweaver-chapter-start {{
  page-break-before: always;
  break-before: page;
}}
body.bookweaver-chapter-first {{
  page-break-before: auto;
  break-before: auto;
}}
h1, h2, h3, h4, h5, h6 {{
  font-family: inherit;
  font-weight: 600;
  color: #111827;
  line-height: 1.28;
  margin: 2.2em 0 1em;
  page-break-after: avoid;
  break-after: avoid;
}}
h1 {{
  font-size: 1.85rem;
  margin-top: 0.4em;
  padding-bottom: 0.45em;
  border-bottom: 1px solid #d8d2c4;
}}
h2 {{
  font-size: 1.35rem;
}}
h3 {{
  font-size: 1.16rem;
}}
p {{
  margin: 0 0 1.05em;
  text-align: start;
}}
img {{
  display: block;
  max-width: 100%;
  height: auto;
  margin: 1.8rem auto 1.1rem;
  page-break-inside: avoid;
  break-inside: avoid;
}}
figure {{
  margin: 1.8rem 0;
  page-break-inside: avoid;
  break-inside: avoid;
}}
blockquote {{
  margin: 1.1rem 0 1.35rem;
  padding: 0.15rem 0 0.15rem 1rem;
  border-left: 0.18rem solid #b6ad9c;
  color: #4b5563;
}}
table {{
  border-collapse: collapse;
  width: 100%;
  margin: 1.4rem 0;
  font-size: 0.92rem;
  page-break-inside: avoid;
  break-inside: avoid;
}}
td, th {{
  border: 1px solid #d1d5db;
  padding: 0.5rem;
  vertical-align: top;
  word-break: break-word;
}}
table.worksheet-table {{
  display: block;
  overflow-x: auto;
  font-size: 0.84rem;
  line-height: 1.45;
}}
table.worksheet-table td,
table.worksheet-table th {{
  min-width: 7rem;
}}
li {{
  margin: 0.25rem 0 0.55rem;
}}
.chapter-notes {{
  margin-top: 2.4rem;
  padding-top: 1rem;
  border-top: 1px solid #d8d2c4;
  color: #4b5563;
  font-size: 0.86rem;
  line-height: 1.55;
}}
.chapter-notes h2 {{
  margin: 0 0 0.85rem;
  font-size: 1rem;
  letter-spacing: 0.04em;
}}
.chapter-notes p {{
  margin: 0 0 0.55em;
}}
.preserved-apparatus {{
  margin-top: 1.2rem;
  padding: 1rem 1.05rem;
  border: 1px solid #e3ded2;
  background: #fbf7ec;
  color: #3f4652;
  font-size: 0.88rem;
  line-height: 1.5;
}}
.preserved-apparatus h1,
.preserved-apparatus h2,
.preserved-apparatus h3 {{
  page-break-before: auto;
  break-before: auto;
  margin-top: 0.6rem;
}}
.preserved-apparatus p,
.preserved-apparatus li {{
  margin-bottom: 0.5rem;
}}
""".strip() + "\n"
